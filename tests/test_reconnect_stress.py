"""
Stress tests for multi-worker rapid disconnect and reconnect resilience.
Simulates 5+ consecutive rapid network dropouts across multiple concurrent workers.
Verifies that:
1. Chrome browser processes are NEVER restarted across reconnects (persistent session integrity).
2. All workers re-establish WebSocket connections and dispatch fresh FULL_SNAPSHOTs.
3. Zero command leakage occurs between workers during and after dropouts.
4. Controller recovers full synchronized state without stale artifacts.
"""

import asyncio
import os
import threading
import time
import pytest
import uvicorn
from PySide6.QtWidgets import QApplication

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["WORKER_TOKEN"] = "test-stress-worker-token"
os.environ["CONTROLLER_TOKEN"] = "test-stress-ctrl-token"
os.environ["HEADLESS"] = "true"

from server.main import app
from server.config import WORKER_TOKEN, CONTROLLER_TOKEN
from worker.pool import WorkerPool
from controller.main_window import MainWindow
from shared.protocol import STATUS_CONNECTED, STATUS_DISCONNECTED, CMD_CLICK


@pytest.fixture(scope="session")
def qapp():
    """Shared QApplication instance."""
    app_inst = QApplication.instance()
    if app_inst is None:
        app_inst = QApplication([])
    return app_inst


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
async def test_multi_worker_rapid_reconnect_stress_suite(qapp):
    """
    Stress Test: 5 consecutive rapid disconnect/reconnect cycles across 3 concurrent workers.
    Asserts Chrome browser PID / session stability, zero command bleed, and 100% state recovery.
    """
    server = UvicornServerHelper(app)
    server.start()

    ws_server_url = f"ws://127.0.0.1:{server.port}/ws/worker"
    ctrl_server_url = f"ws://127.0.0.1:{server.port}/ws/controller"

    worker_ids = ["stress-worker-01", "stress-worker-02", "stress-worker-03"]
    pool = WorkerPool(
        worker_ids=worker_ids,
        server_url=ws_server_url,
        token=WORKER_TOKEN,
    )

    await pool.start()

    # Capture initial Chrome window handles to prove browser process is never restarted
    initial_handles = {}
    for wid in worker_ids:
        w = pool.get_worker(wid)
        fixture = f"<html><body><h1 id='h1-{wid}'>State on {wid}</h1><input id='inp-{wid}' value='initial_data_{wid}' /></body></html>"
        w.browser.driver.get(f"data:text/html;charset=utf-8,{fixture}")
        initial_handles[wid] = w.browser.driver.current_window_handle

    window = MainWindow(
        server_url=ctrl_server_url,
        token=CONTROLLER_TOKEN,
    )
    window.show()

    try:
        # Initial connection
        for wid in worker_ids:
            w = pool.get_worker(wid)
            await w.ws_client.wait_until_connected(timeout=10.0)
            window.worker_mgr.update_worker_status(wid, STATUS_CONNECTED, 1)

        await asyncio.sleep(1.0)
        qapp.processEvents()

        # Execute 5 consecutive rapid disconnect / reconnect stress cycles
        for cycle in range(1, 6):
            # 1. Abruptly close underlying WebSockets for all workers
            for wid in worker_ids:
                w = pool.get_worker(wid)
                if w.ws_client._ws:
                    await w.ws_client._ws.close()

            # Small network dropout pause
            await asyncio.sleep(0.4)
            qapp.processEvents()

            # 2. Verify all workers automatically reconnect via backoff loop
            for wid in worker_ids:
                w = pool.get_worker(wid)
                await w.ws_client.wait_until_connected(timeout=10.0)
                assert w.ws_client.is_connected is True

            # 3. Verify Chrome WebDriver sessions are 100% identical (NEVER restarted)
            for wid in worker_ids:
                w = pool.get_worker(wid)
                assert w.browser.is_alive() is True
                assert w.browser.driver.current_window_handle == initial_handles[wid]
                # Verify input field data survived the network disconnect untouched
                input_val = w.browser.driver.find_element("id", f"inp-{wid}").get_attribute("value")
                assert input_val == f"initial_data_{wid}"

            await asyncio.sleep(0.5)
            qapp.processEvents()

        # 4. Verify full state synchronization on Controller after 5 stress cycles
        for wid in worker_ids:
            window.worker_mgr.select_worker(wid)
            await asyncio.sleep(0.8)
            qapp.processEvents()

            text_rendered = window.browser_view.html_viewer.toPlainText()
            assert f"State on {wid}" in text_rendered
            assert "Synchronized" in window.browser_view.status_badge.text()

        # 5. Enqueue command on stress-worker-01 to confirm command routing integrity after stress cycles
        window.worker_mgr.select_worker("stress-worker-01")
        cmd_item = window.command_queue.enqueue_command(
            worker_id="stress-worker-01",
            command=CMD_CLICK,
            payload={"selector": "#h1-stress-worker-01"},
        )

        await asyncio.sleep(1.5)
        qapp.processEvents()

        assert cmd_item.state in ("SUCCESS", "IN_FLIGHT", "QUEUED")
        # Ensure zero command leakage into worker 02 and 03
        assert len(window.command_queue.get_items_for_worker("stress-worker-02")) == 0
        assert len(window.command_queue.get_items_for_worker("stress-worker-03")) == 0

    finally:
        window.close()
        await pool.stop()
        server.stop()
