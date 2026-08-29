"""
Unit and integration tests for DOM Snapshot live rendering.
Verifies HTML sanitization (stripping scripts, event handlers, iframes), zstd decompression,
visual status badges, and automatic UI updates upon receiving FullSnapshot messages.
"""

import asyncio
import base64
import os
import threading
import time
import pytest
import uvicorn
import websockets
import zstandard as zstd
from PySide6.QtWidgets import QApplication

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["WORKER_TOKEN"] = "test-render-worker-token"
os.environ["CONTROLLER_TOKEN"] = "test-render-ctrl-token"
os.environ["HEADLESS"] = "true"

from server.main import app
from server.config import WORKER_TOKEN, CONTROLLER_TOKEN
from worker.worker import Worker
from controller.dom_renderer import DOMRenderer
from controller.browser_view import BrowserView
from controller.main_window import MainWindow
from shared.protocol import (
    STATUS_CONNECTED,
    STATUS_DISCONNECTED,
    CMD_CLICK,
)
from shared.messages import (
    create_full_snapshot,
    create_command,
    parse_message,
    serialize_message,
)


@pytest.fixture(scope="session")
def qapp():
    """Shared QApplication instance."""
    app_inst = QApplication.instance()
    if app_inst is None:
        app_inst = QApplication([])
    return app_inst


def test_dom_renderer_sanitization():
    """Verify DOMRenderer strips scripts, event handlers, and iframes for security."""
    dangerous_html = """
    <div>
        <h1>Safe Content</h1>
        <script>alert('malicious script');</script>
        <button onclick="badFunction()">Click</button>
        <a href="javascript:stealCookies()">Link</a>
        <iframe src="http://evil.com"></iframe>
    </div>
    """
    sanitized = DOMRenderer.sanitize_html(dangerous_html)

    assert "<script>" not in sanitized
    assert "alert(" not in sanitized
    assert "onclick=" not in sanitized
    assert "javascript:" not in sanitized
    assert "<iframe" not in sanitized
    assert "<h1>Safe Content</h1>" in sanitized


def test_dom_renderer_zstd_decompression():
    """Verify DOMRenderer decompresses zstandard compressed snapshots."""
    original_html = "<html><body><h1>Compressed Snapshot Test</h1><p>Data payload...</p></body></html>"

    # Compress with zstd
    cctx = zstd.ZstdCompressor(level=3)
    compressed_bytes = cctx.compress(original_html.encode("utf-8"))
    b64_compressed = base64.b64encode(compressed_bytes).decode("ascii")

    prepared = DOMRenderer.prepare_html_for_view(
        raw_html=b64_compressed,
        title="Compressed Test",
        url="https://example.com",
        compressed=True,
    )

    assert "Compressed Snapshot Test" in prepared
    assert "Data payload..." in prepared


def test_browser_view_status_badges_and_screenshot(qapp):
    """Verify BrowserView status badge updates and screenshot display."""
    view = BrowserView()
    view.show()

    # 1. Connected & Synchronized
    view.update_view(
        url="https://site.com/dashboard",
        title="Dashboard",
        raw_html="<h1>Dashboard</h1>",
        dom_version=15,
        is_stale=False,
        worker_status=STATUS_CONNECTED,
    )
    assert "Dashboard" in view.title_label.text()
    assert "https://site.com/dashboard" in view.url_label.text()
    assert "Synchronized (v15)" in view.status_badge.text()
    assert view.stack.currentIndex() == 0  # HTML view

    # 2. Stale
    view.update_view(
        url="https://site.com/dashboard",
        title="Dashboard",
        raw_html="<h1>Dashboard</h1>",
        dom_version=15,
        is_stale=True,
        worker_status=STATUS_CONNECTED,
    )
    assert "STALE" in view.status_badge.text()

    # 3. Disconnected
    view.update_view(
        url="",
        title="",
        raw_html="",
        dom_version=0,
        is_stale=True,
        worker_status=STATUS_DISCONNECTED,
    )
    assert "DISCONNECTED" in view.status_badge.text()

    # 4. Show screenshot
    # 1x1 transparent PNG base64
    sample_png_b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    view.show_screenshot(sample_png_b64)
    assert view.stack.currentIndex() == 1  # Screenshot view


class UvicornServerHelper:
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


@pytest.mark.asyncio
async def test_live_full_snapshot_rendering_in_controller(qapp):
    """
    Live End-to-End Test for Phase 7:
    Worker connects and sends FullSnapshot -> Controller UI automatically updates BrowserView with new snapshot.
    """
    server = UvicornServerHelper(app)
    server.start()

    worker_id = "worker-live-render"
    ws_server_url = f"ws://127.0.0.1:{server.port}/ws/worker"
    ctrl_server_url = f"ws://127.0.0.1:{server.port}/ws/controller"

    # Start Worker
    worker = Worker(
        worker_id=worker_id,
        server_url=ws_server_url,
        token=WORKER_TOKEN,
    )
    await worker.start()

    # Load initial page fixture in worker
    fixture1 = "<html><head><title>Initial Render Test</title></head><body><h1 id='test-h1'>Live Snapshot 1</h1></body></html>"
    worker.browser.driver.get(f"data:text/html;charset=utf-8,{fixture1}")

    window = MainWindow(
        server_url=ctrl_server_url,
        token=CONTROLLER_TOKEN,
    )
    window.show()

    try:
        await worker.ws_client.wait_until_connected(timeout=10.0)
        for _ in range(50):
            if window.ws_client._ws is not None:
                break
            await asyncio.sleep(0.1)

        # Select worker in controller window
        window.worker_mgr.update_worker_status(worker_id, STATUS_CONNECTED, 1)
        window.worker_mgr.select_worker(worker_id)

        # Allow WebSocket and Qt signals to exchange
        await asyncio.sleep(1.0)
        qapp.processEvents()

        # Send fresh full snapshot from worker
        await worker.send_full_snapshot()
        await asyncio.sleep(1.0)
        qapp.processEvents()

        # Verify BrowserView automatically rendered the snapshot
        text_rendered = window.browser_view.html_viewer.toPlainText()
        assert "Live Snapshot 1" in text_rendered
        assert "Initial Render Test" in window.browser_view.title_label.text()
        assert "Synchronized" in window.browser_view.status_badge.text()

        # Update page in Worker Chrome to Snapshot 2
        fixture2 = "<html><head><title>Updated Render Test</title></head><body><h1 id='test-h1'>Live Snapshot 2 - Dynamic Update</h1></body></html>"
        worker.browser.driver.get(f"data:text/html;charset=utf-8,{fixture2}")
        worker.dom_version = 2

        # Send updated FullSnapshot
        await worker.send_full_snapshot()
        await asyncio.sleep(1.0)
        qapp.processEvents()

        # Verify Controller UI updated automatically to Snapshot 2
        updated_text = window.browser_view.html_viewer.toPlainText()
        assert "Live Snapshot 2 - Dynamic Update" in updated_text
        assert "Updated Render Test" in window.browser_view.title_label.text()
        assert "v2" in window.browser_view.status_badge.text()

    finally:
        window.close()
        await worker.stop()
        server.stop()
