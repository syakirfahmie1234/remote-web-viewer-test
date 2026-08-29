"""
Server-side data models for connection state tracking and routing metadata.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Set
from fastapi import WebSocket

from shared.protocol import STATUS_CONNECTED


def get_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class WorkerConnectionState:
    """Represents a connected Worker instance session."""
    worker_id: str
    websocket: WebSocket
    status: str = STATUS_CONNECTED
    capabilities: Dict[str, Any] = field(default_factory=dict)
    dom_version: int = 0
    connected_at: str = field(default_factory=get_utc_iso)
    last_seen_at: str = field(default_factory=get_utc_iso)
    # Throttle tracking
    active_throttle_profile: str = "balanced"
    last_snapshot_at: float = 0.0  # monotonic time of last snapshot
    min_snapshot_interval_ms: int = 0


@dataclass
class ControllerConnectionState:
    """Represents a connected Controller instance session."""
    websocket: WebSocket
    client_id: Optional[str] = None
    subscribed_worker_ids: Set[str] = field(default_factory=set)
    connected_at: str = field(default_factory=get_utc_iso)
    last_seen_at: str = field(default_factory=get_utc_iso)
