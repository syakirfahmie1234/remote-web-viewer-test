"""
WebSocket connection lifecycle management for Workers and Controllers.
Handles authentication handshake, registration, message loop, and isolated cleanup.
"""

from __future__ import annotations
import asyncio
import logging
from typing import Optional
from fastapi import WebSocket, WebSocketDisconnect, status

from server.authentication import (
    verify_worker_token,
    verify_controller_token,
    extract_token_from_websocket,
    get_authorized_workers_for_token,
    get_client_ip,
)
from server.worker_manager import WorkerManager
from server.controller_manager import ControllerManager
from server.message_router import MessageRouter
from server.session import SessionManager
from server.audit import log_audit_event
from shared.protocol import (
    MSG_AUTH,
    MSG_HELLO,
    MSG_WORKER_REGISTER,
    MSG_CONTROLLER_REGISTER,
    STATUS_CONNECTED,
    STATUS_DISCONNECTED,
)
from shared.models import (
    AuthMessage,
    WorkerRegisterMessage,
    ControllerRegisterMessage,
)
from shared.messages import (
    parse_message,
    serialize_message,
    create_error,
    create_worker_status,
)

logger = logging.getLogger("server.websocket_manager")


class WebSocketManager:
    """
    Manages active WebSocket sessions for both Worker and Controller endpoints.
    """
    def __init__(
        self,
        worker_mgr: WorkerManager,
        controller_mgr: ControllerManager,
        router: MessageRouter,
        session_mgr: SessionManager,
    ) -> None:
        self.worker_mgr = worker_mgr
        self.controller_mgr = controller_mgr
        self.router = router
        self.session_mgr = session_mgr

    async def handle_worker_connection(self, websocket: WebSocket) -> None:
        """
        Handle the full lifecycle of a Worker WebSocket connection.
        """
        await websocket.accept()
        worker_id: Optional[str] = None

        try:
            client_ip = get_client_ip(websocket)
            worker_id_hint = websocket.query_params.get("worker_id")
            
            # 1. Authentication
            token = extract_token_from_websocket(websocket)
            if token is not None:
                if not verify_worker_token(token):
                    log_audit_event(event="AUTH_FAILURE", worker_id=worker_id_hint, ip_address=client_ip, role="worker", reason="Invalid token in query/header")
                    logger.warning("Worker authentication failed: invalid token provided")
                    await websocket.send_text(
                        serialize_message(create_error(code="AUTH_FAILED", detail="Invalid worker token"))
                    )
                    await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                    return
                authenticated = True
            else:
                # Wait for initial AUTH frame
                try:
                    auth_raw = await asyncio.wait_for(websocket.receive_text(), timeout=5.0)
                    msg = parse_message(auth_raw)
                    if isinstance(msg, AuthMessage) and verify_worker_token(msg.token):
                        authenticated = True
                    else:
                        log_audit_event(event="AUTH_FAILURE", worker_id=worker_id_hint, ip_address=client_ip, role="worker", reason="Invalid token in AUTH message")
                        logger.warning("Worker authentication failed on initial message")
                        await websocket.send_text(
                            serialize_message(create_error(code="AUTH_FAILED", detail="Invalid worker token"))
                        )
                        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                        return
                except (asyncio.TimeoutError, Exception) as e:
                    log_audit_event(event="AUTH_FAILURE", worker_id=worker_id_hint, ip_address=client_ip, role="worker", reason=f"Timeout or parse error: {e}")
                    logger.warning(f"Worker auth timeout or error: {e}")
                    await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                    return
                    
            log_audit_event(event="AUTH_SUCCESS", worker_id=worker_id_hint, ip_address=client_ip, role="worker")

            # 2. Registration
            query_worker_id = websocket.query_params.get("worker_id")
            if query_worker_id and query_worker_id.strip():
                worker_id = query_worker_id.strip()
                await self.worker_mgr.register_worker(worker_id, websocket)
            else:
                try:
                    reg_raw = await asyncio.wait_for(websocket.receive_text(), timeout=5.0)
                    msg = parse_message(reg_raw)
                    if isinstance(msg, WorkerRegisterMessage):
                        worker_id = msg.worker_id
                        await self.worker_mgr.register_worker(
                            worker_id=worker_id,
                            ws=websocket,
                            capabilities=msg.capabilities,
                        )
                    else:
                        logger.warning(f"Expected WORKER_REGISTER, got {msg.type}")
                        await websocket.send_text(
                            serialize_message(create_error(code="REGISTRATION_FAILED", detail="Expected worker_register"))
                        )
                        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                        return
                except (asyncio.TimeoutError, Exception) as e:
                    logger.warning(f"Worker registration timeout or error: {e}")
                    await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                    return

            # 3. Message dispatch loop
            while True:
                data = await websocket.receive_text()
                await self.router.handle_worker_message(websocket, worker_id, data)

        except WebSocketDisconnect:
            logger.info(f"Worker WebSocket disconnected: {worker_id or 'unregistered'}")
        except Exception as e:
            logger.error(f"Unexpected exception in worker WebSocket loop for {worker_id}: {e}")
        finally:
            if worker_id:
                await self.worker_mgr.unregister_worker(worker_id, websocket)

    async def handle_controller_connection(self, websocket: WebSocket) -> None:
        """
        Handle the full lifecycle of a Controller WebSocket connection.
        """
        await websocket.accept()

        try:
            client_ip = get_client_ip(websocket)
            client_id = websocket.query_params.get("client_id")
            active_token = None

            # 1. Authentication
            token = extract_token_from_websocket(websocket)
            if token is not None:
                if not verify_controller_token(token):
                    log_audit_event(event="AUTH_FAILURE", client_id=client_id, ip_address=client_ip, role="controller", reason="Invalid token in query/header")
                    logger.warning("Controller authentication failed: invalid token provided")
                    await websocket.send_text(
                        serialize_message(create_error(code="AUTH_FAILED", detail="Invalid controller token"))
                    )
                    await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                    return
                active_token = token
            else:
                # Wait for initial AUTH frame
                try:
                    auth_raw = await asyncio.wait_for(websocket.receive_text(), timeout=5.0)
                    msg = parse_message(auth_raw)
                    if isinstance(msg, AuthMessage) and verify_controller_token(msg.token):
                        active_token = msg.token
                    else:
                        log_audit_event(event="AUTH_FAILURE", client_id=client_id, ip_address=client_ip, role="controller", reason="Invalid token in AUTH message")
                        logger.warning("Controller authentication failed on initial message")
                        await websocket.send_text(
                            serialize_message(create_error(code="AUTH_FAILED", detail="Invalid controller token"))
                        )
                        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                        return
                except (asyncio.TimeoutError, Exception) as e:
                    log_audit_event(event="AUTH_FAILURE", client_id=client_id, ip_address=client_ip, role="controller", reason=f"Timeout or parse error: {e}")
                    logger.warning(f"Controller auth timeout or error: {e}")
                    await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                    return

            log_audit_event(event="AUTH_SUCCESS", client_id=client_id, ip_address=client_ip, role="controller")

            # 2. Registration and Session Creation
            subscribed_worker = websocket.query_params.get("worker_id")
            authorized_workers = get_authorized_workers_for_token(active_token)

            session = self.controller_mgr.register_controller(
                ws=websocket,
                client_id=client_id,
                subscribed_worker_ids={subscribed_worker} if subscribed_worker else set(),
                authorized_workers=authorized_workers,
            )

            log_audit_event(
                event="SESSION_START", 
                session_id=session.session_id, 
                client_id=client_id, 
                extra={"authorized_workers": list(authorized_workers) if authorized_workers is not None else None}
            )

            for w_status in self.worker_mgr.get_all_worker_statuses():
                if w_status["connected"]:
                    w_id = w_status["worker_id"]
                    if w_id == subscribed_worker:
                        continue
                    if self.session_mgr.is_authorized(websocket, w_id):
                        await websocket.send_text(
                            serialize_message(
                                create_worker_status(
                                    worker_id=w_id,
                                    status=w_status["status"],
                                    dom_version=w_status["dom_version"],
                                )
                            )
                        )

            if subscribed_worker:
                self.worker_mgr.add_subscriber(subscribed_worker, websocket)
                # Send initial status for this worker
                wstate = self.worker_mgr.get_worker_state(subscribed_worker)
                status_val = wstate.status if wstate else STATUS_DISCONNECTED
                dom_ver = wstate.dom_version if wstate else None
                await websocket.send_text(
                    serialize_message(
                        create_worker_status(
                            worker_id=subscribed_worker,
                            status=status_val,
                            dom_version=dom_ver,
                        )
                    )
                )

            # 3. Message dispatch loop
            while True:
                data = await websocket.receive_text()
                await self.router.handle_controller_message(websocket, data)

        except WebSocketDisconnect:
            logger.info("Controller WebSocket disconnected")
        except Exception as e:
            logger.error(f"Unexpected exception in controller WebSocket loop: {e}")
        finally:
            self.controller_mgr.unregister_controller(websocket)
            self.worker_mgr.remove_subscriber_from_all(websocket)
