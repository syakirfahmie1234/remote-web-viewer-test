"""
FastAPI Server Application Entry Point.
Provides WebSocket endpoints (/ws/worker, /ws/controller) and health check (/health).
Strictly acts as a relay with zero browser automation, DOM storage, or HTTP polling.
"""

from __future__ import annotations
import logging
from contextlib import asynccontextmanager
from typing import Dict
import asyncio
from fastapi import FastAPI, WebSocket
import uvicorn

from server.config import PORT, SESSION_TIMEOUT_SECONDS
from server.worker_manager import WorkerManager
from server.controller_manager import ControllerManager
from server.message_router import MessageRouter
from server.websocket_manager import WebSocketManager
from server.session import SessionManager
from server.audit import log_audit_event
from shared.messages import serialize_message, create_error

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
)
logger = logging.getLogger("server.main")

# Instantiate singletons for connection management and routing
worker_manager = WorkerManager()
session_manager = SessionManager()
controller_manager = ControllerManager(session_mgr=session_manager)
message_router = MessageRouter(
    worker_mgr=worker_manager, 
    controller_mgr=controller_manager,
    session_mgr=session_manager,
)
websocket_manager = WebSocketManager(
    worker_mgr=worker_manager,
    controller_mgr=controller_manager,
    router=message_router,
    session_mgr=session_manager,
)


async def session_reaper_loop() -> None:
    """Background task to reap expired controller sessions."""
    logger.info(f"Session reaper started. Timeout set to {SESSION_TIMEOUT_SECONDS}s")
    while True:
        try:
            await asyncio.sleep(30)
            expired = session_manager.get_expired_sessions(SESSION_TIMEOUT_SECONDS)
            for session in expired:
                logger.info(f"Reaping expired session {session.session_id}")
                err_msg = serialize_message(
                    create_error(code="SESSION_TIMEOUT", detail="Session expired due to inactivity")
                )
                try:
                    await session.websocket.send_text(err_msg)
                    await session.websocket.close(code=1000, reason="Session expired")
                except Exception as e:
                    logger.debug(f"Error closing expired socket {session.session_id}: {e}")
                finally:
                    controller_manager.unregister_controller(session.websocket)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in session reaper loop: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"FastAPI Server starting on port {PORT}")
    reaper_task = None
    if SESSION_TIMEOUT_SECONDS > 0:
        reaper_task = asyncio.create_task(session_reaper_loop())
    
    yield
    
    logger.info("FastAPI Server shutting down")
    if reaper_task:
        reaper_task.cancel()
        try:
            await reaper_task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="Remote Website Real-Time Relay Server",
    description="WebSocket-only real-time relay routing strictly by worker_id",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health_check() -> Dict[str, str]:
    """
    Health check endpoint for Render deployment health probe.
    Does NOT return real-time state or act as a polling endpoint.
    """
    return {"status": "ok"}


@app.websocket("/ws/worker")
async def websocket_worker_endpoint(websocket: WebSocket) -> None:
    """
    WebSocket endpoint for Worker instances.
    Each worker establishes an outbound connection, authenticates, and registers its worker_id.
    """
    await websocket_manager.handle_worker_connection(websocket)


@app.websocket("/ws/controller")
async def websocket_controller_endpoint(websocket: WebSocket) -> None:
    """
    WebSocket endpoint for Controller instances.
    Controllers authenticate, subscribe to target worker_id(s), and receive updates.
    """
    await websocket_manager.handle_controller_connection(websocket)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
