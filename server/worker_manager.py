"""
Worker connection and subscription manager.
Maintains worker_id -> WebSocket and worker_id -> subscribed Controller WebSockets mappings.
Enforces complete isolation between workers.
"""

from __future__ import annotations
import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Set
from fastapi import WebSocket

from server.models import WorkerConnectionState, get_utc_iso
from shared.protocol import (
    STATUS_CONNECTED,
    STATUS_DISCONNECTED,
    MSG_WORKER_STATUS,
)
from shared.messages import create_worker_status, serialize_message

logger = logging.getLogger("server.worker_manager")


class WorkerManager:
    """
    Manages active Worker connections, lifecycle, and Controller subscriptions per worker_id.
    """
    def __init__(self) -> None:
        # worker_id -> active Worker WebSocket connection
        self._worker_connections: Dict[str, WebSocket] = {}
        # worker_id -> WorkerConnectionState metadata
        self._worker_states: Dict[str, WorkerConnectionState] = {}
        # worker_id -> Set of Controller WebSockets subscribed to this worker
        self._worker_subscribers: Dict[str, Set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def register_worker(
        self,
        worker_id: str,
        ws: WebSocket,
        capabilities: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Register or re-register a Worker connection under its unique worker_id.
        Replaces any existing stale socket for this worker_id cleanly.
        """
        async with self._lock:
            old_ws = self._worker_connections.get(worker_id)
            if old_ws and old_ws != ws:
                logger.info(f"Worker {worker_id} reconnected; closing previous socket")
                try:
                    await old_ws.close(code=1000, reason="Replaced by new worker connection")
                except Exception as e:
                    logger.debug(f"Error closing old socket for {worker_id}: {e}")

            self._worker_connections[worker_id] = ws
            self._worker_states[worker_id] = WorkerConnectionState(
                worker_id=worker_id,
                websocket=ws,
                status=STATUS_CONNECTED,
                capabilities=capabilities or {},
                connected_at=get_utc_iso(),
                last_seen_at=get_utc_iso(),
            )
            # Ensure subscriber set exists
            if worker_id not in self._worker_subscribers:
                self._worker_subscribers[worker_id] = set()

            logger.info(f"Worker registered: {worker_id} (active workers: {len(self._worker_connections)})")

        # Notify subscribed controllers of connection
        await self._notify_subscribers_status(worker_id, STATUS_CONNECTED)

    async def unregister_worker(self, worker_id: str, ws: Optional[WebSocket] = None) -> None:
        """
        Unregister a Worker on disconnect.
        Removes only this worker_id's socket, marks status disconnected,
        notifies subscribers, and leaves all other workers untouched.
        """
        subscribers_to_notify: Set[WebSocket] = set()
        async with self._lock:
            current_ws = self._worker_connections.get(worker_id)
            # Only unregister if the disconnecting socket matches the registered socket
            if ws is None or current_ws == ws:
                self._worker_connections.pop(worker_id, None)
                if worker_id in self._worker_states:
                    self._worker_states[worker_id].status = STATUS_DISCONNECTED
                    self._worker_states[worker_id].last_seen_at = get_utc_iso()
                subscribers_to_notify = set(self._worker_subscribers.get(worker_id, set()))
                logger.info(f"Worker disconnected: {worker_id} (remaining active: {len(self._worker_connections)})")

        # Send disconnected status to subscribed controllers
        if subscribers_to_notify:
            status_msg = serialize_message(
                create_worker_status(worker_id=worker_id, status=STATUS_DISCONNECTED)
            )
            for ctrl_ws in subscribers_to_notify:
                try:
                    await ctrl_ws.send_text(status_msg)
                except Exception as e:
                    logger.debug(f"Failed to notify controller {ctrl_ws} of {worker_id} disconnect: {e}")

    def get_worker_ws(self, worker_id: str) -> Optional[WebSocket]:
        """Get the active WebSocket connection for a given worker_id."""
        return self._worker_connections.get(worker_id)

    def is_worker_connected(self, worker_id: str) -> bool:
        """Check if a specific worker_id is currently connected."""
        return worker_id in self._worker_connections

    def get_worker_state(self, worker_id: str) -> Optional[WorkerConnectionState]:
        """Get the connection state for a given worker_id."""
        return self._worker_states.get(worker_id)

    def add_subscriber(self, worker_id: str, controller_ws: WebSocket) -> None:
        """Subscribe a Controller WebSocket to updates from a specific worker_id."""
        if worker_id not in self._worker_subscribers:
            self._worker_subscribers[worker_id] = set()
        self._worker_subscribers[worker_id].add(controller_ws)
        logger.debug(f"Controller subscribed to {worker_id}. Total subscribers: {len(self._worker_subscribers[worker_id])}")

    def remove_subscriber(self, worker_id: str, controller_ws: WebSocket) -> None:
        """Unsubscribe a Controller WebSocket from a specific worker_id."""
        if worker_id in self._worker_subscribers:
            self._worker_subscribers[worker_id].discard(controller_ws)

    def remove_subscriber_from_all(self, controller_ws: WebSocket) -> None:
        """Remove a Controller WebSocket from all worker subscription sets upon disconnect."""
        for subscribers in self._worker_subscribers.values():
            subscribers.discard(controller_ws)

    def get_subscribers(self, worker_id: str) -> Set[WebSocket]:
        """Get all Controller WebSockets subscribed to a given worker_id."""
        return set(self._worker_subscribers.get(worker_id, set()))

    def update_dom_version(self, worker_id: str, dom_version: int) -> None:
        """Update the tracked DOM version for a worker."""
        state = self._worker_states.get(worker_id)
        if state:
            state.dom_version = dom_version
            state.last_seen_at = get_utc_iso()

    async def update_status(self, worker_id: str, status: str, dom_version: Optional[int] = None) -> None:
        """Update worker status and notify its subscribers."""
        state = self._worker_states.get(worker_id)
        if state:
            state.status = status
            state.last_seen_at = get_utc_iso()
            if dom_version is not None:
                state.dom_version = dom_version
        await self._notify_subscribers_status(worker_id, status, dom_version)

    async def _notify_subscribers_status(
        self,
        worker_id: str,
        status: str,
        dom_version: Optional[int] = None,
    ) -> None:
        """Broadcast status update to subscribed controllers for this worker_id only."""
        subscribers = self.get_subscribers(worker_id)
        if not subscribers:
            return
        msg = serialize_message(
            create_worker_status(worker_id=worker_id, status=status, dom_version=dom_version)
        )
        for ctrl_ws in subscribers:
            try:
                await ctrl_ws.send_text(msg)
            except Exception as e:
                logger.debug(f"Error sending status to subscriber: {e}")

    def get_all_worker_statuses(self) -> List[Dict[str, Any]]:
        """Return a list of status summaries for all known workers."""
        result = []
        for worker_id, state in self._worker_states.items():
            result.append({
                "worker_id": worker_id,
                "status": state.status,
                "dom_version": state.dom_version,
                "connected": worker_id in self._worker_connections,
                "capabilities": state.capabilities,
                "connected_at": state.connected_at,
                "last_seen_at": state.last_seen_at,
            })
        return result

    def should_throttle_snapshot(self, worker_id: str) -> bool:
        """
        Check if a snapshot from this worker should be rate-limited.
        Returns True if the snapshot should be dropped (too fast), False if allowed.
        """
        state = self._worker_states.get(worker_id)
        if not state or state.min_snapshot_interval_ms <= 0:
            return False

        now = time.monotonic()
        elapsed_ms = (now - state.last_snapshot_at) * 1000
        if elapsed_ms < state.min_snapshot_interval_ms:
            logger.debug(
                f"Throttling snapshot from '{worker_id}': "
                f"{elapsed_ms:.0f}ms < {state.min_snapshot_interval_ms}ms minimum interval"
            )
            return True
        return False

    def record_snapshot(self, worker_id: str) -> None:
        """Record the timestamp of an accepted snapshot for rate-limiting."""
        state = self._worker_states.get(worker_id)
        if state:
            state.last_snapshot_at = time.monotonic()

    def set_throttle_profile(
        self,
        worker_id: str,
        profile_name: str,
        min_snapshot_interval_ms: int,
    ) -> None:
        """Update the active throttle profile for a worker."""
        state = self._worker_states.get(worker_id)
        if state:
            state.active_throttle_profile = profile_name
            state.min_snapshot_interval_ms = min_snapshot_interval_ms
            logger.info(
                f"Worker '{worker_id}' throttle profile set to '{profile_name}' "
                f"(min_interval={min_snapshot_interval_ms}ms)"
            )
