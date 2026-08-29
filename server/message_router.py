"""
Message router for routing messages strictly by worker_id between Workers and Controllers.
Enforces that Worker A never receives messages intended for Worker B, and Controllers
only receive updates from the worker_id they are subscribed to.
"""

from __future__ import annotations
import logging
import time
from typing import Any, Dict, Optional, Union
from fastapi import WebSocket

from server.worker_manager import WorkerManager
from server.controller_manager import ControllerManager
from shared.protocol import (
    MSG_HELLO,
    MSG_AUTH,
    MSG_WORKER_REGISTER,
    MSG_CONTROLLER_REGISTER,
    MSG_WORKER_STATUS,
    MSG_COMMAND,
    MSG_COMMAND_RESULT,
    MSG_FULL_SNAPSHOT,
    MSG_DOM_UPDATE,
    MSG_RESYNC_REQUEST,
    MSG_ERROR,
    MSG_PING,
    MSG_PONG,
    STATUS_DISCONNECTED,
)
from shared.models import (
    BaseMessage,
    WorkerScopedMessage,
    CommandMessage,
    CommandResultMessage,
    FullSnapshotMessage,
    DomUpdateMessage,
    ResyncRequestMessage,
    WorkerStatusMessage,
    ErrorMessage,
    PingMessage,
    PongMessage,
    ControllerRegisterMessage,
    ThrottleConfigMessage,
    BrowserConfigMessage,
)
from shared.messages import (
    parse_message,
    serialize_message,
    create_error,
    create_pong,
    create_worker_status,
    ProtocolError,
)

from server.session import SessionManager
from server.audit import log_audit_event

logger = logging.getLogger("server.message_router")


class MessageRouter:
    """
    Core relay routing engine. Routes messages strictly by worker_id.
    """
    def __init__(self, worker_mgr: WorkerManager, controller_mgr: ControllerManager, session_mgr: SessionManager) -> None:
        self.worker_mgr = worker_mgr
        self.controller_mgr = controller_mgr
        self.session_mgr = session_mgr

    async def handle_controller_message(
        self,
        ws: WebSocket,
        raw_msg: Union[str, bytes, Dict[str, Any]],
    ) -> None:
        """
        Handle and route an inbound message from a Controller WebSocket.
        """
        try:
            msg = parse_message(raw_msg)
        except ProtocolError as e:
            logger.warning(f"Protocol error from controller: {e}")
            err = serialize_message(create_error(code="PROTOCOL_ERROR", detail=str(e)))
            await ws.send_text(err)
            return
        except Exception as e:
            logger.error(f"Unexpected parse error from controller: {e}")
            err = serialize_message(create_error(code="MALFORMED_MESSAGE", detail=str(e)))
            await ws.send_text(err)
            return

        msg_type = msg.type
        
        # Keep session alive on any incoming message
        self.session_mgr.touch_session(ws)

        # 1. CONTROLLER_REGISTER / Subscription management
        if isinstance(msg, ControllerRegisterMessage):
            subs = set()
            if msg.subscribed_worker_id:
                subs.add(msg.subscribed_worker_id)
            if msg.subscribed_worker_ids:
                subs.update(msg.subscribed_worker_ids)
                
            old_workers = self.controller_mgr.set_subscriptions(ws, subs)
            
            for w_id in old_workers - subs:
                self.worker_mgr.remove_subscriber(w_id, ws)
                
            for w_id in subs - old_workers:
                self.worker_mgr.add_subscriber(w_id, ws)
                
            # For new subscriptions, send current status
            for w_id in (subs - old_workers):
                worker_state = self.worker_mgr.get_worker_state(w_id)
                if worker_state:
                    status_msg = serialize_message(
                        create_worker_status(
                            worker_id=w_id,
                            status=worker_state.status,
                            dom_version=worker_state.dom_version,
                        )
                    )
                else:
                    status_msg = serialize_message(
                        create_worker_status(
                            worker_id=w_id,
                            status=STATUS_DISCONNECTED,
                        )
                    )
                await ws.send_text(status_msg)

            # Send status for all other currently connected workers
            for w_status in self.worker_mgr.get_all_worker_statuses():
                if w_status["connected"]:
                    w_id = w_status["worker_id"]
                    # Skip if already sent above
                    if w_id in (subs - old_workers):
                        continue
                    if self.session_mgr.is_authorized(ws, w_id):
                        await ws.send_text(
                            serialize_message(
                                create_worker_status(
                                    worker_id=w_id,
                                    status=w_status["status"],
                                    dom_version=w_status["dom_version"],
                                )
                            )
                        )
            return

        # Common authorization check for worker-targeted messages
        if isinstance(msg, (CommandMessage, ResyncRequestMessage, ThrottleConfigMessage, BrowserConfigMessage)):
            worker_id = msg.worker_id
            if not self.session_mgr.is_authorized(ws, worker_id):
                session = self.session_mgr.get_session(ws)
                client_id = session.client_id if session else "unknown"
                sess_id = session.session_id if session else "unknown"
                log_audit_event(
                    event="ACCESS_DENIED",
                    session_id=sess_id,
                    client_id=client_id,
                    worker_id=worker_id,
                    reason=f"Controller attempted to send {msg.type} to unauthorized worker",
                )
                err_msg = serialize_message(
                    create_error(
                        code="UNAUTHORIZED",
                        detail=f"You are not authorized to interact with worker '{worker_id}'",
                        worker_id=worker_id,
                    )
                )
                await ws.send_text(err_msg)
                return

        # 2. COMMAND — Route strictly to target worker_id
        if isinstance(msg, CommandMessage):
            worker_id = msg.worker_id
            worker_ws = self.worker_mgr.get_worker_ws(worker_id)
            if not worker_ws:
                logger.warning(f"Command rejected: worker '{worker_id}' is not connected")
                err_msg = serialize_message(
                    create_error(
                        code="WORKER_NOT_CONNECTED",
                        detail=f"Target worker '{worker_id}' is currently offline or unregistered",
                        worker_id=worker_id,
                    )
                )
                await ws.send_text(err_msg)
                return

            # Forward exclusively to the target worker
            forward_payload = serialize_message(msg)
            try:
                await worker_ws.send_text(forward_payload)
                logger.debug(f"Routed command '{msg.command}' to worker '{worker_id}'")
            except Exception as e:
                logger.error(f"Failed to forward command to worker {worker_id}: {e}")
                err_msg = serialize_message(
                    create_error(
                        code="FORWARD_FAILED",
                        detail=f"Failed to deliver command to worker '{worker_id}': {e}",
                        worker_id=worker_id,
                    )
                )
                await ws.send_text(err_msg)
            return

        # 3. RESYNC_REQUEST — Route strictly to target worker_id
        elif isinstance(msg, ResyncRequestMessage):
            worker_id = msg.worker_id
            worker_ws = self.worker_mgr.get_worker_ws(worker_id)
            if not worker_ws:
                err_msg = serialize_message(
                    create_error(
                        code="WORKER_NOT_CONNECTED",
                        detail=f"Cannot resync: worker '{worker_id}' is not connected",
                        worker_id=worker_id,
                    )
                )
                await ws.send_text(err_msg)
                return

            forward_payload = serialize_message(msg)
            try:
                await worker_ws.send_text(forward_payload)
                logger.debug(f"Routed RESYNC_REQUEST to worker '{worker_id}'")
            except Exception as e:
                logger.error(f"Failed to forward resync to worker {worker_id}: {e}")
            return

        # 4. THROTTLE_CONFIG — Route to target worker_id and update server-side rate limit
        elif isinstance(msg, ThrottleConfigMessage):
            worker_id = msg.worker_id
            # Update server-side rate limiting for this worker
            self.worker_mgr.set_throttle_profile(
                worker_id=worker_id,
                profile_name=msg.profile_name,
                min_snapshot_interval_ms=msg.min_snapshot_interval_ms,
            )
            # Forward to worker so it can adjust compression settings
            worker_ws = self.worker_mgr.get_worker_ws(worker_id)
            if worker_ws:
                try:
                    await worker_ws.send_text(serialize_message(msg))
                    logger.debug(f"Routed THROTTLE_CONFIG '{msg.profile_name}' to worker '{worker_id}'")
                except Exception as e:
                    logger.error(f"Failed to forward throttle config to worker {worker_id}: {e}")
            return

        # 5. BROWSER_CONFIG — Route to target worker_id
        elif isinstance(msg, BrowserConfigMessage):
            worker_id = msg.worker_id
            worker_ws = self.worker_mgr.get_worker_ws(worker_id)
            if not worker_ws:
                err_msg = serialize_message(
                    create_error(
                        code="WORKER_NOT_CONNECTED",
                        detail=f"Cannot apply browser config: worker '{worker_id}' is not connected",
                        worker_id=worker_id,
                    )
                )
                await ws.send_text(err_msg)
                return

            try:
                await worker_ws.send_text(serialize_message(msg))
                logger.debug(f"Routed BROWSER_CONFIG to worker '{worker_id}'")
            except Exception as e:
                logger.error(f"Failed to forward browser config to worker {worker_id}: {e}")
            return

        # 6. PING / PONG
        elif isinstance(msg, PingMessage):
            await ws.send_text(serialize_message(create_pong(payload=msg.payload)))
            return

        elif isinstance(msg, PongMessage):
            return

        else:
            logger.warning(f"Unhandled controller message type: {msg_type}")

    async def handle_worker_message(
        self,
        ws: WebSocket,
        bound_worker_id: str,
        raw_msg: Union[str, bytes, Dict[str, Any]],
    ) -> None:
        """
        Handle and route an inbound message from a Worker WebSocket.
        Broadcasts Worker-scoped messages ONLY to Controllers subscribed to this worker_id.
        """
        try:
            msg = parse_message(raw_msg)
        except ProtocolError as e:
            logger.warning(f"Protocol error from worker {bound_worker_id}: {e}")
            err = serialize_message(create_error(code="PROTOCOL_ERROR", detail=str(e), worker_id=bound_worker_id))
            await ws.send_text(err)
            return
        except Exception as e:
            logger.error(f"Unexpected parse error from worker {bound_worker_id}: {e}")
            err = serialize_message(create_error(code="MALFORMED_MESSAGE", detail=str(e), worker_id=bound_worker_id))
            await ws.send_text(err)
            return

        # Defensive security check: Worker must not impersonate another worker_id
        if isinstance(msg, WorkerScopedMessage) and msg.worker_id != bound_worker_id:
            logger.error(
                f"Worker identity mismatch: connection registered as '{bound_worker_id}' sent message for '{msg.worker_id}'"
            )
            err = serialize_message(
                create_error(
                    code="IDENTITY_MISMATCH",
                    detail=f"Message worker_id '{msg.worker_id}' does not match registered '{bound_worker_id}'",
                    worker_id=bound_worker_id,
                )
            )
            await ws.send_text(err)
            return

        msg_type = msg.type

        # 1. WORKER_STATUS update
        if isinstance(msg, WorkerStatusMessage):
            await self.worker_mgr.update_status(bound_worker_id, msg.status, msg.dom_version)
            return

        # 2. FULL_SNAPSHOT
        elif isinstance(msg, FullSnapshotMessage):
            self.worker_mgr.update_dom_version(bound_worker_id, msg.version)
            await self._broadcast_to_subscribers(bound_worker_id, msg)
            return

        # 3. DOM_UPDATE
        elif isinstance(msg, DomUpdateMessage):
            self.worker_mgr.update_dom_version(bound_worker_id, msg.version)
            await self._broadcast_to_subscribers(bound_worker_id, msg)
            return

        # 4. COMMAND_RESULT
        elif isinstance(msg, CommandResultMessage):
            await self._broadcast_to_subscribers(bound_worker_id, msg)
            return

        # 5. ERROR
        elif isinstance(msg, ErrorMessage):
            await self._broadcast_to_subscribers(bound_worker_id, msg)
            return

        # 6. PING / PONG
        elif isinstance(msg, PingMessage):
            await ws.send_text(serialize_message(create_pong(payload=msg.payload)))
            return

        elif isinstance(msg, PongMessage):
            return

        else:
            logger.warning(f"Unhandled worker message type from {bound_worker_id}: {msg_type}")

    async def _broadcast_to_subscribers(self, worker_id: str, msg: BaseMessage) -> None:
        """
        Forward a worker message ONLY to Controllers subscribed to this worker_id.
        """
        subscribers = self.worker_mgr.get_subscribers(worker_id)
        if not subscribers:
            logger.debug(f"No active controller subscribers for worker '{worker_id}'")
            return

        serialized = serialize_message(msg)
        for ctrl_ws in list(subscribers):
            try:
                await ctrl_ws.send_text(serialized)
            except Exception as e:
                logger.debug(f"Failed to send to subscriber {ctrl_ws}: {e}")
