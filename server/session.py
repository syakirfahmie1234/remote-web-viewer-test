"""
Controller session tracking and access control management.
"""

from __future__ import annotations
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Set
from fastapi import WebSocket

from server.models import get_utc_iso

logger = logging.getLogger("server.session")

@dataclass
class ControllerSession:
    """Represents an authenticated Controller session."""
    session_id: str
    websocket: WebSocket
    client_id: Optional[str]
    authorized_worker_ids: Optional[FrozenSet[str]]
    subscribed_worker_ids: Set[str] = field(default_factory=set)
    created_at: float = field(default_factory=time.monotonic)
    last_snapshot_at: Dict[str, float] = field(default_factory=dict) # worker_id -> timestamp
    last_activity_at: float = field(default_factory=time.monotonic)
    created_iso: str = field(default_factory=get_utc_iso)


class SessionManager:
    """
    Manages active Controller sessions, idle timeouts, and per-session access control.
    """
    def __init__(self) -> None:
        self._sessions: Dict[WebSocket, ControllerSession] = {}

    def create_session(
        self,
        ws: WebSocket,
        client_id: Optional[str] = None,
        authorized_workers: Optional[FrozenSet[str]] = None,
    ) -> ControllerSession:
        """Create and track a new session for a connected controller."""
        session_id = f"sess-{uuid.uuid4().hex[:12]}"
        session = ControllerSession(
            session_id=session_id,
            websocket=ws,
            client_id=client_id,
            authorized_worker_ids=authorized_workers,
        )
        self._sessions[ws] = session
        logger.debug(f"Session {session_id} created for client {client_id or 'anonymous'}")
        return session

    def get_session(self, ws: WebSocket) -> Optional[ControllerSession]:
        """Get the session for a given WebSocket."""
        return self._sessions.get(ws)

    def touch_session(self, ws: WebSocket) -> None:
        """Update the last activity timestamp for a session."""
        session = self._sessions.get(ws)
        if session:
            session.last_activity_at = time.monotonic()

    def destroy_session(self, ws: WebSocket) -> Optional[ControllerSession]:
        """Remove a session from tracking."""
        session = self._sessions.pop(ws, None)
        if session:
            logger.debug(f"Session {session.session_id} destroyed")
        return session

    def get_expired_sessions(self, timeout_seconds: int) -> List[ControllerSession]:
        """Return all sessions that have been idle longer than timeout_seconds."""
        if timeout_seconds <= 0:
            return []
        
        now = time.monotonic()
        expired = []
        for session in self._sessions.values():
            if now - session.last_activity_at > timeout_seconds:
                expired.append(session)
        return expired

    def is_authorized(self, ws: WebSocket, worker_id: str) -> bool:
        """Check if the session is authorized to access the given worker_id."""
        session = self._sessions.get(ws)
        if not session:
            return False
            
        # None means access to all workers (backward compatibility)
        if session.authorized_worker_ids is None:
            return True
            
        return worker_id in session.authorized_worker_ids
