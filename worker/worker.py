"""
Worker main application orchestrator.
Manages one persistent Chrome browser instance and one WebSocket connection to the server.
Guarantees that WebSocket reconnects survive without restarting Chrome.
"""

from __future__ import annotations
import asyncio
import logging
import os
import signal
import tempfile
from typing import Optional

from worker.config import (
    WORKER_ID,
    WORKER_TOKEN,
    SERVER_WS_URL,
    TARGET_DOMAIN,
)
from worker.browser import BrowserManager
from worker.websocket_client import WorkerWebSocketClient
from shared.protocol import (
    MSG_COMMAND,
    MSG_RESYNC_REQUEST,
    STATUS_CRASHED,
)
from shared.models import (
    BaseMessage,
    CommandMessage,
    ResyncRequestMessage,
    ThrottleConfigMessage,
    BrowserConfigMessage,
)
from shared.messages import (
    create_full_snapshot,
    create_worker_status,
    create_command_result,
    create_error,
    create_dom_update,
)

from worker.mutation_observer import DOMMutationTracker
from worker.command_handler import CommandHandler
from worker.dom_pipeline import HTMLNormalizer
from shared.compression import compress_payload
from shared.throttle import get_default_profile, ThrottleProfile

logger = logging.getLogger(f"worker.main.{WORKER_ID}")


class Worker:
    """
    Main Worker process instance.
    Maintains persistent Chrome browser session and WebSocket connection to relay server.
    """
    def __init__(
        self,
        worker_id: str = WORKER_ID,
        server_url: str = SERVER_WS_URL,
        token: str = WORKER_TOKEN,
        target_domain: str = TARGET_DOMAIN,
    ) -> None:
        self.worker_id = worker_id
        self.server_url = server_url
        self.token = token
        self.target_domain = target_domain

        profile_dir = tempfile.mkdtemp(prefix=f"remote_chrome_{self.worker_id}_")
        self.browser = BrowserManager(user_data_dir=profile_dir)
        self.normalizer = HTMLNormalizer()
        self.command_handler = CommandHandler(
            browser=self.browser,
            worker_id=self.worker_id,
        )
        self.ws_client = WorkerWebSocketClient(
            server_url=self.server_url,
            worker_id=self.worker_id,
            token=self.token,
        )

        self.dom_version: int = 1
        self._running: bool = False
        self._throttle_profile: ThrottleProfile = get_default_profile()

        self.dom_tracker = DOMMutationTracker(self.browser, self.worker_id)
        
        # Guard for Python state updates (snapshot tracking, version bumps)
        self._state_lock = asyncio.Lock()
        
        # Guard for all Selenium I/O across the worker (Semaphore to allow offloading to_thread)
        self._selenium_lock = asyncio.Semaphore(1)
        self._command_executing = False
        
        self._last_snapshot_html: str = ""
        self._navigation_in_progress: bool = False
        self._mutation_task: Optional[asyncio.Task] = None

        # Register callbacks
        self.ws_client.set_lifecycle_callbacks(
            on_connected=self._on_server_connected,
            on_disconnected=self._on_server_disconnected,
        )
        self.ws_client.set_message_handler(self._handle_server_message)

    async def start(self) -> None:
        """
        Start the Worker: launch persistent Chrome browser, navigate to target domain,
        and start WebSocket client.
        """
        self._running = True
        logger.info(f"Starting Worker '{self.worker_id}'...")

        # 1. Start persistent Chrome browser
        self.browser.start()
        try:
            self.browser.navigate(self.target_domain)
        except Exception as e:
            logger.warning(f"Initial navigation to target domain failed or delayed: {e}")

        # 2. Connect to WebSocket relay server
        await self.ws_client.start()
        
        # 3. Start mutation poll loop
        self._mutation_task = asyncio.create_task(self._mutation_poll_loop())
        logger.info(f"Worker '{self.worker_id}' initialized and running")

    async def stop(self) -> None:
        """
        Stop Worker: close WebSocket client and cleanly terminate Chrome browser.
        """
        self._running = False
        logger.info(f"Stopping Worker '{self.worker_id}'...")
        if self._mutation_task:
            self._mutation_task.cancel()
            try:
                await self._mutation_task
            except asyncio.CancelledError:
                pass
        await self.ws_client.stop()
        self.browser.quit()
        logger.info(f"Worker '{self.worker_id}' stopped")

    async def _on_server_connected(self) -> None:
        """
        Triggered when WebSocket connection to server is established or re-established.
        Sends a fresh FULL_SNAPSHOT to ensure Controller mirror is completely in sync.
        NOTE: Chrome browser is NEVER restarted on WebSocket reconnect.
        """
        logger.info(f"Connected to server. Dispatching initial FULL_SNAPSHOT for '{self.worker_id}'")
        try:
            await self.send_full_snapshot()
        except Exception as e:
            logger.error(f"Failed to send initial snapshot upon connect: {e}")

    async def _on_server_disconnected(self) -> None:
        """
        Triggered when WebSocket connection is lost.
        Chrome browser remains running and untouched.
        """
        logger.info(f"WebSocket disconnected from server. Chrome browser remains active.")

    async def send_full_snapshot(self) -> None:
        """
        Capture current page source from Chrome and send FULL_SNAPSHOT to server.
        """
        if not self.browser.is_alive():
            logger.error("Cannot capture snapshot: Chrome browser session is dead. Initiating crash recovery...")
            await self._recover_from_browser_crash()
            return

        async with self._selenium_lock:
            page_source = await asyncio.to_thread(self.browser.get_page_source)
            url = await asyncio.to_thread(self.browser.get_current_url)
            title = await asyncio.to_thread(self.browser.get_title)
            await asyncio.to_thread(self.dom_tracker.inject)
            
        # Normalization and compression can be done without Selenium lock, just offload to thread
        normalized_html = await asyncio.to_thread(self.normalizer.normalize, page_source)
        def _do_compress():
            return compress_payload(
                data=normalized_html,
                threshold=self._throttle_profile.compression_threshold,
                level=self._throttle_profile.compression_level,
            )
        payload_html, is_compressed = await asyncio.to_thread(_do_compress)

        async with self._state_lock:
            msg = create_full_snapshot(
                worker_id=self.worker_id,
                version=self.dom_version,
                url=url,
                title=title,
                html=payload_html,
                compressed=is_compressed,
            )
            await self.ws_client.send_message(msg)
            logger.info(f"Sent FULL_SNAPSHOT (version={self.dom_version}, url={url}, compressed={is_compressed})")
            
            self._last_snapshot_html = normalized_html
            self.dom_version += 1

    async def _recover_from_browser_crash(self) -> None:
        """
        Crash recovery lifecycle (per-Worker isolated):
        1. Notify server with WORKER_STATUS(crashed)
        2. Restart Chrome
        3. Navigate to TARGET_DOMAIN
        4. Reset dom_version = 0
        5. Send fresh FULL_SNAPSHOT
        """
        logger.warning(f"Executing browser crash recovery for worker '{self.worker_id}'...")
        try:
            await self.ws_client.send_message(
                create_worker_status(worker_id=self.worker_id, status=STATUS_CRASHED)
            )
        except Exception:
            pass

        async with self._selenium_lock:
            await asyncio.to_thread(self.browser.restart)
            try:
                await asyncio.to_thread(self.browser.navigate, self.target_domain)
            except Exception as e:
                logger.error(f"Navigation after crash restart failed: {e}")

        async with self._state_lock:
            self.dom_version = 0
        await self.send_full_snapshot()
        logger.info(f"Crash recovery complete for '{self.worker_id}'")

    async def _handle_server_message(self, msg: BaseMessage) -> None:
        """
        Dispatch incoming server messages (commands, resync requests).
        """
        if isinstance(msg, ResyncRequestMessage):
            logger.info(f"Received RESYNC_REQUEST for worker '{self.worker_id}' (reason: {msg.reason})")
            await self.send_full_snapshot()

        elif isinstance(msg, CommandMessage):
            async def _process_command():
                logger.info(f"Executing command '{msg.command}' for worker '{self.worker_id}'")
                is_nav = (msg.command == "navigate")
                if is_nav:
                    self._navigation_in_progress = True
                    
                self._command_executing = True
                try:
                    async with self._selenium_lock:
                        result = await self.command_handler.execute(msg)
                finally:
                    self._command_executing = False
                    
                await self.ws_client.send_message(result)
                
                if is_nav:
                    async with self._selenium_lock:
                        if self.browser.is_alive():
                            page_source = await asyncio.to_thread(self.browser.get_page_source)
                            await asyncio.to_thread(self.dom_tracker.ensure_injected)
                        else:
                            page_source = None
                            
                    if page_source is not None:
                        normalized_html = await asyncio.to_thread(self.normalizer.normalize, page_source)
                        async with self._state_lock:
                            self._last_snapshot_html = normalized_html
                    self._navigation_in_progress = False

            asyncio.create_task(_process_command())
            
        elif isinstance(msg, ThrottleConfigMessage):
            logger.info(f"Applying new throttle profile '{msg.profile_name}' for worker '{self.worker_id}'")
            self._throttle_profile = ThrottleProfile(
                name=msg.profile_name,
                compression_level=msg.compression_level,
                compression_threshold=msg.compression_threshold,
                max_snapshot_bytes=msg.max_snapshot_bytes,
                min_snapshot_interval_ms=msg.min_snapshot_interval_ms,
                description=f"Remote applied: {msg.profile_name}"
            )
            
        elif isinstance(msg, BrowserConfigMessage):
            logger.info(f"Received BrowserConfigMessage for worker '{self.worker_id}'. Restarting Chrome...")
            from worker.browser import BrowserConfig
            
            from worker.config import PROXY_USERNAME, PROXY_PASSWORD
            
            new_config = BrowserConfig(
                headless=msg.headless,
                proxy_url=msg.proxy_url,
                proxy_username=PROXY_USERNAME if msg.proxy_url else None,
                proxy_password=PROXY_PASSWORD if msg.proxy_url else None,
            )
            
            try:
                await self.ws_client.send_message(
                    create_worker_status(worker_id=self.worker_id, status=STATUS_CRASHED)
                )
            except Exception:
                pass

            async with self._selenium_lock:
                await asyncio.to_thread(self.browser.restart_with_config, new_config)
                try:
                    await asyncio.to_thread(self.browser.navigate, self.target_domain)
                except Exception as e:
                    logger.error(f"Navigation after config restart failed: {e}")
                
            async with self._state_lock:
                self.dom_version = 0
            await self.send_full_snapshot()
            logger.info("Chrome restart with new config complete.")

    async def _mutation_poll_loop(self) -> None:
        """Background task to poll for DOM mutations, compute diffs, and send MSG_DOM_UPDATE."""
        from selenium.common.exceptions import WebDriverException
        from shared.dom_differ import compute_diff
        
        while self._running:
            try:
                if getattr(self, "_command_executing", False):
                    await asyncio.sleep(0.5)  # Backoff during active commands
                    continue
                else:
                    await asyncio.sleep(0.1)
                
                if not self.ws_client.is_connected or not self.browser.is_alive():
                    continue
                    
                if self._navigation_in_progress:
                    # Discard pending mutations mid-navigation
                    async with self._selenium_lock:
                        await asyncio.to_thread(self.dom_tracker.drain_mutations)
                    continue
                
                async with self._selenium_lock:
                    mutations = await asyncio.to_thread(self.dom_tracker.drain_mutations)
                    if not mutations:
                        continue
                    
                    if self._navigation_in_progress or not self.browser.is_alive():
                        continue
                        
                    page_source = await asyncio.to_thread(self.browser.get_page_source)
                
                # Normalization and diffing do not need Selenium lock
                new_html = await asyncio.to_thread(self.normalizer.normalize, page_source)
                
                async with self._state_lock:
                    diff_ops = await asyncio.to_thread(compute_diff, self._last_snapshot_html, new_html)
                    if diff_ops:
                        msg = create_dom_update(
                            worker_id=self.worker_id,
                            base_version=self.dom_version,
                            version=self.dom_version + 1,
                            ops=[op.to_dict() for op in diff_ops]
                        )
                        await self.ws_client.send_message(msg)
                        
                        self._last_snapshot_html = new_html
                        self.dom_version += 1
                        logger.info(f"Sent DOM_UPDATE for '{self.worker_id}' (version={self.dom_version}, ops={len(diff_ops)})")
                    else:
                        logger.debug(f"Drained mutations but computed diff is empty for '{self.worker_id}'")
            except WebDriverException:
                pass
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in mutation poll loop: {e}")

async def run_worker() -> None:
    """Entry point coroutine for running worker process."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    )
    worker = Worker()

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _sig_handler():
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _sig_handler)
        except NotImplementedError:
            pass  # Windows signal handling

    await worker.start()
    try:
        await stop_event.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await worker.stop()


if __name__ == "__main__":
    asyncio.run(run_worker())
