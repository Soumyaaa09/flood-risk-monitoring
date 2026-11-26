"""Pydantic schemas for FloodWatch Risk Alerts & In-App Notification System (Phase 2.7).

Strict Scientific Disclaimers:
- Alerts represent surface-water runoff potential from evaluated rainfall telemetry.
- They do NOT represent confirmed riverine flooding or statutory evacuation orders.
- Missing data is NEVER treated as zero rainfall or low risk.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

DEFAULT_ALERT_DISCLAIMER = (
    "FloodWatch provides experimental rainfall-based decision support. "
    "It is not an official flood warning service. Follow IMD and OSDMA for statutory warnings."
)


class AlertType(str, Enum):
    """Categorical alert event classifications."""
    MODERATE_RAINFALL_RISK = "MODERATE_RAINFALL_RISK"
    HIGH_RAINFALL_RISK = "HIGH_RAINFALL_RISK"
    VERY_HIGH_RAINFALL_RISK = "VERY_HIGH_RAINFALL_RISK"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
    DATA_STALE = "DATA_STALE"
    RISK_REDUCED = "RISK_REDUCED"


class AlertSeverity(str, Enum):
    """Operational urgency level of the alert.
    
    Includes DATA_WARNING to cleanly segregate telemetry/sensor quality issues
    from hydrological rainfall warnings (Remediation ALT-01).
    """
    INFO = "INFO"
    DATA_WARNING = "DATA_WARNING"
    WARNING = "WARNING"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class AlertStatus(str, Enum):
    """Lifecycle state of an alert."""
    NEW = "NEW"
    ACTIVE = "ACTIVE"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"


class AlertEvidence(BaseModel):
    """Structured meteorological and quality evidence backing the alert."""
    risk_category: str
    confidence: str
    recent_observed_mm: Optional[float] = None
    forecast_6h_mm: Optional[float] = None
    forecast_24h_mm: Optional[float] = None
    observation_freshness: str = "unavailable"
    observation_age_minutes: Optional[float] = None
    forecast_completeness_pct: Optional[float] = None
    antecedent_category: Optional[str] = None
    trigger_reason: str = ""


class AlertResponse(BaseModel):
    """Public representation of an in-app risk alert or notification."""
    id: str
    location_id: str
    location_name: Optional[str] = None
    district_name: Optional[str] = None
    alert_type: AlertType
    severity: AlertSeverity
    status: AlertStatus
    title: str
    description: str
    evidence: Dict[str, Any] = Field(default_factory=dict)
    # Internal fingerprint is excluded from public serialization
    fingerprint: Optional[str] = Field(default=None, exclude=True)
    created_at: datetime
    updated_at: datetime
    acknowledged_at: Optional[datetime] = None
    acknowledged_by: Optional[str] = None
    resolved_at: Optional[datetime] = None
    resolved_reason: Optional[str] = None
    disclaimer: str = DEFAULT_ALERT_DISCLAIMER


class AlertAcknowledgeRequest(BaseModel):
    """Request payload for operator alert acknowledgement."""
    acknowledged_by: Optional[str] = Field(
        default="duty_officer",
        description="Identifier or role of the operator acknowledging the alert",
    )


class AlertResolveRequest(BaseModel):
    """Request payload for manual operator alert resolution."""
    resolved_reason: str = Field(
        ...,
        min_length=3,
        max_length=500,
        description="Operational justification for resolving the alert (e.g., ground inspection confirmed normal conditions)",
    )
    resolved_by: Optional[str] = Field(
        default=None,
        max_length=100,
        description="Optional operator role or identifier providing context",
    )


class AlertsListResponse(BaseModel):
    """Paginated list of alerts with lifecycle counts."""
    total: int
    alerts: List[AlertResponse]
    active_count: int = 0
    acknowledged_count: int = 0
    resolved_count: int = 0
    disclaimer: str = DEFAULT_ALERT_DISCLAIMER


class AlertSummaryResponse(BaseModel):
    """High-level operational summary of active alerts for dashboard and badge indicators."""
    total_active_alerts: int
    total_acknowledged_alerts: int
    highest_active_severity: Optional[AlertSeverity] = None
    severity_counts: Dict[str, int] = Field(default_factory=dict)
    location_active_counts: Dict[str, int] = Field(default_factory=dict)
    recent_alerts: List[AlertResponse] = Field(default_factory=list)
    disclaimer: str = DEFAULT_ALERT_DISCLAIMER
