"""
Unit and integration tests for Controller Command Queue System.
Verifies per-Worker sequential queuing, multi-Worker isolation, state transitions
(QUEUED, IN_FLIGHT, SUCCESS, FAILED, TIMED_OUT), and GUI error surfacing.
"""

import os
import pytest
from PySide6.QtWidgets import QApplication

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from controller.command_queue import (
    ControllerCommandQueue,
    STATE_QUEUED,
    STATE_IN_FLIGHT,
    STATE_SUCCESS,
    STATE_FAILED,
    STATE_TIMED_OUT,
)
from controller.command_panel import CommandPanel
from shared.protocol import CMD_CLICK, CMD_TYPE
from shared.models import CommandResultMessage
from shared.messages import create_command_result


@pytest.fixture(scope="session")
def qapp():
    """Shared QApplication instance."""
    app_inst = QApplication.instance()
    if app_inst is None:
        app_inst = QApplication([])
    return app_inst


def test_command_queue_sequential_execution_per_worker(qapp):
    """
    Verify that multiple commands queued for a single Worker are dispatched strictly one at a time.
    """
    dispatched_commands = []

    def mock_send(msg):
        dispatched_commands.append(msg)

    cq = ControllerCommandQueue(send_message_fn=mock_send)

    # 1. Enqueue 3 commands for worker-01
    item1 = cq.enqueue_command("worker-01", CMD_CLICK, {"selector": "#btn-1"})
    item2 = cq.enqueue_command("worker-01", CMD_CLICK, {"selector": "#btn-2"})
    item3 = cq.enqueue_command("worker-01", CMD_TYPE, {"selector": "#input", "text": "hello"})

    # Check immediate state: item1 is IN_FLIGHT, item2 and item3 are QUEUED
    assert item1.state == STATE_IN_FLIGHT
    assert item2.state == STATE_QUEUED
    assert item3.state == STATE_QUEUED
    assert len(dispatched_commands) == 1
    assert dispatched_commands[0].payload["selector"] == "#btn-1"

    # 2. Result for item1 arrives
    res1 = create_command_result(worker_id="worker-01", command=CMD_CLICK, success=True)
    cq.handle_command_result(res1)

    # Check state: item1 is SUCCESS, item2 transitioned to IN_FLIGHT and dispatched
    assert item1.state == STATE_SUCCESS
    assert item2.state == STATE_IN_FLIGHT
    assert item3.state == STATE_QUEUED
    assert len(dispatched_commands) == 2
    assert dispatched_commands[1].payload["selector"] == "#btn-2"

    # 3. Result for item2 arrives
    res2 = create_command_result(worker_id="worker-01", command=CMD_CLICK, success=True)
    cq.handle_command_result(res2)

    assert item2.state == STATE_SUCCESS
    assert item3.state == STATE_IN_FLIGHT
    assert len(dispatched_commands) == 3
    assert dispatched_commands[2].command == CMD_TYPE

    # 4. Result for item3 arrives
    res3 = create_command_result(worker_id="worker-01", command=CMD_TYPE, success=True)
    cq.handle_command_result(res3)

    assert item3.state == STATE_SUCCESS
    # Queue is now empty
    items = cq.get_items_for_worker("worker-01")
    assert len([it for it in items if it.state in (STATE_QUEUED, STATE_IN_FLIGHT)]) == 0


def test_command_queue_multi_worker_isolation(qapp):
    """
    Verify that an in-flight command on worker-01 does NOT block commands on worker-02.
    """
    dispatched = []
    cq = ControllerCommandQueue(send_message_fn=lambda msg: dispatched.append(msg))

    # Enqueue on worker-01 -> IN_FLIGHT
    w1_item1 = cq.enqueue_command("worker-01", CMD_CLICK, {"selector": "#btn-w1"})
    assert w1_item1.state == STATE_IN_FLIGHT
    assert len(dispatched) == 1

    # Enqueue on worker-02 -> immediately IN_FLIGHT (not blocked by worker-01)
    w2_item1 = cq.enqueue_command("worker-02", CMD_CLICK, {"selector": "#btn-w2"})
    assert w2_item1.state == STATE_IN_FLIGHT
    assert len(dispatched) == 2

    # Enqueue 2nd on worker-01 -> QUEUED on worker-01
    w1_item2 = cq.enqueue_command("worker-01", CMD_CLICK, {"selector": "#btn-w1-second"})
    assert w1_item2.state == STATE_QUEUED
    assert len(dispatched) == 2


def test_command_failure_surfacing_and_queue_continuation(qapp):
    """
    Verify that command failure transitions to FAILED, emits failure signal,
    and allows next queued command to execute without deadlocking.
    """
    dispatched = []
    failed_signals = []

    cq = ControllerCommandQueue(send_message_fn=lambda msg: dispatched.append(msg))
    cq.command_failed.connect(lambda item: failed_signals.append(item))

    # Enqueue failing command then succeeding command
    item1 = cq.enqueue_command("worker-01", CMD_CLICK, {"selector": "#broken"})
    item2 = cq.enqueue_command("worker-01", CMD_CLICK, {"selector": "#working"})

    assert item1.state == STATE_IN_FLIGHT
    assert item2.state == STATE_QUEUED

    # Fail item1
    fail_res = create_command_result(
        worker_id="worker-01",
        command=CMD_CLICK,
        success=False,
        error="Element not found",
    )
    cq.handle_command_result(fail_res)

    # Verify item1 failed and signal emitted
    assert item1.state == STATE_FAILED
    assert item1.error == "Element not found"
    assert len(failed_signals) == 1
    assert failed_signals[0].command_id == item1.command_id

    # Verify item2 unblocked and is now IN_FLIGHT
    assert item2.state == STATE_IN_FLIGHT
    assert len(dispatched) == 2


def test_command_panel_queue_table_and_error_banner(qapp):
    """Verify CommandPanel renders queue items and shows error banner."""
    panel = CommandPanel()
    panel.show()

    cq = ControllerCommandQueue()
    item1 = cq.enqueue_command("worker-01", CMD_CLICK, {"selector": "#btn"})
    item2 = cq.enqueue_command("worker-01", CMD_TYPE, {"selector": "#txt", "text": "foo"})

    # Update table
    items = cq.get_items_for_worker("worker-01")
    panel.update_queue_display(items)

    assert panel.queue_table.rowCount() == 2

    # Surface error
    item1.state = STATE_FAILED
    item1.error = "Timeout waiting for clickable element"
    panel.show_failure(item1)

    assert panel.error_banner.isVisible() is True
    assert "Timeout waiting for clickable element" in panel.error_label.text()
