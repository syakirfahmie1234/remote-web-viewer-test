"""
Phase 14 Tests -- Dynamic Element Action Dispatch and Interactive Highlight

Verifies:
1. CMD_HIGHLIGHT is in COMMAND_ALLOWLIST.
2. CommandHandler._highlight_element() runs JS correctly.
3. CMD_HIGHLIGHT routes exclusively to active worker (queue isolation).
4. CMD_CLICK auto-highlight fires BEFORE element.click() (call-order mock).
5. CommandPanel.btn_highlight emits CMD_HIGHLIGHT signal with correct payload.
6. CommandPanel.btn_highlight styled with orange.
"""

from __future__ import annotations
import asyncio
import os
import time
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QApplication

from shared.protocol import (
    CMD_CLICK,
    CMD_HIGHLIGHT,
    COMMAND_ALLOWLIST,
)
from controller.command_panel import CommandPanel
from controller.command_queue import ControllerCommandQueue
from worker.command_handler import CommandHandler
from worker.browser import BrowserManager

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("HEADLESS", "true")


def _get_or_create_app():
    existing = QApplication.instance()
    if existing:
        return existing
    return QApplication([])


def test_cmd_highlight_in_allowlist():
    assert CMD_HIGHLIGHT in COMMAND_ALLOWLIST


def test_cmd_highlight_value():
    assert CMD_HIGHLIGHT == "highlight"


def test_command_panel_has_highlight_button():
    _get_or_create_app()
    panel = CommandPanel()
    assert hasattr(panel, "btn_highlight")
    btn = panel.btn_highlight
    assert btn.isEnabled()
    assert "FF6B00" in btn.styleSheet()


def test_command_panel_highlight_signal_emitted():
    _get_or_create_app()
    panel = CommandPanel()
    received = []
    panel.command_requested.connect(lambda cmd, pl: received.append((cmd, pl)))
    panel.selector_input.setText("#test-element")
    panel.btn_highlight.click()
    assert len(received) == 1
    cmd, payload = received[0]
    assert cmd == CMD_HIGHLIGHT
    assert payload.get("selector") == "#test-element"
    assert "duration_ms" in payload
    assert "color" in payload
    assert int(payload["duration_ms"]) > 0


def test_command_panel_highlight_no_emit_if_empty_selector():
    _get_or_create_app()
    panel = CommandPanel()
    received = []
    panel.command_requested.connect(lambda cmd, pl: received.append((cmd, pl)))
    panel.selector_input.setText("")
    panel.btn_highlight.click()
    assert len(received) == 0


def test_highlight_element_on_dead_browser_returns_zero():
    mock_browser = MagicMock(spec=BrowserManager)
    mock_browser.driver = None
    mock_browser.is_alive.return_value = False
    handler = CommandHandler(browser=mock_browser, worker_id="unit-test-worker")
    count = handler._highlight_element("#any", duration_ms=500)
    assert count == 0


def test_highlight_js_script_called_with_selector():
    mock_browser = MagicMock(spec=BrowserManager)
    mock_driver = MagicMock()
    mock_driver.execute_script.return_value = 2
    mock_browser.driver = mock_driver
    mock_browser.is_alive.return_value = True
    handler = CommandHandler(browser=mock_browser, worker_id="unit-test-worker")
    count = handler._highlight_element(".my-class", duration_ms=800, color="#FF6B00")
    assert count == 2
    mock_driver.execute_script.assert_called_once()
    args = mock_driver.execute_script.call_args[0]
    assert ".my-class" in args
    assert 800 in args
    assert "#FF6B00" in args


def test_highlight_js_handles_bad_selector_gracefully():
    mock_browser = MagicMock(spec=BrowserManager)
    mock_driver = MagicMock()
    mock_driver.execute_script.side_effect = Exception("invalid selector")
    mock_browser.driver = mock_driver
    mock_browser.is_alive.return_value = True
    handler = CommandHandler(browser=mock_browser, worker_id="unit-test-worker")
    count = handler._highlight_element("[[invalid]]", duration_ms=100)
    assert count == 0


def test_dispatch_highlight_command_via_execute():
    mock_browser = MagicMock(spec=BrowserManager)
    mock_driver = MagicMock()
    mock_driver.execute_script.return_value = 1
    mock_browser.driver = mock_driver
    mock_browser.is_alive.return_value = True
    handler = CommandHandler(browser=mock_browser, worker_id="unit-test-worker")
    from shared.models import CommandMessage
    msg = CommandMessage(
        type="command",
        message_id="msg-hl-01",
        timestamp=time.time(),
        protocol_version=1,
        worker_id="unit-test-worker",
        command=CMD_HIGHLIGHT,
        payload={"selector": "#target", "duration_ms": 500, "color": "#FF6B00"},
    )
    result = asyncio.run(handler.execute(msg))
    assert result.success is True
    assert result.payload.get("highlighted") == "#target"
    assert "elements_found" in result.payload


def test_dispatch_highlight_zero_elements_still_success():
    mock_browser = MagicMock(spec=BrowserManager)
    mock_driver = MagicMock()
    mock_driver.execute_script.return_value = 0
    mock_browser.driver = mock_driver
    mock_browser.is_alive.return_value = True
    handler = CommandHandler(browser=mock_browser, worker_id="unit-test-worker")
    from shared.models import CommandMessage
    msg = CommandMessage(
        type="command",
        message_id="msg-hl-02",
        timestamp=time.time(),
        protocol_version=1,
        worker_id="unit-test-worker",
        command=CMD_HIGHLIGHT,
        payload={"selector": ".nonexistent", "duration_ms": 300},
    )
    result = asyncio.run(handler.execute(msg))
    assert result.success is True
    assert result.payload["elements_found"] == 0


def test_click_command_calls_highlight_before_click():
    mock_browser = MagicMock(spec=BrowserManager)
    mock_driver = MagicMock()
    call_order = []

    def mock_execute_script(*args, **kwargs):
        call_order.append("highlight")
        return 1

    mock_element = MagicMock()
    mock_element.click.side_effect = lambda: call_order.append("click")
    mock_driver.execute_script = mock_execute_script
    mock_browser.driver = mock_driver
    mock_browser.is_alive.return_value = True

    with patch("worker.command_handler.WebDriverWait") as mock_wait_cls:
        mock_wait = MagicMock()
        mock_wait_cls.return_value = mock_wait
        mock_wait.until.return_value = mock_element
        handler = CommandHandler(browser=mock_browser, worker_id="unit-test-worker")
        from shared.models import CommandMessage
        msg = CommandMessage(
            type="command",
            message_id="msg-click-01",
            timestamp=time.time(),
            protocol_version=1,
            worker_id="unit-test-worker",
            command=CMD_CLICK,
            payload={"selector": "#btn"},
        )
        result = asyncio.run(handler.execute(msg))

    assert result.success is True
    assert call_order == ["highlight", "click"], f"Expected [highlight, click], got: {call_order}"


def test_highlight_routes_exclusively_to_active_worker():
    _get_or_create_app()
    sent_messages = []

    def capture_send(msg):
        sent_messages.append(msg)

    queue = ControllerCommandQueue(send_message_fn=capture_send)

    cmd_item = queue.enqueue_command(
        worker_id="worker-action-01",
        command=CMD_HIGHLIGHT,
        payload={"selector": "body", "duration_ms": 200, "color": "#FF6B00"},
    )

    count_01 = len(queue.get_items_for_worker("worker-action-01"))
    count_02 = len(queue.get_items_for_worker("worker-action-02"))

    assert count_01 >= 1
    assert count_02 == 0, f"CMD_HIGHLIGHT leaked to worker-action-02: count={count_02}"
    assert cmd_item.worker_id == "worker-action-01"
    assert cmd_item.command == CMD_HIGHLIGHT
    assert len(sent_messages) >= 1
    sent = sent_messages[0]
    assert hasattr(sent, "worker_id")
    assert sent.worker_id == "worker-action-01"