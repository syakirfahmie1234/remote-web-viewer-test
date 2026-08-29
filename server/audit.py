"""
Structured audit logger for authentication and session events.
Emits JSON-serializable log dictionaries.
"""

from __future__ import annotations
import json
import logging
from typing import Any, Dict, Optional
from server.models import get_utc_iso

logger = logging.getLogger("server.audit")

def log_audit_event(
    event: str,
    session_id: Optional[str] = None,
    client_id: Optional[str] = None,
    worker_id: Optional[str] = None,
    ip_address: Optional[str] = None,
    role: Optional[str] = None,
    reason: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Emit a structured audit event."""
    data: Dict[str, Any] = {
        "event": event,
        "timestamp": get_utc_iso(),
    }
    if session_id: data["session_id"] = session_id
    if client_id: data["client_id"] = client_id
    if worker_id: data["worker_id"] = worker_id
    if ip_address: data["ip_address"] = ip_address
    if role: data["role"] = role
    if reason: data["reason"] = reason
    if extra: data["extra"] = extra

    # Log as JSON string for easy ingest
    logger.info(f"[AUDIT] {json.dumps(data)}")
