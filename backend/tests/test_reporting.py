"""Automated Unit and Integration Tests for Historical Rainfall Reporting & Data Export (Phase 2.8).

Verification Coverage (14 Core Scenarios):
1. Correct report generation (JSON payload structure & required fields).
2. Correct IST reporting dates (Asia/Kolkata timezone formatting).
3. Daily aggregation consistency (retains HistoricalRainfallAnalyzer metrics).
4. Missing observations preserved as missing (None in JSON, empty string in CSV; never zero).
5. Forecasts strictly excluded from historical data (only in separate risk outlook).
6. Correct units and column names (rainfall_mm, coverage_pct, etc.).
7. RFC 4180 CSV escaping and CRLF line terminators.
8. Spreadsheet formula injection protection (sanitize_csv_cell with =, +, -, @, \\t, \\r).
9. Invalid location handling (unknown location returns 400).
10. Invalid date handling (start_date > end_date returns 400).
11. Empty data handling (clean fallback without unhandled exceptions).
12. Report endpoint access controls (public GET, 405 on POST/DELETE).
13. Printable report content and statutory prototype disclosures.
14. Large date range protection (>90 days rejected with 400).
"""

from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
import pytest
from httpx import AsyncClient

from app.api.v1.routes.reports import get_reporting_service
from app.main import app
from app.risk_engine.historical_analyzer import IST_TIMEZONE
from app.schemas.ingestion import FreshnessState
from app.schemas.monitoring import QualityStatus
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
from app.schemas.risk import (
    AntecedentCategory,
    AntecedentRainfallIndicator,
    DailyRainfallRecord,
    DailyReportingWindow,
    HistoricalRainfallContext,
    RainfallRiskAssessmentResponse,
    RainfallTrend,
    RiskCategory,
    RiskConfidence,
)
from app.services.reporting_service import (
    ReportingService,
    sanitize_csv_cell,
)

MOCK_PILOT_LOCATIONS = [
    {
        "id": "11111111-1111-1111-1111-111111111111",
        "district_id": "d1",
        "name": "Paradeep Weather Grid Point",
        "location_type": "virtual_weather_point",
        "latitude": 20.3160,
        "longitude": 86.6110,
        "districts": {"name": "Jagatsinghpur"},
    },
    {
        "id": "22222222-2222-2222-2222-222222222222",
        "district_id": "d2",
        "name": "Cuttack Weather Grid Point",
        "location_type": "virtual_weather_point",
        "latitude": 20.4625,
        "longitude": 85.8830,
        "districts": {"name": "Cuttack"},
    },
    {
        "id": "33333333-3333-3333-3333-333333333333",
        "district_id": "d3",
        "name": "Kendrapara Weather Grid Point",
        "location_type": "virtual_weather_point",
        "latitude": 20.5017,
        "longitude": 86.4223,
        "districts": {"name": "Kendrapara"},
    },
]


def make_mock_history_context(location_id: str, days: int = 7) -> HistoricalRainfallContext:
    """Generate realistic mock historical context for reporting tests."""
    ref_time = datetime(2026, 11, 22, 10, 0, 0, tzinfo=timezone.utc)
    ist_ref = ref_time.astimezone(IST_TIMEZONE)
    end_date = ist_ref.date()

    daily_totals = []
    for i in range(days):
        day_date = (end_date - timedelta(days=(days - 1 - i))).isoformat()
        is_partial = (i == days - 1)
        # One day has 0 observations to test missing-data preservation
        if i == 1:
            daily_totals.append(
                DailyRainfallRecord(
                    date=day_date,
                    rainfall_mm=0.0,
                    observation_count=0,
                    is_rainy_day=False,
                    coverage_pct=0.0,
                    is_partial_day=False,
                    reporting_window=DailyReportingWindow.IST_CALENDAR_DAY.value,
                )
            )
        else:
            rf = 12.5 if i % 2 == 0 else 1.0
            daily_totals.append(
                DailyRainfallRecord(
                    date=day_date,
                    rainfall_mm=rf,
                    observation_count=24,
                    is_rainy_day=(rf >= 2.5),
                    coverage_pct=100.0,
                    is_partial_day=is_partial,
                    reporting_window=DailyReportingWindow.IST_CALENDAR_DAY.value,
                )
            )

    return HistoricalRainfallContext(
        reference_time=ref_time,
        window_days=days,
        reporting_window=DailyReportingWindow.IST_CALENDAR_DAY,
        rainfall_24h_mm=12.5,
        rainfall_72h_mm=26.0,
        rainfall_7d_mm=51.5,
        daily_totals=daily_totals,
        consecutive_rainy_days=2,
        daily_trend=RainfallTrend.INCREASING,
        coverage_pct=85.7,
        missing_intervals_count=24,
        has_sufficient_coverage=True,
        data_gap_warning="Minor 1-day observation gap detected in historical period.",
    )


def make_mock_antecedent(location_id: str) -> AntecedentRainfallIndicator:
    return AntecedentRainfallIndicator(
        location_id=location_id,
        reference_time=datetime(2026, 11, 22, 10, 0, 0, tzinfo=timezone.utc),
        indicator_value_mm=42.5,
        indicator_category=AntecedentCategory.HIGH_ANTECEDENT,
        confidence=RiskConfidence.HIGH,
        explanation="Multi-day rainfall accumulation indicates high antecedent moisture index.",
        coverage_pct=95.0,
        missing_intervals_count=2,
    )


def make_mock_risk_assessment(location_id: str) -> RainfallRiskAssessmentResponse:
    return RainfallRiskAssessmentResponse(
        assessment_id="test_assessment_123",
        location_id=location_id,
        location_name="Paradeep Weather Grid Point",
        district_name="Jagatsinghpur",
        latitude=20.3160,
        longitude=86.6110,
        assessment_timestamp=datetime(2026, 11, 22, 10, 0, 0, tzinfo=timezone.utc),
        risk_category=RiskCategory.MODERATE,
        confidence=RiskConfidence.HIGH,
        recent_observed_rainfall_mm=12.5,
        forecast_6h_precipitation_mm=15.0,
        forecast_24h_precipitation_mm=35.0,
        observation_freshness=FreshnessState.FRESH,
        observation_age_minutes=15.0,
        forecast_completeness_pct=100.0,
        active_forecast_hours=24,
        quality_status=QualityStatus.PASS,
        threshold_version="2.4.1",
        is_stale=False,
        assessment_explanation="Moderate runoff potential driven by upcoming 35 mm forecast.",
        prototype_disclaimer=DEFAULT_REPORT_DISCLOSURE,
    )


# =============================================================================
# 1. Correct Report Generation (JSON Payload)
# =============================================================================

@pytest.mark.asyncio
async def test_correct_report_generation_json(client: AsyncClient):
    """Verify JSON rainfall report payload has all required structured fields."""
    mock_sb = AsyncMock()
    mock_sb.get_pilot_locations.return_value = MOCK_PILOT_LOCATIONS

    mock_risk = AsyncMock()
    mock_risk.get_location_history_context.return_value = (
        make_mock_history_context("11111111-1111-1111-1111-111111111111", days=7),
        make_mock_antecedent("11111111-1111-1111-1111-111111111111"),
    )
    mock_risk.get_location_rainfall_risk.return_value = make_mock_risk_assessment("11111111-1111-1111-1111-111111111111")

    service = ReportingService(supabase_client=mock_sb, risk_service=mock_risk)
    app.dependency_overrides[get_reporting_service] = lambda: service

    try:
        resp = await client.get("/api/v1/reports/rainfall?location_id=11111111-1111-1111-1111-111111111111&days=7")
        assert resp.status_code == 200
        data = resp.json()

        assert data["report_id"].startswith("rpt_")
        assert data["location"]["location_id"] == "11111111-1111-1111-1111-111111111111"
        assert data["location"]["location_name"] == "Paradeep Weather Grid Point"
        assert data["location"]["district_name"] == "Jagatsinghpur"
        assert data["location"]["latitude"] == 20.3160
        assert data["location"]["longitude"] == 86.6110
        assert len(data["daily_records"]) == 7
        assert data["summary"]["consecutive_rainy_days"] == 2
        assert data["summary"]["daily_trend"] == "INCREASING"
        assert data["antecedent_indicator"]["category"] == "HIGH_ANTECEDENT"
        assert data["risk_summary"]["risk_category"] == "MODERATE"
        assert len(data["data_quality_warnings"]) > 0
    finally:
        app.dependency_overrides.clear()


# =============================================================================
# 2. Correct IST Reporting Dates
# =============================================================================

@pytest.mark.asyncio
async def test_correct_ist_reporting_dates(client: AsyncClient):
    """Verify daily record dates and report timestamps adhere to Asia/Kolkata (IST)."""
    mock_sb = AsyncMock()
    mock_sb.get_pilot_locations.return_value = MOCK_PILOT_LOCATIONS

    mock_risk = AsyncMock()
    mock_risk.get_location_history_context.return_value = (
        make_mock_history_context("22222222-2222-2222-2222-222222222222", days=7),
        None,
    )
    mock_risk.get_location_rainfall_risk.return_value = None

    service = ReportingService(supabase_client=mock_sb, risk_service=mock_risk)
    app.dependency_overrides[get_reporting_service] = lambda: service

    try:
        resp = await client.get("/api/v1/reports/rainfall?location_id=22222222-2222-2222-2222-222222222222&days=7")
        assert resp.status_code == 200
        data = resp.json()

        assert data["date_range"]["timezone"] == "Asia/Kolkata"
        assert "IST" in data["generated_at_ist"]
        for rec in data["daily_records"]:
            assert rec["reporting_window"] == "IST_CALENDAR_DAY"
            parts = rec["date"].split("-")
            assert len(parts) == 3
            assert len(parts[0]) == 4
    finally:
        app.dependency_overrides.clear()


# =============================================================================
# 3. Daily Aggregation Consistency
# =============================================================================

@pytest.mark.asyncio
async def test_daily_aggregation_consistency():
    """Verify report summaries accurately preserve analyzer calculations."""
    mock_sb = AsyncMock()
    mock_sb.get_pilot_locations.return_value = MOCK_PILOT_LOCATIONS

    hist = make_mock_history_context("11111111-1111-1111-1111-111111111111", days=7)
    mock_risk = AsyncMock()
    mock_risk.get_location_history_context.return_value = (hist, None)
    mock_risk.get_location_rainfall_risk.return_value = None

    service = ReportingService(supabase_client=mock_sb, risk_service=mock_risk)
    report = await service.generate_rainfall_report(
        location_id="11111111-1111-1111-1111-111111111111",
        days=7,
    )

    assert report.summary.rainfall_24h_mm == hist.rainfall_24h_mm
    assert report.summary.rainfall_72h_mm == hist.rainfall_72h_mm
    assert report.summary.rainfall_7d_mm == hist.rainfall_7d_mm
    assert report.summary.consecutive_rainy_days == hist.consecutive_rainy_days
    assert report.summary.daily_trend == hist.daily_trend.value
    assert report.summary.window_coverage_pct == hist.coverage_pct


# =============================================================================
# 4. Missing Observations Preserved As Missing
# =============================================================================

@pytest.mark.asyncio
async def test_missing_observations_preserved_as_missing():
    """Verify missing observation day has None in JSON and empty string in CSV (never fake 0.0)."""
    mock_sb = AsyncMock()
    mock_sb.get_pilot_locations.return_value = MOCK_PILOT_LOCATIONS

    hist = make_mock_history_context("11111111-1111-1111-1111-111111111111", days=7)
    assert hist.daily_totals[1].observation_count == 0

    mock_risk = AsyncMock()
    mock_risk.get_location_history_context.return_value = (hist, None)
    mock_risk.get_location_rainfall_risk.return_value = None

    service = ReportingService(supabase_client=mock_sb, risk_service=mock_risk)

    # 1. Check JSON
    report = await service.generate_rainfall_report(
        location_id="11111111-1111-1111-1111-111111111111",
        days=7,
    )
    missing_day = report.daily_records[1]
    assert missing_day.rainfall_mm is None
    assert missing_day.observation_count == 0
    assert missing_day.notes == "No observations recorded"

    # 2. Check CSV export
    csv_text = service.generate_rainfall_csv(report)
    lines = csv_text.strip().split("\r\n")
    missing_row = lines[2].split(",")
    # Column 3 is rainfall_mm
    assert missing_row[3] == ""  # Empty string, NEVER "0" or "0.00"


# =============================================================================
# 5. Forecasts Strictly Excluded From Historical Data
# =============================================================================

@pytest.mark.asyncio
async def test_forecasts_strictly_excluded_from_history():
    """Verify daily records contain only historical telemetry; forecasts only in risk outlook."""
    mock_sb = AsyncMock()
    mock_sb.get_pilot_locations.return_value = MOCK_PILOT_LOCATIONS

    mock_risk = AsyncMock()
    mock_risk.get_location_history_context.return_value = (
        make_mock_history_context("11111111-1111-1111-1111-111111111111", days=7),
        None,
    )
    mock_risk.get_location_rainfall_risk.return_value = make_mock_risk_assessment("11111111-1111-1111-1111-111111111111")

    service = ReportingService(supabase_client=mock_sb, risk_service=mock_risk)
    report = await service.generate_rainfall_report(
        location_id="11111111-1111-1111-1111-111111111111",
        days=7,
    )

    for rec in report.daily_records:
        assert not hasattr(rec, "forecast_rainfall_mm")
        assert not hasattr(rec, "forecast_24h_precipitation_mm")

    assert report.risk_summary is not None
    assert report.risk_summary.forecast_24h_mm == 35.0


# =============================================================================
# 6. Correct CSV Headers and Units
# =============================================================================

@pytest.mark.asyncio
async def test_csv_export_headers_and_units(client: AsyncClient):
    """Verify CSV header specifies explicit units (rainfall_mm)."""
    mock_sb = AsyncMock()
    mock_sb.get_pilot_locations.return_value = MOCK_PILOT_LOCATIONS

    mock_risk = AsyncMock()
    mock_risk.get_location_history_context.return_value = (
        make_mock_history_context("11111111-1111-1111-1111-111111111111", days=7),
        None,
    )
    mock_risk.get_location_rainfall_risk.return_value = None

    service = ReportingService(supabase_client=mock_sb, risk_service=mock_risk)
    app.dependency_overrides[get_reporting_service] = lambda: service

    try:
        resp = await client.get("/api/v1/reports/rainfall.csv?location_id=11111111-1111-1111-1111-111111111111&days=7")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/csv")
        assert "attachment;" in resp.headers["content-disposition"]
        assert "floodwatch_rainfall_paradeep" in resp.headers["content-disposition"]

        content = resp.text
        assert content.startswith("\ufeff")
        first_line = content.lstrip("\ufeff").split("\r\n")[0]
        expected_headers = [
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
        ]
        for col in expected_headers:
            assert col in first_line
    finally:
        app.dependency_overrides.clear()


# =============================================================================
# 7. RFC 4180 CSV Escaping
# =============================================================================

def test_rfc_4180_csv_escaping():
    """Verify CSV escaping and CRLF line terminators."""
    report = RainfallReportResponse(
        report_id="rpt_test123",
        generated_at_utc=datetime.now(timezone.utc),
        generated_at_ist="2026-11-22 10:00:00 IST",
        location=ReportLocationInfo(
            location_id="loc_1",
            location_name="Paradeep, Port Area",
            district_name='Jagatsinghpur "Coastal" Area',
            latitude=20.3160,
            longitude=86.6110,
        ),
        date_range=DateRangeInfo(
            start_date="2026-11-15",
            end_date="2026-11-21",
            window_days=7,
        ),
        summary=ReportAccumulationSummary(),
        daily_records=[
            ReportDailyRecord(
                date="2026-11-15",
                rainfall_mm=10.5,
                observation_count=24,
                coverage_pct=100.0,
                is_rainy_day=True,
            )
        ],
    )

    csv_output = ReportingService.generate_rainfall_csv(report)
    assert "\r\n" in csv_output
    assert '"Paradeep, Port Area"' in csv_output
    assert '""Coastal""' in csv_output


# =============================================================================
# 8. Spreadsheet Formula Injection Protection
# =============================================================================

def test_spreadsheet_formula_injection_protection():
    """Verify formula injection prefixes (=, +, -, @, \\t, \\r) are safely escaped with '."""
    dangerous_inputs = [
        "=SUM(A1:A10)",
        "+cmd|' /C calc'!A0",
        "-1+1",
        "@SUM(1,2)",
        "\tmalicious_tab",
        "\rmalicious_return",
    ]

    for inp in dangerous_inputs:
        sanitized = sanitize_csv_cell(inp)
        assert sanitized.startswith("'"), f"Failed to escape dangerous cell: {inp}"

    assert sanitize_csv_cell("Paradeep") == "Paradeep"
    assert sanitize_csv_cell("2026-11-22") == "2026-11-22"
    assert sanitize_csv_cell(12.5) == "12.5"
    assert sanitize_csv_cell(None) == ""


# =============================================================================
# 9. Invalid Location Handling
# =============================================================================

@pytest.mark.asyncio
async def test_invalid_location_handling(client: AsyncClient):
    """Verify requesting an unknown location returns 400 Bad Request."""
    mock_sb = AsyncMock()
    mock_sb.get_pilot_locations.return_value = MOCK_PILOT_LOCATIONS

    service = ReportingService(supabase_client=mock_sb)
    app.dependency_overrides[get_reporting_service] = lambda: service

    try:
        resp = await client.get("/api/v1/reports/rainfall?location_id=99999999-9999-9999-9999-999999999999&days=7")
        assert resp.status_code == 400
        data = resp.json()
        assert data["detail"]["error"] == "invalid_report_request"
        assert "not found" in data["detail"]["message"]
    finally:
        app.dependency_overrides.clear()


# =============================================================================
# 10. Invalid Date Handling
# =============================================================================

@pytest.mark.asyncio
async def test_invalid_date_handling(client: AsyncClient):
    """Verify start_date > end_date returns 400 Bad Request."""
    mock_sb = AsyncMock()
    mock_sb.get_pilot_locations.return_value = MOCK_PILOT_LOCATIONS

    service = ReportingService(supabase_client=mock_sb)
    app.dependency_overrides[get_reporting_service] = lambda: service

    try:
        resp = await client.get(
            "/api/v1/reports/rainfall?location_id=11111111-1111-1111-1111-111111111111"
            "&start_date=2026-11-25&end_date=2026-11-20"
        )
        assert resp.status_code == 400
        data = resp.json()
        assert data["detail"]["error"] == "invalid_report_request"
        assert "cannot be after end_date" in data["detail"]["message"]
    finally:
        app.dependency_overrides.clear()


# =============================================================================
# 11. Empty Data Handling
# =============================================================================

@pytest.mark.asyncio
async def test_empty_data_handling(client: AsyncClient):
    """Verify graceful handling when no observations exist for the period."""
    mock_sb = AsyncMock()
    mock_sb.get_pilot_locations.return_value = MOCK_PILOT_LOCATIONS

    empty_history = HistoricalRainfallContext(
        reference_time=datetime(2026, 11, 22, 10, 0, 0, tzinfo=timezone.utc),
        window_days=7,
        reporting_window=DailyReportingWindow.IST_CALENDAR_DAY,
        rainfall_24h_mm=None,
        rainfall_72h_mm=None,
        rainfall_7d_mm=None,
        daily_totals=[],
        consecutive_rainy_days=0,
        daily_trend=RainfallTrend.INSUFFICIENT_DATA,
        coverage_pct=0.0,
        missing_intervals_count=0,
        has_sufficient_coverage=False,
    )
    mock_risk = AsyncMock()
    mock_risk.get_location_history_context.return_value = (empty_history, None)
    mock_risk.get_location_rainfall_risk.return_value = None

    service = ReportingService(supabase_client=mock_sb, risk_service=mock_risk)
    app.dependency_overrides[get_reporting_service] = lambda: service

    try:
        resp = await client.get("/api/v1/reports/rainfall?location_id=11111111-1111-1111-1111-111111111111&days=7")
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"]["total_window_rainfall_mm"] is None
        assert data["summary"]["daily_trend"] == "INSUFFICIENT_DATA"
        assert len(data["daily_records"]) == 0
    finally:
        app.dependency_overrides.clear()


# =============================================================================
# 12. Report Endpoint Access Controls
# =============================================================================

@pytest.mark.asyncio
async def test_report_endpoint_access_control(client: AsyncClient):
    """Verify reports are public GET endpoints and reject mutation operations with 405."""
    post_resp = await client.post("/api/v1/reports/rainfall")
    assert post_resp.status_code == 405

    del_resp = await client.delete("/api/v1/reports/rainfall")
    assert del_resp.status_code == 405


# =============================================================================
# 13. Printable Report Content & Disclosures
# =============================================================================

@pytest.mark.asyncio
async def test_printable_report_content_and_disclosures(client: AsyncClient):
    """Verify statutory and scientific prototype disclosures are present."""
    mock_sb = AsyncMock()
    mock_sb.get_pilot_locations.return_value = MOCK_PILOT_LOCATIONS

    mock_risk = AsyncMock()
    mock_risk.get_location_history_context.return_value = (
        make_mock_history_context("33333333-3333-3333-3333-333333333333", days=7),
        None,
    )
    mock_risk.get_location_rainfall_risk.return_value = None

    service = ReportingService(supabase_client=mock_sb, risk_service=mock_risk)
    app.dependency_overrides[get_reporting_service] = lambda: service

    try:
        resp = await client.get("/api/v1/reports/rainfall?location_id=33333333-3333-3333-3333-333333333333&days=7")
        assert resp.status_code == 200
        data = resp.json()

        disclosures = data["methodology_and_disclosures"]
        assert "statutory_disclaimer" in disclosures
        assert "NOT an official measurement" in disclosures["statutory_disclaimer"]
        assert "virtual_grid_disclosure" in disclosures
        assert "Virtual meteorological grid point" in disclosures["virtual_grid_disclosure"]
        assert "2.5 mm depth" in disclosures["rainy_day_threshold"]
    finally:
        app.dependency_overrides.clear()


# =============================================================================
# 14. Large Date Range Protection (>90 Days)
# =============================================================================

@pytest.mark.asyncio
async def test_large_date_range_protection(client: AsyncClient):
    """Verify date ranges exceeding 90 days are strictly rejected with 400 Bad Request."""
    mock_sb = AsyncMock()
    mock_sb.get_pilot_locations.return_value = MOCK_PILOT_LOCATIONS

    service = ReportingService(supabase_client=mock_sb)
    app.dependency_overrides[get_reporting_service] = lambda: service

    try:
        # Query param days > 90 triggers FastAPI query validation (422) or service validation (400)
        resp_days = await client.get("/api/v1/reports/rainfall?location_id=11111111-1111-1111-1111-111111111111&days=95")
        assert resp_days.status_code in (400, 422)

        # Explicit date range > 90 days
        resp_range = await client.get(
            "/api/v1/reports/rainfall?location_id=11111111-1111-1111-1111-111111111111"
            "&start_date=2026-01-01&end_date=2026-05-01"
        )
        assert resp_range.status_code == 400
        data = resp_range.json()
        assert "exceeds maximum allowable reporting limit of 90 days" in data["detail"]["message"]
    finally:
        app.dependency_overrides.clear()


# =============================================================================
# 15. LOW-02: Sanitized 503 Error Propagation on Database Outage
# =============================================================================

@pytest.mark.asyncio
async def test_reports_endpoints_sanitize_and_return_503_on_supabase_error(client: AsyncClient, caplog):
    """Verify report JSON and CSV endpoints return HTTP 503 and redact secrets when Supabase fails."""
    from app.db.supabase_client import SupabaseClientError

    mock_sb = AsyncMock()
    mock_sb.get_pilot_locations.side_effect = SupabaseClientError(
        "Database error with apikey=sb_secret_secret123 and Bearer eyJsecret.jwt.token",
        status_code=503,
    )

    service = ReportingService(supabase_client=mock_sb)
    app.dependency_overrides[get_reporting_service] = lambda: service

    try:
        with caplog.at_level("ERROR"):
            resp_json = await client.get("/api/v1/reports/rainfall?location_id=11111111-1111-1111-1111-111111111111")
            resp_csv = await client.get("/api/v1/reports/rainfall.csv?location_id=11111111-1111-1111-1111-111111111111")

        assert resp_json.status_code == 503
        assert resp_csv.status_code == 503
        assert "sb_secret_" not in resp_json.text
        assert "eyJsecret" not in resp_csv.text
        assert "sb_secret_" not in caplog.text
        assert "eyJsecret" not in caplog.text
    finally:
        app.dependency_overrides.clear()
