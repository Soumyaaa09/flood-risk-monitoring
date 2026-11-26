"""Historical Rainfall Analytics and Antecedent Rainfall Indicator Engine for FloodWatch.

Strict Hydrological and Data Principles:
1. Historical aggregation strictly utilizes observed / analysis records ('observed', 'analysis', 'reanalysis').
2. FORECAST records are explicitly excluded from all historical aggregations.
3. Timestamps are normalized to UTC.
4. Duplicate observations for the same timestamp are deduplicated safely.
5. Missing data is NEVER converted to zero.
6. Available coverage and missing intervals are explicitly audited and reported.
7. Antecedent Rainfall Indicator (ARI) is an empirical precipitation accumulation index.
   It does NOT simulate soil moisture, infiltration rates, or groundwater elevation.
8. A 'rainy day' is defined according to the IMD standard: daily precipitation >= 2.5 mm.
"""

from datetime import date, datetime, time, timedelta, timezone
import math
from typing import Any, Dict, List, Optional, Tuple

from app.schemas.risk import (
    AntecedentCategory,
    AntecedentRainfallIndicator,
    DailyRainfallRecord,
    DailyReportingWindow,
    HistoricalRainfallContext,
    RainfallTrend,
    RiskConfidence,
)
from app.services.data_quality import normalize_to_utc


# Indian Standard Time (IST): UTC+05:30
IST_TIMEZONE = timezone(timedelta(hours=5, minutes=30))


class HistoricalRainfallAnalyzer:
    """Computes transparent time-series aggregations and antecedent indicators from historical observations."""

    # IMD Meteorological Standard for a rainy day in India (applied to local telemetry window)
    RAINY_DAY_THRESHOLD_MM = 2.5

    # Minimum coverage required for confident historical totals
    MIN_COVERAGE_PCT = 50.0

    @classmethod
    def sanitize_and_deduplicate(
        cls, raw_observations: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Filter to valid historical rainfall observations, exclude forecasts, and deduplicate by valid_at.
        
        Returns:
            Chronologically sorted list of unique observations by valid_at ascending.
        """
        valid_points: Dict[str, Dict[str, Any]] = {}

        for obs in raw_observations:
            if not isinstance(obs, dict):
                continue

            # 1. Strictly exclude forecasts and simulations
            obs_type = str(obs.get("observation_type", "")).lower()
            if obs_type not in ("observed", "analysis", "reanalysis"):
                continue

            # 2. Measurement must be rainfall
            meas_type = str(obs.get("measurement_type", "")).lower()
            if meas_type != "rainfall":
                continue

            # 3. Unit validation
            unit = str(obs.get("unit", "")).lower()
            if unit != "mm":
                continue

            # 4. Value validation: non-negative, non-null, finite
            val = obs.get("value")
            if val is None:
                continue
            try:
                val_float = float(val)
                if math.isnan(val_float) or math.isinf(val_float) or val_float < 0.0:
                    continue
            except (ValueError, TypeError):
                continue

            # 5. Timestamp validation and UTC normalization
            raw_valid = obs.get("valid_at")
            if not raw_valid:
                continue
            try:
                if isinstance(raw_valid, str):
                    dt = datetime.fromisoformat(raw_valid.replace("Z", "+00:00"))
                elif isinstance(raw_valid, datetime):
                    dt = raw_valid
                else:
                    continue
                dt_utc = normalize_to_utc(dt)
            except Exception:
                continue

            if dt_utc is None:
                continue

            iso_key = dt_utc.isoformat()
            meta = obs.get("metadata") if isinstance(obs.get("metadata"), dict) else {}

            # Deduplication: If already present, keep the latest fetched_at or created_at
            if iso_key in valid_points:
                existing_fetch = valid_points[iso_key].get("fetched_at", "")
                current_fetch = obs.get("fetched_at", "")
                if current_fetch and current_fetch > existing_fetch:
                    valid_points[iso_key] = {
                        "valid_at": dt_utc,
                        "value": round(val_float, 4),
                        "unit": "mm",
                        "fetched_at": current_fetch,
                        "observation_type": obs_type,
                        "metadata": meta,
                    }
            else:
                valid_points[iso_key] = {
                    "valid_at": dt_utc,
                    "value": round(val_float, 4),
                    "unit": "mm",
                    "fetched_at": obs.get("fetched_at", ""),
                    "observation_type": obs_type,
                    "metadata": meta,
                }

        # Sort chronologically
        sorted_records = sorted(valid_points.values(), key=lambda x: x["valid_at"])
        return sorted_records

    @classmethod
    def detect_observation_interval(cls, sorted_observations: List[Dict[str, Any]]) -> float:
        """Detect the observation interval (sampling cadence) in minutes.
        
        Priority:
        1. Explicit accumulation_interval_seconds from record metadata.
        2. Median delta t (in minutes) between consecutive observations.
        3. Default to 60.0 minutes (hourly) if insufficient data.
        """
        if not sorted_observations:
            return 60.0

        # 1. Check metadata for accumulation_interval_seconds
        meta_intervals: List[float] = []
        for obs in sorted_observations:
            meta = obs.get("metadata", {})
            if isinstance(meta, dict):
                sec = meta.get("accumulation_interval_seconds")
                if sec is not None:
                    try:
                        sec_val = float(sec)
                        if sec_val > 0:
                            meta_intervals.append(sec_val / 60.0)
                    except (ValueError, TypeError):
                        pass

        if meta_intervals:
            meta_intervals.sort()
            return meta_intervals[len(meta_intervals) // 2]

        # 2. Calculate deltas between consecutive valid_at timestamps
        # Only deltas <= 180.0 minutes (3 hours) represent realistic observation cadence;
        # larger deltas represent telemetry data gaps between hourly/sub-hourly readings.
        if len(sorted_observations) >= 2:
            deltas_min: List[float] = []
            for i in range(len(sorted_observations) - 1):
                t1 = sorted_observations[i]["valid_at"]
                t2 = sorted_observations[i + 1]["valid_at"]
                d_min = (t2 - t1).total_seconds() / 60.0
                if 5.0 <= d_min <= 180.0:
                    deltas_min.append(d_min)

            if deltas_min:
                deltas_min.sort()
                return deltas_min[len(deltas_min) // 2]

        # 3. Default fallback: 60 minutes (standard hourly weather observations)
        return 60.0

    @classmethod
    def analyze_history(
        cls,
        raw_observations: List[Dict[str, Any]],
        reference_time: Optional[datetime] = None,
        window_days: int = 7,
        timezone_str: str = "Asia/Kolkata",
        reporting_window: DailyReportingWindow = DailyReportingWindow.IST_CALENDAR_DAY,
    ) -> HistoricalRainfallContext:
        """Calculate 24h, 72h, 7d totals, daily breakdowns, trends, and data coverage."""
        ref_utc = normalize_to_utc(reference_time) or datetime.now(timezone.utc)
        clean_obs = cls.sanitize_and_deduplicate(raw_observations)
        detected_interval_min = cls.detect_observation_interval(clean_obs)

        window_start = ref_utc - timedelta(days=window_days)
        t_24h = ref_utc - timedelta(hours=24)
        t_72h = ref_utc - timedelta(hours=72)
        t_7d = ref_utc - timedelta(days=7)

        # Filter observations within window
        in_window = [o for o in clean_obs if window_start <= o["valid_at"] <= ref_utc]

        # Dynamically calculate expected points using detected interval (never hardcoded to 24)
        expected_points = max(1, round((window_days * 24 * 60) / detected_interval_min))
        actual_points = len(in_window)
        coverage_pct = min(100.0, round((actual_points / expected_points) * 100.0, 1))

        # Detect missing intervals (gaps > 2 * detected_interval)
        interval_sec = detected_interval_min * 60.0
        missing_intervals = 0
        gap_warning: Optional[str] = None

        if len(in_window) >= 2:
            for i in range(len(in_window) - 1):
                delta_sec = (in_window[i + 1]["valid_at"] - in_window[i]["valid_at"]).total_seconds()
                if delta_sec > 2.0 * interval_sec:
                    missing_intervals += int(delta_sec // interval_sec) - 1
        elif len(in_window) == 0:
            missing_intervals = expected_points

        has_sufficient_coverage = coverage_pct >= cls.MIN_COVERAGE_PCT
        unit_desc = "hourly" if round(detected_interval_min) == 60 else f"{int(detected_interval_min)}-minute"

        if not has_sufficient_coverage:
            gap_warning = (
                f"Historical data coverage is {coverage_pct}% (< {cls.MIN_COVERAGE_PCT}% minimum). "
                f"Found {missing_intervals} missing {unit_desc} interval(s)."
            )
        elif missing_intervals > 0:
            gap_warning = f"Data contains {missing_intervals} missing {unit_desc} interval(s)."

        # Window accumulations (rolling hours relative to reference_time)
        obs_24h = [o["value"] for o in in_window if o["valid_at"] >= t_24h]
        obs_72h = [o["value"] for o in in_window if o["valid_at"] >= t_72h]
        obs_7d = [o["value"] for o in in_window if o["valid_at"] >= t_7d]

        # Ensure we do NOT replace missing data with zero:
        tot_24h = round(sum(obs_24h), 2) if len(obs_24h) > 0 else None
        tot_72h = round(sum(obs_72h), 2) if len(obs_72h) > 0 else None
        tot_7d = round(sum(obs_7d), 2) if len(obs_7d) > 0 else None

        # Daily rainfall aggregation in designated reporting window & timezone
        daily_records, consecutive_rainy_days, daily_trend, current_day_in_progress = cls._compute_daily_records(
            in_window_obs=in_window,
            window_start=window_start,
            ref_utc=ref_utc,
            timezone_str=timezone_str,
            reporting_window=reporting_window,
            detected_interval_min=detected_interval_min,
        )

        return HistoricalRainfallContext(
            reference_time=ref_utc,
            window_days=window_days,
            timezone=timezone_str,
            reporting_window=reporting_window,
            detected_interval_minutes=detected_interval_min,
            accumulation_type="discrete_sampled_depth",
            rainfall_24h_mm=tot_24h,
            rainfall_72h_mm=tot_72h,
            rainfall_7d_mm=tot_7d,
            daily_totals=daily_records,
            daily_trend=daily_trend,
            consecutive_rainy_days=consecutive_rainy_days,
            current_day_in_progress=current_day_in_progress,
            coverage_pct=coverage_pct,
            missing_intervals_count=missing_intervals,
            data_gap_warning=gap_warning,
            has_sufficient_coverage=has_sufficient_coverage,
        )

    @classmethod
    def _compute_daily_records(
        cls,
        in_window_obs: List[Dict[str, Any]],
        window_start: datetime,
        ref_utc: datetime,
        timezone_str: str = "Asia/Kolkata",
        reporting_window: DailyReportingWindow = DailyReportingWindow.IST_CALENDAR_DAY,
        detected_interval_min: float = 60.0,
    ) -> Tuple[List[DailyRainfallRecord], int, RainfallTrend, bool]:
        """Aggregate observations by reporting window dates, evaluate rainy days, trend, and partial days."""
        if timezone_str == "UTC":
            tz = timezone.utc
        else:
            tz = IST_TIMEZONE

        def get_bucket_info(dt_utc: datetime) -> Tuple[str, datetime, datetime]:
            """Determine bucket date label, start UTC, and end UTC for a given observation timestamp."""
            if reporting_window == DailyReportingWindow.IMD_SYNOPTIC_DAY:
                # IMD synoptic day runs 08:30 IST to 08:30 IST next day (03:00 UTC to 03:00 UTC next day)
                shifted = dt_utc - timedelta(hours=3)
                synoptic_date = shifted.date()
                label = synoptic_date.strftime("%Y-%m-%d")
                start_utc = datetime.combine(synoptic_date, time(3, 0, 0), tzinfo=timezone.utc)
                end_utc = start_utc + timedelta(days=1)
                return label, start_utc, end_utc
            else:
                # IST_CALENDAR_DAY (00:00 to 24:00 IST = 18:30 UTC D-1 to 18:30 UTC D)
                dt_local = dt_utc.astimezone(tz)
                local_date = dt_local.date()
                label = local_date.strftime("%Y-%m-%d")
                start_local = datetime.combine(local_date, time.min, tzinfo=tz)
                end_local = start_local + timedelta(days=1)
                start_utc = start_local.astimezone(timezone.utc)
                end_utc = end_local.astimezone(timezone.utc)
                return label, start_utc, end_utc

        # Determine start bucket and end bucket bounds
        _, first_start_utc, _ = get_bucket_info(window_start)
        _, _, last_end_utc = get_bucket_info(ref_utc)

        # Generate continuous list of day buckets from first_start_utc to last_end_utc
        buckets: List[Tuple[str, datetime, datetime]] = []
        curr_start = first_start_utc
        while curr_start < last_end_utc:
            mid_pt = curr_start + timedelta(hours=12)
            label, b_start, b_end = get_bucket_info(mid_pt)
            buckets.append((label, b_start, b_end))
            curr_start = b_end

        # Map observations into buckets
        obs_by_bucket: Dict[str, List[float]] = {}
        for obs in in_window_obs:
            o_time = obs["valid_at"]
            label, b_start, b_end = get_bucket_info(o_time)
            obs_by_bucket.setdefault(label, []).append(obs["value"])

        expected_pts_full_day = max(1, round((24.0 * 60.0) / detected_interval_min))

        daily_records: List[DailyRainfallRecord] = []
        current_day_in_progress = False

        for label, b_start, b_end in buckets:
            vals = obs_by_bucket.get(label, [])
            is_partial = ref_utc < b_end
            if is_partial:
                current_day_in_progress = True

            day_count = len(vals)
            if day_count > 0:
                day_total = round(sum(vals), 2)
                is_rainy = day_total >= cls.RAINY_DAY_THRESHOLD_MM
            else:
                # Remediation DATA-01: Days with zero observations must preserve None, never falsely 0.0 mm
                day_total = None
                is_rainy = False

            if is_partial:
                elapsed_sec = max(0.0, (ref_utc - b_start).total_seconds())
                elapsed_hr = min(24.0, elapsed_sec / 3600.0)
                expected_pts = max(1, round((elapsed_hr * 60.0) / detected_interval_min))
            else:
                expected_pts = expected_pts_full_day

            day_coverage = (
                min(100.0, round((day_count / expected_pts) * 100.0, 1))
                if expected_pts > 0
                else 0.0
            )

            daily_records.append(
                DailyRainfallRecord(
                    date=label,
                    start_time_utc=b_start,
                    end_time_utc=b_end,
                    rainfall_mm=day_total,
                    observation_count=day_count,
                    is_rainy_day=is_rainy,
                    coverage_pct=day_coverage,
                    is_partial_day=is_partial,
                    reporting_window=reporting_window.value,
                )
            )

        # Compute consecutive rainy days
        consecutive = 0
        records_to_evaluate = list(reversed(daily_records))
        latest_rec = daily_records[-1] if daily_records else None

        if latest_rec and latest_rec.is_partial_day:
            if latest_rec.is_rainy_day:
                # Today has already accumulated >= 2.5 mm; counts towards streak
                pass
            else:
                # Today is in-progress and < 2.5 mm; do not let morning reset streak
                records_to_evaluate = records_to_evaluate[1:]

        for rec in records_to_evaluate:
            if rec.observation_count > 0 and rec.is_rainy_day:
                consecutive += 1
            else:
                break

        # Compute daily rainfall trend using completed days with data
        # Exclude in-progress partial day if it has < 20 hours elapsed
        completed_days = [
            r for r in daily_records
            if (not r.is_partial_day or (ref_utc - (r.start_time_utc or ref_utc)).total_seconds() >= 20 * 3600)
            and r.observation_count > 0
            and r.rainfall_mm is not None
        ]

        if len(completed_days) < 2:
            trend = RainfallTrend.INSUFFICIENT_DATA
        elif all(r.rainfall_mm == 0.0 for r in completed_days):
            trend = RainfallTrend.NO_RAIN
        else:
            mid = len(completed_days) // 2
            earlier = completed_days[:mid]
            recent = completed_days[mid:]
            avg_earlier = sum(r.rainfall_mm for r in earlier) / max(1, len(earlier))
            avg_recent = sum(r.rainfall_mm for r in recent) / max(1, len(recent))

            diff = avg_recent - avg_earlier
            if diff > 5.0:
                trend = RainfallTrend.INCREASING
            elif diff < -5.0:
                trend = RainfallTrend.DECREASING
            else:
                trend = RainfallTrend.STEADY

        return daily_records, consecutive, trend, current_day_in_progress

    @classmethod
    def calculate_antecedent_indicator(
        cls, history: HistoricalRainfallContext
    ) -> AntecedentRainfallIndicator:
        """Derive the empirical Antecedent Rainfall Indicator from multi-window historical totals."""
        r24 = history.rainfall_24h_mm
        r72 = history.rainfall_72h_mm
        r7d = history.rainfall_7d_mm

        # Strict completeness check: require sufficient coverage and all three underlying totals
        if not history.has_sufficient_coverage or r24 is None or r72 is None or r7d is None:
            return AntecedentRainfallIndicator(
                indicator_category=AntecedentCategory.UNAVAILABLE,
                indicator_value_mm=None,
                underlying_24h_mm=r24,
                underlying_72h_mm=r72,
                underlying_7d_mm=r7d,
                coverage_pct=history.coverage_pct,
                missing_intervals_count=history.missing_intervals_count,
                confidence=RiskConfidence.NONE,
                explanation=(
                    f"Antecedent indicator UNAVAILABLE: Historical data coverage is {history.coverage_pct}% "
                    f"(< {cls.MIN_COVERAGE_PCT}% required) or historical observations are missing across required windows."
                ),
            )

        # Multi-window weighted accumulation: 0.5 * 24h + 0.3 * 72h + 0.2 * 7d
        ari_value = round((0.5 * r24) + (0.3 * r72) + (0.2 * r7d), 2)

        # Categorization based on research antecedent accumulation thresholds
        if ari_value >= 75.0 or r72 >= 100.0:
            category = AntecedentCategory.EXTREME_ANTECEDENT
            desc = (
                f"EXTREME antecedent rainfall accumulation ({ari_value:.1f} mm index; 72h total: {r72:.1f} mm). "
                f"High preceding surface water volume indicates significant runoff readiness."
            )
        elif ari_value >= 35.0 or r72 >= 50.0:
            category = AntecedentCategory.HIGH_ANTECEDENT
            desc = (
                f"HIGH antecedent rainfall accumulation ({ari_value:.1f} mm index; 72h total: {r72:.1f} mm). "
                f"Preceding rainfall has significantly elevated catchment runoff potential."
            )
        elif ari_value >= 15.0 or r72 >= 25.0:
            category = AntecedentCategory.MODERATE_ANTECEDENT
            desc = (
                f"MODERATE antecedent rainfall accumulation ({ari_value:.1f} mm index; 72h total: {r72:.1f} mm). "
                f"Moderate surface moisture accumulation observed over preceding days."
            )
        else:
            category = AntecedentCategory.LOW_ANTECEDENT
            desc = (
                f"LOW antecedent rainfall accumulation ({ari_value:.1f} mm index; 72h total: {r72:.1f} mm). "
                f"Minimal preceding rainfall detected across the pilot station catchment."
            )

        confidence = (
            RiskConfidence.HIGH
            if history.coverage_pct >= 90.0 and history.missing_intervals_count == 0
            else RiskConfidence.MEDIUM
        )

        return AntecedentRainfallIndicator(
            indicator_category=category,
            indicator_value_mm=ari_value,
            underlying_24h_mm=r24,
            underlying_72h_mm=r72,
            underlying_7d_mm=r7d,
            coverage_pct=history.coverage_pct,
            missing_intervals_count=history.missing_intervals_count,
            confidence=confidence,
            explanation=desc,
        )
