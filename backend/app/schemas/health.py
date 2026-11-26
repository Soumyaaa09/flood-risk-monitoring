from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field


class DatabaseHealth(BaseModel):
    """Database connectivity status schema."""

    status: str = Field(
        ...,
        description="Connectivity status: 'connected', 'disconnected', 'timeout', or 'not_configured'",
    )
    provider: Optional[str] = Field(
        None, description="Database gateway/provider type (e.g. 'supabase_postgrest')"
    )
    table_verified: Optional[str] = Field(
        None, description="Public reference table verified during connectivity test"
    )
    message: Optional[str] = Field(
        None, description="Descriptive status or sanitised error summary"
    )


class HealthResponse(BaseModel):
    """Health check response schema."""

    status: str = Field(default="ok", description="Operational status of the API ('ok' or 'degraded')")
    service: str = Field(..., description="Service identifier")
    version: str = Field(..., description="Application version")
    environment: str = Field(..., description="Runtime environment")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Current server timestamp in UTC",
    )
    database: DatabaseHealth = Field(
        ...,
        description="Database connectivity health reporting without credential exposure",
    )
    prototype_notice: str = Field(
        default="FloodWatch is a research and decision-support prototype, NOT an official emergency-warning service.",
        description="Disclaimer on prototype usage",
    )
