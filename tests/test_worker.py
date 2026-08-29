"""
Integration tests for Worker + Selenium.
Verifies persistent Chrome browser session, Unicode preservation, WebSocket connection,
authentication, registration, and forced disconnect/reconnect survival without restarting Chrome.
"""

import asyncio
import os
import threading
import time
import pytest
import uvicorn
import websockets

# Configure test environment variables
os.environ["WORKER_TOKEN"] = "test-worker-token-secret"
os.environ["CONTROLLER_TOKEN"] = "test-controller-token-secret"
os.environ["HEADLESS"] = "true"
os.environ["TARGET_DOMAIN"] = "https://example.com"

from server.main import app
from server.config import WORKER_TOKEN, CONTROLLER_TOKEN
from worker.browser import BrowserManager
from worker.worker import Worker
from worker.config import get_or_create_stable_worker_id
from shared.protocol import (
    MSG_FULL_SNAPSHOT,
    MSG_WORKER_STATUS,
    STATUS_CONNECTED,
)
from shared.messages import parse_message


class UvicornTestServer:
    """Runs FastAPI in a background thread on an ephemeral port."""
    def __init__(self, app):
        self.app = app
        self.server = None
        self.thread = None
        self.port = None

    def start(self):
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        self.port = sock.getsockname()[1]
        sock.close()

        config = uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="warning")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()

        for _ in range(50):
            if self.server.started:
                break
            time.sleep(0.05)

    def stop(self):
        if self.server:
            self.server.should_exit = True
        if self.thread:
            self.thread.join(timeout=2.0)


@pytest.fixture(scope="module")
def server_info():
    test_server = UvicornTestServer(app)
    test_server.start()
    url = f"ws://127.0.0.1:{test_server.port}/ws/worker"
    ctrl_url = f"ws://127.0.0.1:{test_server.port}/ws/controller"
    yield {"ws_url": url, "ctrl_url": ctrl_url}
    test_server.stop()


def test_browser_manager_lifecycle_and_unicode():
    """Verify BrowserManager starts Chrome, captures Unicode HTML, and manages lifecycle."""
    browser = BrowserManager()
    browser.start()
    try:
        assert browser.is_alive() is True

        # Navigate to a data URI with Unicode and emoji
        unicode_html = "<html><head><title>Unicode Test</title></head><body><h1>Привет мир 🚀 &amp; 测试</h1></body></html>"
        browser.driver.get(f"data:text/html;charset=utf-8,{unicode_html}")

        assert "Unicode Test" in browser.get_title()
        source = browser.get_page_source()
        assert "Привет мир" in source
        assert "🚀" in source
        assert "测试" in source

        # Test screenshot
        screenshot = browser.take_screenshot_base64()
        assert isinstance(screenshot, str)
        assert len(screenshot) > 100
    finally:
        browser.quit()
        assert browser.is_alive() is False


def test_stable_worker_id_generation():
    """Verify stable worker_id persistence."""
    id1 = get_or_create_stable_worker_id()
    id2 = get_or_create_stable_worker_id()
    assert id1 == id2
    assert len(id1) > 0


@pytest.mark.asyncio
async def test_worker_connect_and_survive_reconnect_without_chrome_restart(server_info):
    """
    Core Phase 3 Test:
    1. Worker connects and registers with server.
    2. Controller subscribes and receives initial FULL_SNAPSHOT.
    3. Forced WebSocket disconnect occurs.
    4. Worker reconnects automatically.
    5. Proves Chrome session was NOT restarted (same WebDriver session ID).
    """
    test_worker_id = "worker-persist-test-01"

    worker = Worker(
        worker_id=test_worker_id,
        server_url=server_info["ws_url"],
        token=WORKER_TOKEN,
    )

    await worker.start()

    try:
        # Wait for worker to connect
        connected = await worker.ws_client.wait_until_connected(timeout=10.0)
        assert connected is True
        assert worker.browser.is_alive() is True

        # Capture initial Chrome session ID
        initial_session_id = worker.browser.driver.session_id
        assert initial_session_id is not None

        # Connect a controller to verify snapshot reception
        ctrl_conn_url = f"{server_info['ctrl_url']}?token={CONTROLLER_TOKEN}&worker_id={test_worker_id}"
        async with websockets.connect(ctrl_conn_url) as ctrl_ws:
            # Drain initial status and snapshot
            msg1 = parse_message(await ctrl_ws.recv())
            assert msg1.type in (MSG_WORKER_STATUS, MSG_FULL_SNAPSHOT)

            # --- FORCE DISCONNECT WEBSOCKET ---
            # Force close the worker's active socket simulating a network drop
            if worker.ws_client._ws:
                await worker.ws_client._ws.close()

            # Wait for auto-reconnect
            await asyncio.sleep(2.0)
            reconnected = await worker.ws_client.wait_until_connected(timeout=10.0)
            assert reconnected is True

            # VERIFY CRITICAL INVARIANT: Chrome was NOT restarted!
            assert worker.browser.is_alive() is True
            current_session_id = worker.browser.driver.session_id
            assert current_session_id == initial_session_id, (
                f"Chrome was restarted! Initial session {initial_session_id} != {current_session_id}"
            )

    finally:
        await worker.stop()
