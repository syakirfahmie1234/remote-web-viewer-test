"""
Controller connection and subscription state manager.
Tracks controller WebSockets and their active worker_id subscriptions.
"""

from __future__ import annotations
import logging
from typing import FrozenSet, Optional, Set
from fastapi import WebSocket

from server.session import SessionManager, ControllerSession
from server.audit import log_audit_event

logger = logging.getLogger("server.controller_manager")


class ControllerManager:
    """
    Manages active Controller connections and tracks which worker_id each controller is currently viewing.
    Delegates to SessionManager for underlying session state and access control.
    """
    def __init__(self, session_mgr: SessionManager) -> None:
        self.session_mgr = session_mgr

    def register_controller(
        self,
        ws: WebSocket,
        client_id: Optional[str] = None,
        subscribed_worker_ids: Optional[Set[str]] = None,
        authorized_workers: Optional[FrozenSet[str]] = None,
    ) -> ControllerSession:
        """Register a connected controller socket and create a session."""
        session = self.session_mgr.create_session(
            ws=ws, 
            client_id=client_id, 
            authorized_workers=authorized_workers
        )
        
        if subscribed_worker_ids:
            self.set_subscriptions(ws, subscribed_worker_ids)
            
        logger.info(f"Controller registered: {client_id or 'anonymous'} (session: {session.session_id})")
        return session

    def set_subscriptions(self, ws: WebSocket, worker_ids: Set[str]) -> Set[str]:
        """
        Update the worker_id subscriptions for a controller.
        Rejects unauthorized worker_ids and emits audit logs.
        Returns the previous set of subscribed worker_ids.
        """
        session = self.session_mgr.get_session(ws)
        if not session:
            return set()
            
        old_worker_ids = session.subscribed_worker_ids
        
        # Filter and validate authorized workers
        valid_workers = set()
        for w_id in worker_ids:
            if self.session_mgr.is_authorized(ws, w_id):
                valid_workers.add(w_id)
            else:
                log_audit_event(
                    event="ACCESS_DENIED",
                    session_id=session.session_id,
                    client_id=session.client_id,
                    worker_id=w_id,
                    reason="Controller attempted to subscribe to unauthorized worker",
                )
                
        session.subscribed_worker_ids = valid_workers
        self.session_mgr.touch_session(ws)
        
        if valid_workers != old_worker_ids:
            log_audit_event(
                event="SUBSCRIPTION_CHANGE",
                session_id=session.session_id,
                client_id=session.client_id,
                extra={
                    "added": list(valid_workers - old_worker_ids),
                    "removed": list(old_worker_ids - valid_workers),
                }
            )
            
        return old_worker_ids

    def get_subscriptions(self, ws: WebSocket) -> Set[str]:
        """Get the current worker_ids a controller is subscribed to."""
        session = self.session_mgr.get_session(ws)
        return session.subscribed_worker_ids if session else set()

    def unregister_controller(self, ws: WebSocket) -> Set[str]:
        """
        Unregister a disconnecting controller socket.
        Returns the last subscribed worker_ids for subscriber cleanup.
        """
        session = self.session_mgr.destroy_session(ws)
        if session:
            logger.info(f"Controller disconnected: {session.client_id or 'anonymous'} (session: {session.session_id})")
            log_audit_event(
                event="SESSION_END",
                session_id=session.session_id,
                client_id=session.client_id,
                reason="disconnect",
            )
            return session.subscribed_worker_ids
        return set()

    def is_connected(self, ws: WebSocket) -> bool:
        """Check if a controller socket is actively tracked."""
        return self.session_mgr.get_session(ws) is not None
