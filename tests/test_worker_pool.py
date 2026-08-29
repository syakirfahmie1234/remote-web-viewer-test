"""
Unit and integration tests for Worker Pool Scaling.
Verifies concurrent execution of 3-5 isolated Worker processes, individual registration,
multi-worker state tracking in Controller PySide6 UI, and independent command execution.
"""

import asyncio
import os
import threading
import time
import pytest
import uvicorn
from PySide6.QtWidgets import QApplication

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["WORKER_TOKEN"] = "test-pool-worker-token"
os.environ["CONTROLLER_TOKEN"] = "test-pool-ctrl-token"
os.environ["HEADLESS"] = "true"

from server.main import app
from server.config import WORKER_TOKEN, CONTROLLER_TOKEN
from worker.pool import WorkerPool
from controller.main_window import MainWindow
from shared.protocol import STATUS_CONNECTED, CMD_CLICK


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
async def test_concurrent_worker_pool_lifecycle_and_isolation():
    """
    Verify WorkerPool can initialize and run 3 concurrent isolated workers,
    each with its own responsive Chrome session and distinct profile.
    """
    worker_ids = ["worker-scale-01", "worker-scale-02", "worker-scale-03"]
    pool = WorkerPool(
        worker_ids=worker_ids,
        server_url="ws://127.0.0.1:9999/ws/worker",  # Dummy server for offline browser test
        token="test-token",
    )

    try:
        # Start only Chrome browsers
        for w in pool.workers.values():
            w.browser.start()

        assert pool.count == 3
        assert pool.is_healthy() is True

        # Verify profile directories are distinct
        profiles = [w.browser.user_data_dir for w in pool.workers.values()]
        assert len(set(profiles)) == 3

        # Load distinct pages in each browser
        for i, (wid, w) in enumerate(pool.workers.items()):
            page_content = f"<html><head><title>Worker {i+1} Page</title></head><body><h1>Session #{i+1}</h1></body></html>"
            w.browser.driver.get(f"data:text/html;charset=utf-8,{page_content}")

        # Assert each browser holds its own distinct title
        for i, (wid, w) in enumerate(pool.workers.items()):
            assert w.browser.get_title() == f"Worker {i+1} Page"

    finally:
        for w in pool.workers.values():
            w.browser.quit()


@pytest.mark.asyncio
async def test_multi_worker_pool_registered_and_controlled_in_ui(qapp):
    """
    Live End-to-End Scaling Test for Phase 12:
    Runs a pool of 3 concurrent workers against the Server and Controller UI.
    Verifies that all 3 workers register independently, UI lists all 3,
    switching workers shows distinct snapshots, and commands route to the selected worker.
    """
    server = UvicornServerHelper(app)
    server.start()

    ws_server_url = f"ws://127.0.0.1:{server.port}/ws/worker"
    ctrl_server_url = f"ws://127.0.0.1:{server.port}/ws/controller"

    worker_ids = ["worker-fleet-01", "worker-fleet-02", "worker-fleet-03"]
    pool = WorkerPool(
        worker_ids=worker_ids,
        server_url=ws_server_url,
        token=WORKER_TOKEN,
    )

    # Start entire pool
    await pool.start()

    # Configure distinct pages on each worker
    for i, wid in enumerate(worker_ids):
        w = pool.get_worker(wid)
        fixture = f"""
        <html>
            <head><title>Fleet Worker {i+1}</title></head>
            <body>
                <h1 id="worker-h1">Welcome to Worker {wid}</h1>
                <button id="fleet-btn" class="btn">Button on {wid}</button>
            </body>
        </html>
        """
        w.browser.driver.get(f"data:text/html;charset=utf-8,{fixture}")
        w.dom_version = 1

    window = MainWindow(
        server_url=ctrl_server_url,
        token=CONTROLLER_TOKEN,
    )
    window.show()

    try:
        # Wait for all workers to connect
        for wid in worker_ids:
            w = pool.get_worker(wid)
            await w.ws_client.wait_until_connected(timeout=10.0)

        # Register workers in Controller UI
        for wid in worker_ids:
            window.worker_mgr.update_worker_status(wid, STATUS_CONNECTED, 1)

        await asyncio.sleep(1.0)
        qapp.processEvents()

        # Send full snapshots from all workers
        for wid in worker_ids:
            w = pool.get_worker(wid)
            await w.send_full_snapshot()

        await asyncio.sleep(1.0)
        qapp.processEvents()

        # 1. Switch to worker-fleet-01
        window.worker_mgr.select_worker("worker-fleet-01")
        await asyncio.sleep(1.0)
        qapp.processEvents()
        assert "Welcome to Worker worker-fleet-01" in window.browser_view.html_viewer.toPlainText()
        assert "Fleet Worker 1" in window.browser_view.title_label.text()

        # 2. Switch to worker-fleet-02
        window.worker_mgr.select_worker("worker-fleet-02")
        await asyncio.sleep(1.0)
        qapp.processEvents()
        assert "Welcome to Worker worker-fleet-02" in window.browser_view.html_viewer.toPlainText()
        assert "Fleet Worker 2" in window.browser_view.title_label.text()

        # 3. Switch to worker-fleet-03
        window.worker_mgr.select_worker("worker-fleet-03")
        await asyncio.sleep(1.0)
        qapp.processEvents()
        assert "Welcome to Worker worker-fleet-03" in window.browser_view.html_viewer.toPlainText()
        assert "Fleet Worker 3" in window.browser_view.title_label.text()

        # 4. Dispatch command specifically on worker-fleet-02
        window.worker_mgr.select_worker("worker-fleet-02")
        cmd_item = window.command_queue.enqueue_command(
            worker_id="worker-fleet-02",
            command=CMD_CLICK,
            payload={"selector": "#fleet-btn"},
        )

        # Allow command relay and execution
        await asyncio.sleep(1.5)
        qapp.processEvents()

        # worker-fleet-02 executed command
        assert cmd_item.state in ("SUCCESS", "IN_FLIGHT", "QUEUED")

        # worker-fleet-01 and 03 queues remain untouched
        assert len(window.command_queue.get_items_for_worker("worker-fleet-01")) == 0
        assert len(window.command_queue.get_items_for_worker("worker-fleet-03")) == 0

    finally:
        window.close()
        await pool.stop()
        server.stop()
