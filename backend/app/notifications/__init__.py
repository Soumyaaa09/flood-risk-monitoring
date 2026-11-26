"""Notifications package.
Provides foundational interfaces and services for in-app alerts and notifications.
"""

from app.services.alert_service import AlertService, get_alert_service

__all__ = ["AlertService", "get_alert_service"]
