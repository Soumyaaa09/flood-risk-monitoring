"""Sanitization and redaction utilities for security hardening.

Prevents credential leakage, sensitive database strings, table/constraint names,
and internal stack traces from appearing in API responses, logs, or audit records.
"""

import re
from typing import Any, Dict, Optional

SENSITIVE_PATTERNS = [
    re.compile(r"\bsb_secret_[A-Za-z0-9._~\-]+\b"),
    re.compile(r"\bsb_publishable_[A-Za-z0-9._~\-]+\b"),
    re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE),
    re.compile(r"apikey[:=]\s*[A-Za-z0-9\-._~+/]+", re.IGNORECASE),
    re.compile(r"eyJ[A-Za-z0-9\-_=]+\.[A-Za-z0-9\-_=]+\.?[A-Za-z0-9\-_.+/=]*"),  # JWTs
    re.compile(r"postgresql://[^\s\'\"<>]+", re.IGNORECASE),
    re.compile(r"postgres://[^\s\'\"<>]+", re.IGNORECASE),
    re.compile(r"https?://[^\s\'\"<>]+", re.IGNORECASE),
    re.compile(r"password[:=\s][^\s\'\",;]+", re.IGNORECASE),
    re.compile(r"secret[:=\s][^\s\'\",;]+", re.IGNORECASE),
    re.compile(r"token[:=\s][^\s\'\",;]+", re.IGNORECASE),
]

DATABASE_DETAIL_PATTERNS = [
    re.compile(r"relation\s+[\"']?[a-zA-Z0-9_.]+[\"']?\s+does not exist", re.IGNORECASE),
    re.compile(r"column\s+[\"']?[a-zA-Z0-9_.]+[\"']?\s+does not exist", re.IGNORECASE),
    re.compile(r"duplicate key value violates unique constraint\s+[\"']?[a-zA-Z0-9_.]+[\"']?", re.IGNORECASE),
    re.compile(r"violates foreign key constraint\s+[\"']?[a-zA-Z0-9_.]+[\"']?", re.IGNORECASE),
    re.compile(r"violates check constraint\s+[\"']?[a-zA-Z0-9_.]+[\"']?", re.IGNORECASE),
]


def sanitize_error_message(message: Optional[str]) -> Optional[str]:
    """Sanitize error message to prevent leakage of credentials, URLs, or internal database schemas.
    
    Returns a safe diagnostic message truncated to 255 characters.
    """
    if not message:
        return None

    cleaned = str(message).strip()

    # Redact sensitive credentials and URLs
    for pattern in SENSITIVE_PATTERNS:
        cleaned = pattern.sub("[REDACTED]", cleaned)

    # Redact raw database schema and constraint violation traces
    for pattern in DATABASE_DETAIL_PATTERNS:
        cleaned = pattern.sub("database constraint or schema violation", cleaned)

    # Strip newlines and carriage returns
    cleaned = " ".join(cleaned.split())

    if len(cleaned) > 255:
        cleaned = cleaned[:252] + "..."

    return cleaned


def redact_headers(headers: Dict[str, str]) -> Dict[str, str]:
    """Redact authorization, apikey, and cookie headers for safe logging."""
    redacted = {}
    for k, v in headers.items():
        if k.lower() in ["authorization", "apikey", "x-ingestion-token", "cookie", "set-cookie", "x-api-key"]:
            redacted[k] = "[REDACTED]"
        elif isinstance(v, str) and (
            "sb_secret_" in v or "sb_publishable_" in v or "Bearer " in v or "eyJ" in v
        ):
            redacted[k] = sanitize_error_message(v) or "[REDACTED]"
        else:
            redacted[k] = v
    return redacted
