"""FastAPI routes for Historical Rainfall Reporting & Data Export (Phase 2.8).

Endpoints:
- GET /api/v1/reports/rainfall: JSON report payload with summary, daily totals, and disclosures.
- GET /api/v1/reports/rainfall.csv: RFC 4180 CSV export with formula injection protection.
"""

import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.core.sanitizer import sanitize_error_message
from app.db.supabase_client import SupabaseClientError
from app.schemas.report import RainfallReportResponse
from app.services.reporting_service import ReportingService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reports", tags=["Historical Rainfall Reports & Export"])


def get_reporting_service() -> ReportingService:
    return ReportingService()


@router.get(
    "/rainfall",
    response_model=RainfallReportResponse,
    summary="Generate Historical Rainfall Report (JSON)",
    description=(
        "Generate a structured historical rainfall report for a selected pilot location "
        "(Paradeep, Cuttack, Kendrapara). Evaluated in Asia/Kolkata (IST). "
        "Preserves missing observations as null rather than falsely reporting zero."
    ),
)
async def get_rainfall_report(
    location_id: str = Query(..., description="Pilot monitoring location UUID"),
    days: int = Query(7, ge=1, le=90, description="Reporting window in days (default: 7)"),
    start_date: Optional[str] = Query(None, description="Optional start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="Optional end date (YYYY-MM-DD)"),
    service: ReportingService = Depends(get_reporting_service),
) -> RainfallReportResponse:
    try:
        return await service.generate_rainfall_report(
            location_id=location_id,
            days=days,
            start_date=start_date,
            end_date=end_date,
        )
    except ValueError as val_err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_report_request", "message": str(val_err)},
        )
    except SupabaseClientError as sb_err:
        logger.error(
            "Database error in rainfall report: %s",
            sanitize_error_message(sb_err.message) or sb_err.__class__.__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "database_error", "message": "Failed to retrieve historical observations."},
        )
    except Exception as exc:
        logger.error("Unexpected error generating rainfall report: %s", exc.__class__.__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "report_generation_error", "message": "Failed to generate rainfall report."},
        )


@router.get(
    "/rainfall.csv",
    summary="Export Historical Rainfall Data (CSV)",
    description=(
        "Download an RFC 4180-compliant CSV export of daily rainfall observations. "
        "Includes formula injection protection for spreadsheet security, explicit 'rainfall_mm' units, "
        "and empty strings for missing observations."
    ),
)
async def export_rainfall_csv(
    location_id: str = Query(..., description="Pilot monitoring location UUID"),
    days: int = Query(7, ge=1, le=90, description="Reporting window in days (default: 7)"),
    start_date: Optional[str] = Query(None, description="Optional start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="Optional end date (YYYY-MM-DD)"),
    service: ReportingService = Depends(get_reporting_service),
) -> Response:
    try:
        report = await service.generate_rainfall_report(
            location_id=location_id,
            days=days,
            start_date=start_date,
            end_date=end_date,
        )
        csv_text = service.generate_rainfall_csv(report)
        safe_filename = service.get_safe_filename(report)

        # Include UTF-8 BOM (\ufeff) for compatibility with Microsoft Excel
        bom_csv = "\ufeff" + csv_text

        return Response(
            content=bom_csv.encode("utf-8"),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{safe_filename}"',
                "Cache-Control": "no-cache, no-store, must-revalidate",
            },
        )
    except ValueError as val_err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_export_request", "message": str(val_err)},
        )
    except SupabaseClientError as sb_err:
        logger.error(
            "Database error in rainfall CSV export: %s",
            sanitize_error_message(sb_err.message) or sb_err.__class__.__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "database_error", "message": "Failed to retrieve historical observations for CSV export."},
        )
    except Exception as exc:
        logger.error("Unexpected error in rainfall CSV export: %s", exc.__class__.__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "csv_export_error", "message": "Failed to export rainfall CSV data."},
        )
