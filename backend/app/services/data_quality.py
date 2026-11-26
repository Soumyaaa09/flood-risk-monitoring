"""Data Quality and Freshness Validation Engine for FloodWatch.

Strict Domain Rules:
1. Freshness Classification:
   - age <= 120 minutes: FRESH
   - 120 < age <= 360 minutes: DELAYED
   - age > 360 minutes: STALE
   - missing timestamp or missing data: UNAVAILABLE
   - timestamp > 60 seconds in the future: UNAVAILABLE
   - 0 <= future delta <= 60 seconds: permitted clock skew, evaluated as 0 minutes age.
2. Timestamps must be normalized to UTC.
3. Missing rainfall values must NEVER be converted into zero (0.0).
4. Missing data must NEVER be classified as low risk.
5. Freshness is strictly segregated from flood-risk categorization.
6. Validation covers: missing values, negative rainfall, invalid timestamps,
   duplicate observations, unexpected units, incomplete forecast horizons,
   and inconsistent location coordinates.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set, Tuple

from app.core.config import (
    FRESHNESS_CLOCK_SKEW_TOLERANCE_SECONDS,
    OBSERVATION_FRESHNESS_DELAYED_MINUTES,
    OBSERVATION_FRESHNESS_FRESH_MINUTES,
    Settings,
    get_settings,
)
from app.schemas.ingestion import FreshnessState
from app.schemas.monitoring import DataQualityIssue, QualitySeverity, QualityStatus

if TYPE_CHECKING:
    from app.risk_engine.config import RainfallRiskConfig


def normalize_to_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Normalize any datetime to a timezone-aware UTC datetime.
    
    - If dt is naive, assume UTC.
    - If dt has tzinfo, convert to UTC via astimezone.
    - If None, return None.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_utc_timestamp(value: Any) -> Optional[datetime]:
    """Safely parse a datetime or ISO-8601 string into UTC; return None if missing or invalid."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return normalize_to_utc(value)
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return None
        if cleaned.endswith(("Z", "z")):
            cleaned = cleaned[:-1] + "+00:00"
        elif "+" not in cleaned and "T" in cleaned and "-" not in cleaned.split("T", 1)[1]:
            cleaned += "+00:00"
        try:
            return normalize_to_utc(datetime.fromisoformat(cleaned))
        except ValueError:
            return None
    return None


@dataclass(frozen=True)
class FreshnessThresholdPolicy:
    """Centralized threshold policy defining FRESH / DELAYED / STALE age windows in minutes."""

    name: str
    fresh_max_minutes: float
    delayed_max_minutes: float
    clock_skew_tolerance_seconds: float = FRESHNESS_CLOCK_SKEW_TOLERANCE_SECONDS


def get_observation_freshness_policy(
    config: Optional["RainfallRiskConfig"] = None,
) -> FreshnessThresholdPolicy:
    """Return shared policy for physical observation validity timestamps (valid_at)."""
    fresh_max = (
        float(config.delayed_observation_threshold_minutes)
        if config is not None
        else OBSERVATION_FRESHNESS_FRESH_MINUTES
    )
    delayed_max = (
        float(config.stale_observation_threshold_minutes)
        if config is not None
        else OBSERVATION_FRESHNESS_DELAYED_MINUTES
    )
    return FreshnessThresholdPolicy(
        name="observation_valid_at",
        fresh_max_minutes=fresh_max,
        delayed_max_minutes=delayed_max,
        clock_skew_tolerance_seconds=FRESHNESS_CLOCK_SKEW_TOLERANCE_SECONDS,
    )


def get_ingestion_freshness_policy(
    settings: Optional[Settings] = None,
) -> FreshnessThresholdPolicy:
    """Return shared policy for pipeline retrieval timestamps (fetched_at)."""
    cfg = settings or get_settings()
    return FreshnessThresholdPolicy(
        name="ingestion_fetched_at",
        fresh_max_minutes=float(cfg.FRESHNESS_FRESH_MINUTES),
        delayed_max_minutes=float(cfg.FRESHNESS_DELAYED_MINUTES),
        clock_skew_tolerance_seconds=FRESHNESS_CLOCK_SKEW_TOLERANCE_SECONDS,
    )


OBSERVATION_FRESHNESS_POLICY = get_observation_freshness_policy()


class FreshnessEvaluator:
    """Centralized evaluator for observation (valid_at) and ingestion (fetched_at) freshness."""

    FRESH_MINUTES = OBSERVATION_FRESHNESS_POLICY.fresh_max_minutes
    DELAYED_MINUTES = OBSERVATION_FRESHNESS_POLICY.delayed_max_minutes
    CLOCK_SKEW_TOLERANCE_SECONDS = OBSERVATION_FRESHNESS_POLICY.clock_skew_tolerance_seconds

    @staticmethod
    def _coerce_timestamp_utc(value: Any) -> Optional[datetime]:
        """Parse datetime or ISO-8601 string safely into UTC; return None on missing/malformed inputs."""
        return parse_utc_timestamp(value)

    @classmethod
    def evaluate(
        cls,
        data_timestamp: Any,
        reference_time: Optional[datetime] = None,
        policy: Optional[FreshnessThresholdPolicy] = None,
    ) -> Tuple[FreshnessState, Optional[float]]:
        """Compute freshness state and age in minutes against the specified policy.

        Defaults to the observation freshness policy (valid_at: 120m FRESH / 360m DELAYED).
        Returns (FreshnessState.UNAVAILABLE, None) on missing, malformed, or >60s future timestamps.
        """
        active_policy = policy or OBSERVATION_FRESHNESS_POLICY
        ts_utc = cls._coerce_timestamp_utc(data_timestamp)
        if ts_utc is None:
            return FreshnessState.UNAVAILABLE, None

        ref_utc = cls._coerce_timestamp_utc(reference_time) or datetime.now(timezone.utc)
        delta_seconds = (ref_utc - ts_utc).total_seconds()

        # Check future timestamp beyond clock skew tolerance (60s)
        if delta_seconds < -active_policy.clock_skew_tolerance_seconds:
            return FreshnessState.UNAVAILABLE, None

        # Allow slight clock skew between -60s and 0s to be treated as 0.0 minutes
        age_minutes = max(0.0, delta_seconds / 60.0)

        if age_minutes <= active_policy.fresh_max_minutes:
            return FreshnessState.FRESH, round(age_minutes, 1)
        elif age_minutes <= active_policy.delayed_max_minutes:
            return FreshnessState.DELAYED, round(age_minutes, 1)
        else:
            return FreshnessState.STALE, round(age_minutes, 1)

    @classmethod
    def evaluate_observation(
        cls,
        valid_at: Any,
        reference_time: Optional[datetime] = None,
        config: Optional["RainfallRiskConfig"] = None,
    ) -> Tuple[FreshnessState, Optional[float]]:
        """Evaluate physical observation phenomenon timestamp (valid_at)."""
        policy = (
            get_observation_freshness_policy(config)
            if config is not None
            else OBSERVATION_FRESHNESS_POLICY
        )
        return cls.evaluate(valid_at, reference_time=reference_time, policy=policy)

    @classmethod
    def evaluate_ingestion(
        cls,
        fetched_at: Any,
        reference_time: Optional[datetime] = None,
        settings: Optional[Settings] = None,
    ) -> Tuple[FreshnessState, Optional[float]]:
        """Evaluate pipeline ingestion retrieval timestamp (fetched_at)."""
        policy = get_ingestion_freshness_policy(settings)
        return cls.evaluate(fetched_at, reference_time=reference_time, policy=policy)


class DataQualityValidator:
    """Reusable quality validation for physical observations, forecasts, and locations."""

    VALID_MEASUREMENT_UNITS = {
        "rainfall": {"mm"},
        "water_level": {"m"},
        "discharge": {"m3/s", "m³/s"},
    }

    VALID_OBSERVATION_TYPES = {"analysis", "forecast", "observed", "simulation"}
    VALID_MEASUREMENT_TYPES = {"rainfall", "water_level", "discharge"}

    @classmethod
    def validate_observation_value(
        cls,
        value: Any,
        measurement_type: str = "rainfall",
        unit: str = "mm",
        field_name: str = "value",
    ) -> List[DataQualityIssue]:
        """Validate physical measurement value constraints and unit consistency."""
        issues: List[DataQualityIssue] = []

        # 1. Null, NaN, or Infinite values
        if value is None:
            issues.append(
                DataQualityIssue(
                    severity=QualitySeverity.ERROR,
                    code="MISSING_OBSERVATION_VALUE",
                    message=f"Observation '{field_name}' is null or missing.",
                    field=field_name,
                    value=None,
                )
            )
            return issues

        try:
            val_float = float(value)
        except (ValueError, TypeError):
            issues.append(
                DataQualityIssue(
                    severity=QualitySeverity.ERROR,
                    code="INVALID_NUMERIC_TYPE",
                    message=f"Value for '{field_name}' ({value}) cannot be converted to float.",
                    field=field_name,
                    value=str(value),
                )
            )
            return issues

        if math.isnan(val_float) or math.isinf(val_float):
            issues.append(
                DataQualityIssue(
                    severity=QualitySeverity.ERROR,
                    code="NON_FINITE_OBSERVATION_VALUE",
                    message=f"Value for '{field_name}' is NaN or infinite ({val_float}).",
                    field=field_name,
                    value=str(val_float),
                )
            )
            return issues

        # 2. Physics validation: Rainfall and discharge must be non-negative
        if measurement_type == "rainfall" and val_float < 0.0:
            issues.append(
                DataQualityIssue(
                    severity=QualitySeverity.ERROR,
                    code="NEGATIVE_RAINFALL_VALUE",
                    message=f"Atmospheric rainfall cannot be negative: {val_float} mm.",
                    field=field_name,
                    value=val_float,
                )
            )
        elif measurement_type == "discharge" and val_float < 0.0:
            issues.append(
                DataQualityIssue(
                    severity=QualitySeverity.ERROR,
                    code="NEGATIVE_DISCHARGE_VALUE",
                    message=f"River discharge rate cannot be negative: {val_float} m3/s.",
                    field=field_name,
                    value=val_float,
                )
            )
        # Water level (stage) CAN be negative depending on local gauge datum reference

        # 3. Unit validation
        expected_units = cls.VALID_MEASUREMENT_UNITS.get(measurement_type, set())
        if unit not in expected_units:
            issues.append(
                DataQualityIssue(
                    severity=QualitySeverity.WARNING,
                    code="UNEXPECTED_MEASUREMENT_UNIT",
                    message=f"Unit '{unit}' is unexpected for measurement type '{measurement_type}' (expected: {sorted(expected_units)}).",
                    field="unit",
                    value=unit,
                )
            )

        return issues

    @classmethod
    def validate_timestamp(
        cls,
        dt: Any,
        field_name: str = "valid_at",
        reference_time: Optional[datetime] = None,
        max_future_seconds: Optional[float] = None,
    ) -> Tuple[Optional[datetime], List[DataQualityIssue]]:
        """Validate timestamp parseability, timezone normalization, and future limits."""
        issues: List[DataQualityIssue] = []

        if dt is None:
            issues.append(
                DataQualityIssue(
                    severity=QualitySeverity.ERROR,
                    code="MISSING_TIMESTAMP",
                    message=f"Timestamp '{field_name}' is missing.",
                    field=field_name,
                )
            )
            return None, issues

        utc_dt: Optional[datetime] = None
        if isinstance(dt, datetime):
            utc_dt = normalize_to_utc(dt)
        elif isinstance(dt, str):
            if not dt.strip():
                issues.append(
                    DataQualityIssue(
                        severity=QualitySeverity.ERROR,
                        code="MISSING_TIMESTAMP",
                        message=f"Timestamp '{field_name}' is missing or empty.",
                        field=field_name,
                        value=dt,
                    )
                )
                return None, issues
            utc_dt = parse_utc_timestamp(dt)
            if utc_dt is None:
                issues.append(
                    DataQualityIssue(
                        severity=QualitySeverity.ERROR,
                        code="INVALID_TIMESTAMP_FORMAT",
                        message=f"Invalid timestamp format for '{field_name}': '{dt}'.",
                        field=field_name,
                        value=dt,
                    )
                )
                return None, issues
        else:
            issues.append(
                DataQualityIssue(
                    severity=QualitySeverity.ERROR,
                    code="INVALID_TIMESTAMP_TYPE",
                    message=f"Timestamp '{field_name}' has unsupported type '{type(dt).__name__}'.",
                    field=field_name,
                )
            )
            return None, issues

        if max_future_seconds is not None and utc_dt:
            ref_utc = normalize_to_utc(reference_time) or datetime.now(timezone.utc)
            delta = (utc_dt - ref_utc).total_seconds()
            if delta > max_future_seconds:
                issues.append(
                    DataQualityIssue(
                        severity=QualitySeverity.ERROR,
                        code="TIMESTAMP_IN_FUTURE",
                        message=f"Timestamp '{field_name}' ({utc_dt.isoformat()}) exceeds future threshold by {delta:.1f}s.",
                        field=field_name,
                        value=utc_dt.isoformat(),
                    )
                )

        return utc_dt, issues

    @classmethod
    def validate_forecast_horizon(
        cls,
        forecast_records: List[Dict[str, Any]],
        reference_time: Optional[datetime] = None,
        min_hours: int = 24,
    ) -> Tuple[QualityStatus, List[DataQualityIssue], Dict[str, Any]]:
        """Validate forecast horizon completeness, chronological sequencing, and missing data gaps."""
        issues: List[DataQualityIssue] = []
        ref_utc = normalize_to_utc(reference_time) or datetime.now(timezone.utc)

        if not forecast_records:
            issues.append(
                DataQualityIssue(
                    severity=QualitySeverity.WARNING,
                    code="EMPTY_FORECAST_HORIZON",
                    message="No active forecast records available for location.",
                    field="forecast_records",
                )
            )
            return QualityStatus.WARNING, issues, {"horizon_hours": 0, "completeness_pct": 0.0}

        # Extract and sort future timestamps
        valid_points = []
        for i, fc in enumerate(forecast_records):
            if not isinstance(fc, dict):
                issues.append(
                    DataQualityIssue(
                        severity=QualitySeverity.ERROR,
                        code="INVALID_FORECAST_RECORD",
                        message=f"Forecast record at index {i} has unexpected type '{type(fc).__name__}'.",
                        field=f"forecast_records[{i}]",
                    )
                )
                continue
            raw_v = fc.get("valid_at")
            v_dt, t_issues = cls.validate_timestamp(raw_v, field_name=f"forecast_records[{i}].valid_at")
            if t_issues:
                issues.extend(t_issues)
                continue

            val = fc.get("value")
            val_issues = cls.validate_observation_value(val, measurement_type="rainfall", field_name=f"forecast_records[{i}].value")
            if val_issues:
                issues.extend(val_issues)

            if v_dt and v_dt >= ref_utc:
                valid_points.append((v_dt, val))

        valid_points.sort(key=lambda p: p[0])
        future_count = len(valid_points)

        # Check minimum forecast horizon
        if future_count < min_hours:
            issues.append(
                DataQualityIssue(
                    severity=QualitySeverity.WARNING,
                    code="INCOMPLETE_FORECAST_HORIZON",
                    message=f"Active forecast horizon has {future_count} hours (minimum recommended: {min_hours}h).",
                    field="forecast_horizon",
                    value=future_count,
                )
            )

        # Check for chronological gaps > 1 hour
        gap_count = 0
        for i in range(len(valid_points) - 1):
            t1 = valid_points[i][0]
            t2 = valid_points[i + 1][0]
            gap_seconds = (t2 - t1).total_seconds()
            if gap_seconds > 3600.0:
                gap_hours = gap_seconds / 3600.0
                gap_count += 1
                if gap_count <= 3:  # limit reporting noise
                    issues.append(
                        DataQualityIssue(
                            severity=QualitySeverity.WARNING,
                            code="FORECAST_TIMESTEP_GAP",
                            message=f"Gap of {gap_hours:.1f} hours detected between {t1.isoformat()} and {t2.isoformat()}.",
                            field="valid_at",
                        )
                    )

        # Compute completeness ratio against expected horizon
        expected_points = max(min_hours, future_count)
        completeness_pct = round((future_count / expected_points) * 100.0, 1) if expected_points > 0 else 0.0

        status = QualityStatus.PASS
        has_errors = any(i.severity in [QualitySeverity.ERROR, QualitySeverity.CRITICAL] for i in issues)
        has_warnings = any(i.severity == QualitySeverity.WARNING for i in issues)

        if has_errors:
            status = QualityStatus.FAIL
        elif has_warnings:
            status = QualityStatus.WARNING

        metadata = {
            "horizon_hours": future_count,
            "completeness_pct": completeness_pct,
            "gaps_detected": gap_count,
        }
        return status, issues, metadata

    @classmethod
    def validate_location_metadata(cls, loc: Dict[str, Any]) -> List[DataQualityIssue]:
        """Validate geographic coordinates and required location metadata."""
        issues: List[DataQualityIssue] = []

        if not loc.get("id"):
            issues.append(DataQualityIssue(severity=QualitySeverity.ERROR, code="MISSING_LOCATION_ID", message="Location ID is required."))

        if not loc.get("name"):
            issues.append(DataQualityIssue(severity=QualitySeverity.ERROR, code="MISSING_LOCATION_NAME", message="Location name is required."))

        # Latitude check (-90 to 90)
        try:
            lat = float(loc.get("latitude", -999))
            if not (-90.0 <= lat <= 90.0):
                issues.append(
                    DataQualityIssue(
                        severity=QualitySeverity.ERROR,
                        code="INVALID_LATITUDE",
                        message=f"Latitude {lat} is outside valid geographic range [-90.0, 90.0].",
                        field="latitude",
                        value=lat,
                    )
                )
        except (ValueError, TypeError):
            issues.append(
                DataQualityIssue(
                    severity=QualitySeverity.ERROR,
                    code="NON_NUMERIC_LATITUDE",
                    message=f"Latitude '{loc.get('latitude')}' is not a valid number.",
                    field="latitude",
                )
            )

        # Longitude check (-180 to 180)
        try:
            lon = float(loc.get("longitude", -999))
            if not (-180.0 <= lon <= 180.0):
                issues.append(
                    DataQualityIssue(
                        severity=QualitySeverity.ERROR,
                        code="INVALID_LONGITUDE",
                        message=f"Longitude {lon} is outside valid geographic range [-180.0, 180.0].",
                        field="longitude",
                        value=lon,
                    )
                )
        except (ValueError, TypeError):
            issues.append(
                DataQualityIssue(
                    severity=QualitySeverity.ERROR,
                    code="NON_NUMERIC_LONGITUDE",
                    message=f"Longitude '{loc.get('longitude')}' is not a valid number.",
                    field="longitude",
                )
            )

        return issues

    @classmethod
    def check_duplicate_observations(
        cls, observations: List[Dict[str, Any]]
    ) -> List[DataQualityIssue]:
        """Detect duplicate observation records within an ingestion or persistence batch."""
        issues: List[DataQualityIssue] = []
        seen_keys: Set[Tuple] = set()

        for i, obs in enumerate(observations):
            key = (
                obs.get("location_id"),
                obs.get("data_source_id"),
                obs.get("measurement_type", "rainfall"),
                obs.get("observation_type"),
                obs.get("valid_at"),
                obs.get("forecast_reference_time"),
            )
            if key in seen_keys:
                issues.append(
                    DataQualityIssue(
                        severity=QualitySeverity.ERROR,
                        code="DUPLICATE_OBSERVATION_IN_BATCH",
                        message=f"Duplicate observation detected at index {i} for location '{obs.get('location_id')}' and valid_at '{obs.get('valid_at')}'.",
                        field="valid_at",
                        value=obs.get("valid_at"),
                    )
                )
            else:
                seen_keys.add(key)

        return issues
