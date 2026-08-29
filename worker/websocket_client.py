"""
Worker WebSocket Client.
Maintains persistent, auto-reconnecting WebSocket connection to the FastAPI relay server.
Strictly tags every outgoing message with this Worker instance's worker_id.
"""

from __future__ import annotations
import asyncio
import logging
from typing import Any, Callable, Coroutine, Optional
import websockets
from websockets.exceptions import ConnectionClosed

from worker.config import (
    SERVER_WS_URL,
    WORKER_ID,
    WORKER_TOKEN,
    INITIAL_RECONNECT_DELAY,
    MAX_RECONNECT_DELAY,
    RECONNECT_BACKOFF_FACTOR,
)
from shared.models import BaseMessage, WorkerScopedMessage
from shared.messages import (
    serialize_message,
    parse_message,
    create_worker_register,
    create_pong,
    ProtocolError,
)
from shared.protocol import MSG_PING

logger = logging.getLogger(f"worker.ws.{WORKER_ID}")

MessageHandler = Callable[[BaseMessage], Coroutine[Any, Any, None]]
LifecycleCallback = Callable[[], Coroutine[Any, Any, None]]


class WorkerWebSocketClient:
    """
    Asynchronous WebSocket client connecting the Worker to the relay server.
    """
    def __init__(
        self,
        server_url: str = SERVER_WS_URL,
        worker_id: str = WORKER_ID,
        token: str = WORKER_TOKEN,
    ) -> None:
        self.server_url = server_url
        self.worker_id = worker_id
        self.token = token
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._running: bool = False
        self._connected_event = asyncio.Event()
        self._message_handler: Optional[MessageHandler] = None
        self._on_connected_cb: Optional[LifecycleCallback] = None
        self._on_disconnected_cb: Optional[LifecycleCallback] = None
        self._loop_task: Optional[asyncio.Task] = None

    def set_message_handler(self, handler: MessageHandler) -> None:
        """Register the coroutine handler for incoming messages from server."""
        self._message_handler = handler

    def set_lifecycle_callbacks(
        self,
        on_connected: Optional[LifecycleCallback] = None,
        on_disconnected: Optional[LifecycleCallback] = None,
    ) -> None:
        """Set callbacks triggered when connection is established or lost."""
        self._on_connected_cb = on_connected
        self._on_disconnected_cb = on_disconnected

    @property
    def is_connected(self) -> bool:
        """Check if WebSocket connection is active."""
        if self._ws is None:
            return False
        # Compatible with websockets v10-v17+
        if hasattr(self._ws, "closed"):
            return not self._ws.closed
        if hasattr(self._ws, "state"):
            return getattr(self._ws.state, "name", "") == "OPEN"
        return True

    async def start(self) -> None:
        """Start the background connection and reconnection management loop."""
        self._running = True
        self._loop_task = asyncio.create_task(self._connection_loop())

    async def stop(self) -> None:
        """Stop the client and close the active connection."""
        self._running = False
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
        if self._loop_task:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass

    async def send_message(self, msg: BaseMessage) -> None:
        """
        Send a message to the server.
        Verifies that worker_id matches this client for WorkerScopedMessages.
        """
        if not self.is_connected or not self._ws:
            raise ConnectionError(f"Worker {self.worker_id} is not currently connected to server")

        if isinstance(msg, WorkerScopedMessage) and msg.worker_id != self.worker_id:
            raise ValueError(f"Cannot send message with worker_id '{msg.worker_id}' from worker '{self.worker_id}'")

        payload = serialize_message(msg)
        await self._ws.send(payload)

    async def wait_until_connected(self, timeout: float = 10.0) -> bool:
        """Wait until connection is successfully established."""
        try:
            await asyncio.wait_for(self._connected_event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def _connection_loop(self) -> None:
        """
        Persistent loop managing connection, message reception, and exponential backoff reconnection.
        """
        reconnect_delay = INITIAL_RECONNECT_DELAY

        while self._running:
            url_with_params = f"{self.server_url}?token={self.token}&worker_id={self.worker_id}"
            logger.info(f"Connecting to server {self.server_url} as '{self.worker_id}'...")

            try:
                async with websockets.connect(url_with_params) as ws:
                    self._ws = ws
                    self._connected_event.set()
                    reconnect_delay = INITIAL_RECONNECT_DELAY
                    logger.info(f"Connected and authenticated with server as '{self.worker_id}'")

                    # Trigger connected lifecycle callback (e.g. send initial snapshot)
                    if self._on_connected_cb:
                        try:
                            await self._on_connected_cb()
                        except Exception as e:
                            logger.error(f"Error in on_connected callback: {e}")

                    # Listen for messages
                    async for message in ws:
                        await self._handle_raw_message(message)

            except (ConnectionClosed, OSError) as e:
                logger.warning(f"Connection lost to server ({e}). Reconnecting in {reconnect_delay:.1f}s...")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Unexpected connection error: {e}. Reconnecting in {reconnect_delay:.1f}s...")
            finally:
                self._connected_event.clear()
                self._ws = None
                if self._on_disconnected_cb:
                    try:
                        await self._on_disconnected_cb()
                    except Exception as e:
                        logger.debug(f"Error in on_disconnected callback: {e}")

            if self._running:
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * RECONNECT_BACKOFF_FACTOR, MAX_RECONNECT_DELAY)

    async def _handle_raw_message(self, raw_data: Any) -> None:
        """Parse incoming frame and dispatch to message handler."""
        try:
            msg = parse_message(raw_data)
        except ProtocolError as e:
            logger.warning(f"Failed to parse server message: {e}")
            return
        except Exception as e:
            logger.error(f"Unexpected message error: {e}")
            return

        # Automatically respond to server PING
        if msg.type == MSG_PING:
            try:
                await self.send_message(create_pong(payload=getattr(msg, "payload", None)))
            except Exception:
                pass
            return

        # Dispatch to registered message handler (commands, resync, etc.)
        if self._message_handler:
            try:
                await self._message_handler(msg)
            except Exception as e:
                logger.error(f"Error handling message {msg.type}: {e}")
