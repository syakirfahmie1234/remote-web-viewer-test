"""
Unit and integration tests for Zstandard Compression Tier.
Verifies compression threshold enforcement, transparent decompression,
bandwidth reduction (>50% savings benchmark), and end-to-end WebSocket compressed snapshot relay.
"""

import asyncio
import os
import threading
import time
import pytest
import uvicorn
from PySide6.QtWidgets import QApplication

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["WORKER_TOKEN"] = "test-comp-worker-token"
os.environ["CONTROLLER_TOKEN"] = "test-comp-ctrl-token"
os.environ["HEADLESS"] = "true"

from shared.compression import (
    compress_payload,
    decompress_payload,
    calculate_bandwidth_savings,
    COMPRESSION_THRESHOLD_BYTES,
)
from server.main import app
from server.config import WORKER_TOKEN, CONTROLLER_TOKEN
from worker.worker import Worker
from controller.main_window import MainWindow
from shared.protocol import STATUS_CONNECTED


@pytest.fixture(scope="session")
def qapp():
    """Shared QApplication instance."""
    app_inst = QApplication.instance()
    if app_inst is None:
        app_inst = QApplication([])
    return app_inst


def test_compression_threshold_small_vs_large():
    """Verify payloads below threshold stay uncompressed, while larger payloads are compressed."""
    # 1. Small payload (100 bytes)
    small_data = "<html><body><h1>Small Page</h1></body></html>"
    payload, is_comp = compress_payload(small_data, threshold=1024)
    assert is_comp is False
    assert payload == small_data

    # 2. Large payload (> 1024 bytes)
    large_data = "<div>" + "<p>Repetitive text line for testing DOM compression...</p>\n" * 50 + "</div>"
    assert len(large_data.encode("utf-8")) > 1024

    payload_large, is_comp_large = compress_payload(large_data, threshold=1024)
    assert is_comp_large is True
    assert payload_large != large_data


def test_transparent_decompression_with_unicode():
    """Verify compressed payloads with Unicode (Cyrillic, CJK, Emojis) decompress with 100% fidelity."""
    original_html = """
    <html>
        <head><title>Unicode Compression Test 🚀</title></head>
        <body>
            <h1>Привет мир! Привет вселенная! 🔥</h1>
            <p>CJK: 这是一个测试页面，用于测试压缩效果与多语言编码还原。日本語のテストです。</p>
            <ul>
    """ + "".join([f"<li>Item #{i}: Description with accents: café, naïve, résumé</li>\n" for i in range(100)]) + """
            </ul>
        </body>
    </html>
    """

    compressed_payload, is_comp = compress_payload(original_html, threshold=500)
    assert is_comp is True

    decompressed = decompress_payload(compressed_payload, is_compressed=True)
    assert decompressed == original_html
    assert "Привет мир!" in decompressed
    assert "这是一个测试页面" in decompressed
    assert "🚀" in decompressed


def test_bandwidth_reduction_metric_benchmark():
    """Verify realistic HTML snapshot compression achieves >50% bandwidth reduction."""
    # Realistic 25 KB HTML DOM snapshot
    table_rows = "".join([
        f"<tr id='row-{i}'><td class='col-id'>{i}</td><td class='col-name'>Product Name #{i}</td><td class='col-price'>$99.99</td></tr>\n"
        for i in range(300)
    ])
    html_page = f"<html><body><table class='data-table'><tbody>{table_rows}</tbody></table></body></html>"
    raw_size = len(html_page.encode("utf-8"))
    assert raw_size > 20000

    compressed_payload, is_comp = compress_payload(html_page)
    assert is_comp is True
    wire_size = len(compressed_payload.encode("ascii"))

    savings = calculate_bandwidth_savings(raw_size, wire_size)
    # HTML repetitive structures compress heavily (typically 80-92% with zstd)
    assert savings > 50.0, f"Expected >50% savings, got {savings:.1f}%"
    assert wire_size < (raw_size * 0.5)


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
async def test_end_to_end_compressed_snapshot_pipeline(qapp):
    """
    Live End-to-End Test for Phase 11:
    Worker captures large page (> 1 KB), zstd compresses it, sends over WebSocket ->
    Controller receives, transparently decompresses, and renders live in BrowserView.
    """
    server = UvicornServerHelper(app)
    server.start()

    worker_id = "worker-comp-e2e"
    ws_server_url = f"ws://127.0.0.1:{server.port}/ws/worker"
    ctrl_server_url = f"ws://127.0.0.1:{server.port}/ws/controller"

    # Start Worker
    worker = Worker(
        worker_id=worker_id,
        server_url=ws_server_url,
        token=WORKER_TOKEN,
    )
    await worker.start()

    # Load large HTML page fixture in Worker Chrome
    items_html = "".join([f"<li>Item {i} in large catalog list</li>" for i in range(100)])
    large_fixture = f"<html><head><title>Compressed Catalog Page</title></head><body><h1>Catalog Header</h1><ul>{items_html}</ul></body></html>"
    worker.browser.driver.get(f"data:text/html;charset=utf-8,{large_fixture}")

    window = MainWindow(
        server_url=ctrl_server_url,
        token=CONTROLLER_TOKEN,
    )
    window.show()

    try:
        await worker.ws_client.wait_until_connected(timeout=10.0)

        # Select worker in controller window
        window.worker_mgr.update_worker_status(worker_id, STATUS_CONNECTED, 1)
        window.worker_mgr.select_worker(worker_id)

        await asyncio.sleep(1.0)
        qapp.processEvents()

        # Send compressed snapshot from worker
        await worker.send_full_snapshot()
        await asyncio.sleep(1.0)
        qapp.processEvents()

        # Verify Controller slot and view received and decompressed the snapshot
        slot = window.state_mgr.get_slot(worker_id)
        assert slot is not None
        assert "Compressed Catalog Page" in slot.title
        assert "Item 99 in large catalog list" in slot.html

        # Verify BrowserView rendered decompressed text
        text_rendered = window.browser_view.html_viewer.toPlainText()
        assert "Catalog Header" in text_rendered
        assert "Item 50 in large catalog list" in text_rendered

    finally:
        window.close()
        await worker.stop()
        server.stop()
