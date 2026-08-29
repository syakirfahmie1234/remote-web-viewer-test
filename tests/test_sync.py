"""
Unit and integration tests for Controller State Manager, DOM Synchronization, and PySide6 UI Pipeline.
Verifies independent state tracking per worker_id, version validation, snapshot rendering,
and end-to-end command dispatch through the full pipeline without local website actions.
"""

import asyncio
import base64
import os
import threading
import time
import pytest
import uvicorn
import websockets
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

# Force offscreen rendering for headless Qt testing
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["WORKER_TOKEN"] = "test-sync-worker-token"
os.environ["CONTROLLER_TOKEN"] = "test-sync-ctrl-token"
os.environ["HEADLESS"] = "true"

from server.main import app
from server.config import WORKER_TOKEN, CONTROLLER_TOKEN
from worker.worker import Worker
from controller.state_manager import ControllerStateManager
from controller.worker_manager import ControllerWorkerManager
from controller.main_window import MainWindow
from shared.protocol import (
    STATUS_CONNECTED,
    STATUS_DISCONNECTED,
    CMD_CLICK,
    CMD_TYPE,
)
from shared.models import (
    FullSnapshotMessage,
    DomUpdateMessage,
    CommandResultMessage,
    DOMDiffOp,
)
from shared.messages import (
    create_full_snapshot,
    create_dom_update,
    create_command_result,
    create_command,
    parse_message,
)


@pytest.fixture(scope="session")
def qapp():
    """Shared QApplication instance."""
    app_inst = QApplication.instance()
    if app_inst is None:
        app_inst = QApplication([])
    return app_inst


def test_state_manager_independent_worker_slots():
    """Verify ControllerStateManager maintains independent state slots per worker_id."""
    state_mgr = ControllerStateManager()

    # Apply snapshot for Worker A
    snap_a = create_full_snapshot(
        worker_id="worker-alpha",
        version=10,
        url="https://example.com/alpha",
        title="Alpha Page",
        html="<div>Alpha</div>",
    )
    state_mgr.apply_full_snapshot(snap_a)

    # Apply snapshot for Worker B
    snap_b = create_full_snapshot(
        worker_id="worker-beta",
        version=40,
        url="https://example.com/beta",
        title="Beta Page",
        html="<div>Beta</div>",
    )
    state_mgr.apply_full_snapshot(snap_b)

    slot_a = state_mgr.get_slot("worker-alpha")
    slot_b = state_mgr.get_slot("worker-beta")

    assert slot_a is not None
    assert slot_b is not None

    # Verify complete state isolation
    assert slot_a.worker_id == "worker-alpha"
    assert slot_a.dom_version == 10
    assert slot_a.url == "https://example.com/alpha"
    assert slot_a.is_stale is False

    assert slot_b.worker_id == "worker-beta"
    assert slot_b.dom_version == 40
    assert slot_b.url == "https://example.com/beta"
    assert slot_b.is_stale is False

    # Apply DOM update only to Worker A
    diff_a = create_dom_update(
        worker_id="worker-alpha",
        base_version=10,
        version=11,
        ops=[],
    )
    success = state_mgr.apply_dom_update(diff_a)
    assert success is True
    assert slot_a.dom_version == 11
    # Worker B must remain completely untouched
    assert slot_b.dom_version == 40


def test_state_manager_version_mismatch_flags_stale():
    """Verify version mismatch flags slot as stale and rejects update."""
    state_mgr = ControllerStateManager()
    snap = create_full_snapshot("worker-01", 100, "https://site.com", "Title", "<html></html>")
    state_mgr.apply_full_snapshot(snap)

    slot = state_mgr.get_slot("worker-01")
    assert slot.is_stale is False
    assert slot.dom_version == 100

    # Incoming update with base_version=105 (mismatch)
    bad_diff = create_dom_update("worker-01", base_version=105, version=106, ops=[])
    applied = state_mgr.apply_dom_update(bad_diff)

    assert applied is False
    assert slot.is_stale is True  # Flagged stale


def test_worker_manager_switching_and_signals(qapp):
    """Verify ControllerWorkerManager emits signals and manages active selection."""
    wm = ControllerWorkerManager()
    events = []

    wm.workers_updated.connect(lambda: events.append("updated"))
    wm.active_worker_changed.connect(lambda wid: events.append(f"active:{wid}"))

    wm.update_worker_status("worker-01", STATUS_CONNECTED, 10)
    assert wm.active_worker_id == "worker-01"

    wm.update_worker_status("worker-02", STATUS_CONNECTED, 20)
    wm.select_worker("worker-02")
    assert wm.active_worker_id == "worker-02"

    assert "active:worker-01" in events
    assert "active:worker-02" in events


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
async def test_full_controller_ui_command_pipeline(qapp):
    """
    Core Phase 5 Test:
    UI dispatches a command tagged with active worker_id through the full pipeline:
    MainWindow -> WebSocket -> Server -> Worker Selenium -> Server -> MainWindow
    Displays result in UI and never acts on the real website locally.
    """
    server = UvicornServerHelper(app)
    server.start()

    worker_id = "worker-gui-pipeline"
    ws_server_url = f"ws://127.0.0.1:{server.port}/ws/worker"
    ctrl_server_url = f"ws://127.0.0.1:{server.port}/ws/controller"

    # Start Worker
    worker = Worker(
        worker_id=worker_id,
        server_url=ws_server_url,
        token=WORKER_TOKEN,
    )
    await worker.start()

    # Load interactive fixture into worker browser
    fixture = """
    <html>
        <body>
            <h1 id="status-title">Ready</h1>
            <button id="submit-btn" onclick="document.getElementById('status-title').innerText='Submitted!';">Submit</button>
        </body>
    </html>
    """
    encoded = base64.b64encode(fixture.encode("utf-8")).decode("ascii")
    worker.browser.driver.get(f"data:text/html;base64,{encoded}")

    # Start PySide6 MainWindow
    window = MainWindow(
        server_url=ctrl_server_url,
        token=CONTROLLER_TOKEN,
    )

    try:
        # Wait for worker connection
        await worker.ws_client.wait_until_connected(timeout=10.0)

        # Set active worker in window
        window.worker_mgr.update_worker_status(worker_id, STATUS_CONNECTED, 1)
        window.worker_mgr.select_worker(worker_id)

        # Allow Qt and WebSocket events to process
        await asyncio.sleep(1.0)
        qapp.processEvents()

        # Simulate user requesting CLICK via CommandPanel
        window.command_panel.selector_input.setText("#submit-btn")
        window.command_panel._on_click_clicked()

        # Wait for command execution round-trip
        await asyncio.sleep(1.5)
        qapp.processEvents()

        # Verify: Worker browser DOM was updated via Selenium
        assert worker.browser.driver.find_element("css selector", "#status-title").text == "Submitted!"

        # Verify: Controller UI recorded success metric
        slot = window.state_mgr.get_slot(worker_id)
        assert slot is not None
        assert slot.commands_sent >= 1
        assert slot.commands_failed == 0

    finally:
        window.close()
        await worker.stop()
        server.stop()
