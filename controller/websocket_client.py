"""
Controller WebSocket Client using PySide6 Signals and a background asyncio event loop.
Maintains persistent connection to the FastAPI server and emits Qt signals to the GUI thread.
"""

from __future__ import annotations
import asyncio
import logging
import threading
from typing import Any, Optional
from PySide6.QtCore import QObject, Signal
import websockets
from websockets.exceptions import ConnectionClosed

from shared.models import BaseMessage
from shared.messages import (
    serialize_message,
    parse_message,
    create_pong,
    ProtocolError,
)
from shared.protocol import MSG_PING

logger = logging.getLogger("controller.ws_client")


class ControllerWebSocketClient(QObject):
    """
    WebSocket client running in a background thread and communicating with PySide6 via Signals.
    """
    connected = Signal()
    disconnected = Signal()
    message_received = Signal(object)  # Emits BaseMessage instance
    connection_error = Signal(str)

    def __init__(
        self,
        server_url: str = "ws://127.0.0.1:8000/ws/controller",
        token: str = "default-controller-token-secret",
        client_id: str = "controller-main",
    ) -> None:
        super().__init__()
        self.server_url = server_url
        self.token = token
        self.client_id = client_id

        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._running: bool = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._supervisor_task: Optional[asyncio.Task] = None

    def start(self) -> None:
        """Start the background worker thread and event loop."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_event_loop, daemon=True, name="ControllerWSThread")
        self._thread.start()

    def stop(self) -> None:
        """Stop client and terminate background event loop cleanly."""
        self._running = False
        if self._loop and self._loop.is_running():
            if self._supervisor_task:
                self._loop.call_soon_threadsafe(self._supervisor_task.cancel)
        if self._thread:
            self._thread.join(timeout=2.0)

    def send_message(self, msg: BaseMessage) -> None:
        """
        Thread-safe method to send a BaseMessage over the WebSocket.
        Can be called directly from the PySide6 UI thread.
        """
        if not self._loop or not self._loop.is_running():
            logger.warning("Cannot send message: event loop is not running")
            return
        asyncio.run_coroutine_threadsafe(self._async_send(msg), self._loop)

    async def _async_send(self, msg: BaseMessage) -> None:
        if self._ws is not None:
            try:
                payload = serialize_message(msg)
                await self._ws.send(payload)
            except Exception as e:
                logger.error(f"Failed to send message: {e}")
                self.connection_error.emit(str(e))

    def _run_event_loop(self) -> None:
        """Background thread target."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._supervisor_task = self._loop.create_task(self._connection_supervisor())
            self._loop.run_until_complete(self._supervisor_task)
        except (asyncio.CancelledError, KeyboardInterrupt):
            pass
        finally:
            try:
                pending = asyncio.all_tasks(self._loop)
                for task in pending:
                    task.cancel()
                self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
            self._loop.close()

    async def _connection_supervisor(self) -> None:
        """Continuous reconnection loop."""
        reconnect_delay = 1.0

        while self._running:
            url = f"{self.server_url}?token={self.token}&client_id={self.client_id}"
            logger.info(f"Controller connecting to {self.server_url}...")

            try:
                async with websockets.connect(url) as ws:
                    self._ws = ws
                    reconnect_delay = 1.0
                    logger.info("Controller connected to relay server")
                    self.connected.emit()

                    async for raw_frame in ws:
                        self._handle_raw_frame(raw_frame)

            except (ConnectionClosed, OSError) as e:
                logger.warning(f"Controller connection lost: {e}. Reconnecting in {reconnect_delay:.1f}s...")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Controller WebSocket error: {e}")
                self.connection_error.emit(str(e))
            finally:
                self._ws = None
                self.disconnected.emit()

            if self._running:
                try:
                    await asyncio.sleep(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 1.5, 10.0)
                except asyncio.CancelledError:
                    break

    def _handle_raw_frame(self, raw_data: Any) -> None:
        """Parse incoming JSON frame and emit Qt signal."""
        try:
            msg = parse_message(raw_data)
        except ProtocolError as e:
            logger.warning(f"Controller received unparseable message: {e}")
            return
        except Exception as e:
            logger.error(f"Unexpected error in controller message parser: {e}")
            return

        # Auto-respond to PING
        if msg.type == MSG_PING:
            self.send_message(create_pong(payload=getattr(msg, "payload", None)))
            return

        # Emit to Qt main thread
        self.message_received.emit(msg)
