"""Reporting & Data Export Service for FloodWatch (Phase 2.8).

Architectural Principles:
1. Reuses existing HistoricalRainfallAnalyzer and RiskService calculations.
2. Preserves missing data as None / empty string — NEVER replaces missing telemetry with zero.
3. Excludes forecasts from historical reports; forecasts appear strictly in the separate risk outlook.
4. Generates RFC 4180-compliant CSV with formula injection protection for spreadsheet safety.
5. Operates in Indian Standard Time (Asia/Kolkata, UTC+05:30) for daily calendar aggregation.
"""

import csv
from datetime import date, datetime, timedelta, timezone
import io
import logging
import re
from typing import Any, Dict, List, Optional
import uuid

from app.core.config import get_settings
from app.db.supabase_client import SupabaseClient
from app.risk_engine.historical_analyzer import IST_TIMEZONE, HistoricalRainfallAnalyzer
from app.schemas.report import (
    DEFAULT_REPORT_DISCLOSURE,
    VIRTUAL_GRID_DISCLOSURE,
    DateRangeInfo,
    RainfallReportResponse,
    ReportAccumulationSummary,
    ReportAntecedentInfo,
    ReportDailyRecord,
    ReportLocationInfo,
    ReportRiskSummary,
)
from app.services.data_quality import normalize_to_utc
from app.services.risk_service import RiskService

logger = logging.getLogger(__name__)

# Dangerous characters at start of field in spreadsheet software (Excel, LibreOffice, Sheets)
FORMULA_INJECTION_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def sanitize_csv_cell(value: Any) -> str:
    """Protect against spreadsheet formula injection (CSV Injection / CWE-1236).
    
    If text begins with dangerous formula trigger characters (=, +, -, @, \t, \r),
    preface with a single quote (') so spreadsheets treat it strictly as text.
    """
    if value is None:
        return ""
    text = str(value)
    if text.startswith(FORMULA_INJECTION_PREFIXES):
        return f"'{text}"
    return text


class ReportingService:
    """Generates structured JSON rainfall reports and sanitised CSV exports."""

    def __init__(
        self,
        supabase_client: Optional[SupabaseClient] = None,
        risk_service: Optional[RiskService] = None,
    ):
        self.supabase = supabase_client or SupabaseClient()
        self.risk_service = risk_service or RiskService(supabase_client=self.supabase)
        self.settings = get_settings()

    async def generate_rainfall_report(
        self,
        location_id: str,
        days: int = 7,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        now_time: Optional[datetime] = None,
    ) -> RainfallReportResponse:
        """Construct comprehensive historical rainfall report for a pilot location."""
        ts = normalize_to_utc(now_time) or datetime.now(timezone.utc)
        ist_now = ts.astimezone(IST_TIMEZONE)

        # 1. Validate location
        pilot_locations = await self.supabase.get_pilot_locations()
        matching = [l for l in pilot_locations if str(l["id"]) == location_id]
        if not matching:
            raise ValueError(f"Pilot location '{location_id}' not found.")

        loc = matching[0]
        loc_name = str(loc["name"])
        district = (
            loc.get("districts", {}).get("name")
            if isinstance(loc.get("districts"), dict)
            else loc.get("district_name") or "Mahanadi Delta"
        )
        lat = float(loc["latitude"])
        lon = float(loc["longitude"])

        # 2. Date range resolution & validation
        if start_date and end_date:
            try:
                s_d = date.fromisoformat(start_date)
                e_d = date.fromisoformat(end_date)
            except Exception as exc:
                raise ValueError(f"Invalid date format (must be YYYY-MM-DD): {exc}")

            if s_d > e_d:
                raise ValueError(f"start_date ({start_date}) cannot be after end_date ({end_date}).")

            window_days = (e_d - s_d).days + 1
            if window_days > 90:
                raise ValueError(
                    f"Requested date range ({window_days} days) exceeds maximum allowable reporting limit of 90 days."
                )
            if window_days < 1:
                raise ValueError("Requested date range must span at least 1 day.")

            start_str = s_d.isoformat()
            end_str = e_d.isoformat()
        else:
            if days < 1 or days > 90:
                raise ValueError(f"Reporting window days ({days}) must be between 1 and 90.")
            window_days = days
            end_d = ist_now.date()
            start_d = end_d - timedelta(days=window_days - 1)
            start_str = start_d.isoformat()
            end_str = end_d.isoformat()

        # 3. Retrieve historical observations and compute analytics
        # Reuses existing RiskService.get_location_history_context
        history, antecedent = await self.risk_service.get_location_history_context(
            location_id=location_id,
            days=window_days,
            now_time=ts,
        )

        # 4. Map daily records, preserving missing data
        report_daily_records: List[ReportDailyRecord] = []
        for dr in history.daily_totals:
            # If day has 0 observations or insufficient coverage, keep explicit rainfall value or None
            rf_val = dr.rainfall_mm if dr.observation_count > 0 else None
            report_daily_records.append(
                ReportDailyRecord(
                    date=dr.date,
                    rainfall_mm=rf_val,
                    observation_count=dr.observation_count,
                    coverage_pct=dr.coverage_pct,
                    is_rainy_day=dr.is_rainy_day,
                    is_partial_day=dr.is_partial_day,
                    reporting_window=dr.reporting_window,
                    notes=(
                        "Current day in progress"
                        if dr.is_partial_day
                        else "No observations recorded"
                        if dr.observation_count == 0
                        else None
                    ),
                )
            )

        # Total window accumulation calculation
        valid_daily_values = [r.rainfall_mm for r in report_daily_records if r.rainfall_mm is not None]
        tot_window = round(sum(valid_daily_values), 2) if valid_daily_values else None
        rainy_count = sum(1 for r in report_daily_records if r.is_rainy_day)

        summary = ReportAccumulationSummary(
            rainfall_24h_mm=history.rainfall_24h_mm,
            rainfall_72h_mm=history.rainfall_72h_mm,
            rainfall_7d_mm=history.rainfall_7d_mm,
            total_window_rainfall_mm=tot_window,
            rainy_days_count=rainy_count,
            consecutive_rainy_days=history.consecutive_rainy_days,
            daily_trend=history.daily_trend.value,
            window_coverage_pct=history.coverage_pct,
            missing_intervals_count=history.missing_intervals_count,
            has_sufficient_coverage=history.has_sufficient_coverage,
        )

        # 5. Map Antecedent indicator context
        antecedent_info = None
        if antecedent:
            antecedent_info = ReportAntecedentInfo(
                category=antecedent.indicator_category.value,
                value_mm=antecedent.indicator_value_mm,
                confidence=antecedent.confidence.value,
                explanation=antecedent.explanation,
            )

        # 6. Retrieve current risk assessment context
        risk_info = None
        try:
            loc_risk = await self.risk_service.get_location_rainfall_risk(
                location_id=location_id, now_time=ts
            )
            if loc_risk:
                risk_info = ReportRiskSummary(
                    risk_category=loc_risk.risk_category.value,
                    confidence=loc_risk.confidence.value,
                    recent_observed_mm=loc_risk.recent_observed_rainfall_mm,
                    forecast_24h_mm=loc_risk.forecast_24h_precipitation_mm,
                )
        except Exception as exc:
            logger.warning("Failed to attach risk summary to report: %s", exc.__class__.__name__)

        # 7. Collect quality warnings
        warnings: List[str] = []
        if history.data_gap_warning:
            warnings.append(history.data_gap_warning)
        if not history.has_sufficient_coverage:
            warnings.append(
                f"Data completeness across the {window_days}-day period is {history.coverage_pct:.1f}% "
                f"(recommended threshold: 50.0%). Historical totals may be underestimated."
            )
        if history.missing_intervals_count > 0:
            warnings.append(
                f"Detected {history.missing_intervals_count} missing telemetry intervals within the reporting window."
            )

        report_id = f"rpt_{uuid.uuid4().hex[:12]}"
        ist_str = ist_now.strftime("%Y-%m-%d %H:%M:%S IST")

        return RainfallReportResponse(
            report_id=report_id,
            generated_at_utc=ts,
            generated_at_ist=ist_str,
            location=ReportLocationInfo(
                location_id=location_id,
                location_name=loc_name,
                district_name=district,
                latitude=lat,
                longitude=lon,
            ),
            date_range=DateRangeInfo(
                start_date=start_str,
                end_date=end_str,
                window_days=window_days,
            ),
            summary=summary,
            daily_records=report_daily_records,
            antecedent_indicator=antecedent_info,
            risk_summary=risk_info,
            data_quality_warnings=warnings,
        )

    @classmethod
    def generate_rainfall_csv(cls, report: RainfallReportResponse) -> str:
        """Serialize RainfallReportResponse to standard RFC 4180 CSV with injection protection.
        
        Columns:
        date, location_name, district, rainfall_mm, observation_count, coverage_pct, is_rainy_day, is_partial_day, reporting_window, timezone
        """
        output = io.StringIO()
        writer = csv.writer(output, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)

        # Header row
        writer.writerow([
            "date",
            "location_name",
            "district",
            "rainfall_mm",
            "observation_count",
            "coverage_pct",
            "is_rainy_day",
            "is_partial_day",
            "reporting_window",
            "timezone",
        ])

        loc_name = sanitize_csv_cell(report.location.location_name)
        district = sanitize_csv_cell(report.location.district_name or "")
        tz_name = sanitize_csv_cell(report.date_range.timezone)

        for rec in report.daily_records:
            # Preserves missing data as empty string, NEVER 0.0
            rf_str = f"{rec.rainfall_mm:.2f}" if rec.rainfall_mm is not None else ""
            row = [
                sanitize_csv_cell(rec.date),
                loc_name,
                district,
                rf_str,
                rec.observation_count,
                f"{rec.coverage_pct:.1f}",
                "true" if rec.is_rainy_day else "false",
                "true" if rec.is_partial_day else "false",
                sanitize_csv_cell(rec.reporting_window),
                tz_name,
            ]
            writer.writerow(row)

        return output.getvalue()

    @staticmethod
    def get_safe_filename(report: RainfallReportResponse) -> str:
        """Generate safe, sanitised filename for Content-Disposition."""
        slug = re.sub(r"[^a-zA-Z0-9_\-]+", "_", report.location.location_name.lower()).strip("_")
        return f"floodwatch_rainfall_{slug}_{report.date_range.start_date}_to_{report.date_range.end_date}.csv"
