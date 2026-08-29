"""
Unit and integration tests for Worker Selenium command execution.
Verifies all allowlisted commands (navigate, click, type, clear, keypress, scroll, back,
forward, refresh, screenshot), explicit waits, error handling for missing/invalid selectors,
and worker_id tagging on all results.
"""

import asyncio
import base64
import os
import threading
import time
import pytest
import uvicorn
import websockets

# Test configuration
os.environ["WORKER_TOKEN"] = "test-cmd-worker-token"
os.environ["CONTROLLER_TOKEN"] = "test-cmd-ctrl-token"
os.environ["HEADLESS"] = "true"
os.environ["TARGET_DOMAIN"] = "https://example.com"

from server.main import app
from server.config import WORKER_TOKEN, CONTROLLER_TOKEN
from worker.browser import BrowserManager
from worker.command_handler import CommandHandler
from worker.worker import Worker
from shared.protocol import (
    CMD_NAVIGATE,
    CMD_CLICK,
    CMD_TYPE,
    CMD_CLEAR,
    CMD_KEYPRESS,
    CMD_SCROLL,
    CMD_BACK,
    CMD_FORWARD,
    CMD_REFRESH,
    CMD_SCREENSHOT,
    MSG_COMMAND_RESULT,
)
from shared.messages import (
    create_command,
    parse_message,
    serialize_message,
)

# HTML fixture page for testing interactive controls
FIXTURE_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Command Test Page</title>
    <style>
        body { margin: 0; padding: 20px; font-family: sans-serif; height: 3000px; }
        #scroll-container { width: 300px; height: 150px; overflow: scroll; border: 1px solid #ccc; }
        #scroll-content { height: 1000px; }
    </style>
</head>
<body>
    <h1 id="heading">Original Heading</h1>
    <input type="text" id="username" value="InitialValue">
    <button id="action-btn" onclick="document.getElementById('heading').innerText = 'Button Clicked!';">Click Me</button>
    <div id="output"></div>
    <div id="scroll-container">
        <div id="scroll-content">Scrollable inner text</div>
    </div>
    <script>
        document.getElementById('username').addEventListener('keydown', function(e) {
            if (e.key === 'Enter') {
                document.getElementById('output').innerText = 'Enter Pressed: ' + this.value;
            }
        });
    </script>
</body>
</html>
"""


@pytest.fixture(scope="module")
def browser_instance():
    """Shared headless Chrome browser fixture for command handler tests."""
    browser = BrowserManager()
    browser.start()
    yield browser
    browser.quit()


@pytest.fixture
def cmd_handler(browser_instance):
    """CommandHandler configured for worker-test-01."""
    # Reset page before each test
    encoded = base64.b64encode(FIXTURE_HTML.encode("utf-8")).decode("ascii")
    browser_instance.driver.get(f"data:text/html;base64,{encoded}")
    return CommandHandler(browser=browser_instance, worker_id="worker-test-01", default_timeout=2.0)


@pytest.mark.asyncio
async def test_command_click(cmd_handler, browser_instance):
    """Test click command with explicit wait updates the DOM."""
    msg = create_command(worker_id="worker-test-01", command=CMD_CLICK, payload={"selector": "#action-btn"})
    result = await cmd_handler.execute(msg)

    assert result.success is True
    assert result.worker_id == "worker-test-01"
    assert result.command == CMD_CLICK
    assert result.error is None

    # Verify DOM changed
    heading_text = browser_instance.driver.find_element("css selector", "#heading").text
    assert heading_text == "Button Clicked!"


@pytest.mark.asyncio
async def test_command_type_and_clear(cmd_handler, browser_instance):
    """Test type and clear commands on input elements."""
    # 1. Type with clear_first=True
    type_msg = create_command(
        worker_id="worker-test-01",
        command=CMD_TYPE,
        payload={"selector": "#username", "text": "Dr. Smith", "clear_first": True},
    )
    res1 = await cmd_handler.execute(type_msg)
    assert res1.success is True
    assert res1.worker_id == "worker-test-01"

    val1 = browser_instance.driver.find_element("css selector", "#username").get_attribute("value")
    assert val1 == "Dr. Smith"

    # 2. Clear input
    clear_msg = create_command(worker_id="worker-test-01", command=CMD_CLEAR, payload={"selector": "#username"})
    res2 = await cmd_handler.execute(clear_msg)
    assert res2.success is True
    val2 = browser_instance.driver.find_element("css selector", "#username").get_attribute("value")
    assert val2 == ""


@pytest.mark.asyncio
async def test_command_keypress(cmd_handler, browser_instance):
    """Test keypress sends keyboard events properly."""
    # Set input text first
    await cmd_handler.execute(
        create_command(
            worker_id="worker-test-01",
            command=CMD_TYPE,
            payload={"selector": "#username", "text": "SearchQuery", "clear_first": True},
        )
    )

    # Send Enter key
    key_msg = create_command(
        worker_id="worker-test-01",
        command=CMD_KEYPRESS,
        payload={"selector": "#username", "key": "enter"},
    )
    res = await cmd_handler.execute(key_msg)
    assert res.success is True

    # Output div should update based on JS event listener
    output_text = browser_instance.driver.find_element("css selector", "#output").text
    assert output_text == "Enter Pressed: SearchQuery"


@pytest.mark.asyncio
async def test_command_scroll(cmd_handler, browser_instance):
    """Test scroll command on window and container elements."""
    # Window scroll
    res_win = await cmd_handler.execute(
        create_command(worker_id="worker-test-01", command=CMD_SCROLL, payload={"x": 0, "y": 300})
    )
    assert res_win.success is True
    assert res_win.payload["scrolled_y"] == 300

    # Container scroll
    res_box = await cmd_handler.execute(
        create_command(
            worker_id="worker-test-01",
            command=CMD_SCROLL,
            payload={"selector": "#scroll-container", "x": 0, "y": 120},
        )
    )
    assert res_box.success is True


@pytest.mark.asyncio
async def test_command_screenshot(cmd_handler):
    """Test screenshot command returns valid base64 image data."""
    msg = create_command(worker_id="worker-test-01", command=CMD_SCREENSHOT, payload={})
    res = await cmd_handler.execute(msg)

    assert res.success is True
    assert res.worker_id == "worker-test-01"
    assert "screenshot_base64" in res.payload
    assert len(res.payload["screenshot_base64"]) > 100


@pytest.mark.asyncio
async def test_command_history_and_refresh(cmd_handler, browser_instance):
    """Test back, forward, and refresh commands."""
    res_ref = await cmd_handler.execute(
        create_command(worker_id="worker-test-01", command=CMD_REFRESH, payload={})
    )
    assert res_ref.success is True

    res_back = await cmd_handler.execute(
        create_command(worker_id="worker-test-01", command=CMD_BACK, payload={})
    )
    assert res_back.success is True

    res_fwd = await cmd_handler.execute(
        create_command(worker_id="worker-test-01", command=CMD_FORWARD, payload={})
    )
    assert res_fwd.success is True


@pytest.mark.asyncio
async def test_missing_element_timeout_returns_failure_without_crashing(cmd_handler):
    """
    Verify that an element that does not exist triggers a TimeoutException
    and returns a structured error without crashing the worker.
    """
    msg = create_command(
        worker_id="worker-test-01",
        command=CMD_CLICK,
        payload={"selector": "#non-existent-button-12345"},
    )
    res = await cmd_handler.execute(msg)

    assert res.success is False
    assert res.worker_id == "worker-test-01"
    assert res.error is not None
    assert "Timed out" in res.error or "not found" in res.error


@pytest.mark.asyncio
async def test_invalid_selector_returns_failure_without_crashing(cmd_handler):
    """Verify invalid CSS selector is caught and returned as failure."""
    msg = create_command(
        worker_id="worker-test-01",
        command=CMD_CLICK,
        payload={"selector": "///invalid[[[selector"},
    )
    res = await cmd_handler.execute(msg)

    assert res.success is False
    assert res.worker_id == "worker-test-01"
    assert res.error is not None


# Live End-to-End WebSocket Test for Commands

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
async def test_end_to_end_command_execution_over_websocket():
    """
    Full End-to-End Integration:
    Controller -> Server -> Worker (executes Selenium command) -> Server -> Controller
    """
    server = UvicornServerHelper(app)
    server.start()

    worker_id = "worker-e2e-cmd"
    ws_server_url = f"ws://127.0.0.1:{server.port}/ws/worker"
    ctrl_server_url = f"ws://127.0.0.1:{server.port}/ws/controller"

    worker = Worker(
        worker_id=worker_id,
        server_url=ws_server_url,
        token=WORKER_TOKEN,
    )
    await worker.start()

    try:
        # Wait for worker connection
        await worker.ws_client.wait_until_connected(timeout=10.0)

        # Connect controller
        ctrl_url = f"{ctrl_server_url}?token={CONTROLLER_TOKEN}&worker_id={worker_id}"
        async with websockets.connect(ctrl_url) as ctrl_ws:
            # Navigate worker to test fixture first
            encoded = base64.b64encode(FIXTURE_HTML.encode("utf-8")).decode("ascii")
            worker.browser.driver.get(f"data:text/html;base64,{encoded}")

            # Controller sends CLICK command
            cmd = create_command(worker_id=worker_id, command=CMD_CLICK, payload={"selector": "#action-btn"})
            await ctrl_ws.send(serialize_message(cmd))

            # Receive until we get the COMMAND_RESULT
            res_msg = None
            for _ in range(5):
                raw = await asyncio.wait_for(ctrl_ws.recv(), timeout=5.0)
                parsed = parse_message(raw)
                if parsed.type == MSG_COMMAND_RESULT:
                    res_msg = parsed
                    break

            assert res_msg is not None
            assert res_msg.type == MSG_COMMAND_RESULT
            assert res_msg.worker_id == worker_id
            assert res_msg.command == CMD_CLICK
            assert res_msg.success is True

            # Verify actual browser executed it
            heading = worker.browser.driver.find_element("css selector", "#heading").text
            assert heading == "Button Clicked!"

    finally:
        await worker.stop()
        server.stop()
