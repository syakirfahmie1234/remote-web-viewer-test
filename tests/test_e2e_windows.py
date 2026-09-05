import asyncio
import os
import sys
import pytest
import websockets

from shared.messages import (
    create_command,
    create_controller_register,
    parse_message,
    serialize_message,
)
from shared.protocol import (
    MSG_FULL_SNAPSHOT,
    MSG_TAB_OPENED,
    MSG_TAB_CLOSED,
    MSG_COMMAND_RESULT,
    CMD_NAVIGATE,
    CMD_CLICK,
    CMD_PAGE_SOURCE,
    CMD_CLOSE_TAB,
)

from tests.test_e2e_integration import UvicornTestServer
from server.main import app
from server.config import CONTROLLER_TOKEN, WORKER_TOKEN

async def run_cmd(ws, command, payload, timeout=20.0):
    await ws.send(serialize_message(create_command("scenario-worker-windows", command, payload)))
    while True:
        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        msg = parse_message(raw)
        if msg.type == MSG_COMMAND_RESULT and msg.command == command:
            if not msg.success:
                raise Exception(f"Command {command} failed: {msg.error}")
            return msg.payload

@pytest.mark.asyncio
async def test_windows():
    server = UvicornTestServer(app)
    server.start()
    server_ws_url = f"ws://127.0.0.1:{server.port}/ws"

    env = os.environ.copy()
    env["SERVER_WS_URL"] = f"{server_ws_url}/worker"
    env["WORKER_ID"] = "scenario-worker-windows"
    env["WORKER_TOKEN"] = WORKER_TOKEN
    env["EXPLICIT_WAIT_TIMEOUT"] = "10.0"

    import subprocess
    worker_proc = subprocess.Popen(
        [sys.executable, "-m", "worker.worker"],
        env=env
    )
    
    try:
        async with websockets.connect(f"{server_ws_url}/controller?token={CONTROLLER_TOKEN}") as ws:
            await ws.send(serialize_message(create_controller_register(client_id="test-windows", subscribed_worker_ids=["scenario-worker-windows"])))

            worker_ready = False
            original_tab_handle = None
            for _ in range(30):
                msg = parse_message(await ws.recv())
                if msg.type == MSG_FULL_SNAPSHOT and msg.worker_id == "scenario-worker-windows":
                    worker_ready = True
                    original_tab_handle = msg.tab_handle
                    break
            assert worker_ready, "Worker failed to start"
            print(f"Original tab handle: {original_tab_handle}")

            # 1. Navigate
            print("Navigating to Windows page...")
            await run_cmd(ws, CMD_NAVIGATE, {"url": "https://the-internet.herokuapp.com/windows"})
            
            # 2. Click the link that opens a new window
            print("Clicking 'Click Here' link...")
            # We don't await run_cmd to finish because clicking might block if the new window opens and steals focus?
            # Actually CMD_CLICK will return success, then we wait for MSG_TAB_OPENED asynchronously
            await run_cmd(ws, CMD_CLICK, {"selector": "a[href='/windows/new']"})
            
            new_tab_handle = None
            print("Waiting for MSG_TAB_OPENED...")
            while True:
                msg = parse_message(await asyncio.wait_for(ws.recv(), timeout=10.0))
                if msg.type == MSG_TAB_OPENED:
                    new_tab_handle = msg.tab_handle
                    print(f"New tab opened: {new_tab_handle}")
                    break

            # Now wait for FULL_SNAPSHOT of the new tab
            print("Waiting for FULL_SNAPSHOT of new tab...")
            while True:
                msg = parse_message(await asyncio.wait_for(ws.recv(), timeout=10.0))
                if msg.type == MSG_FULL_SNAPSHOT and msg.tab_handle == new_tab_handle:
                    print("Received FULL_SNAPSHOT of new tab!")
                    break

            # 3. Verify new window content
            src = await run_cmd(ws, CMD_PAGE_SOURCE, {})
            assert "New Window" in src["page_source"], "New window content missing!"
            print("New window content verified.")

            # 4. Close the new tab
            print("Closing the new tab...")
            await run_cmd(ws, CMD_CLOSE_TAB, {"handle": new_tab_handle})
            
            # Wait for MSG_TAB_CLOSED
            print("Waiting for MSG_TAB_CLOSED...")
            while True:
                msg = parse_message(await asyncio.wait_for(ws.recv(), timeout=10.0))
                if msg.type == MSG_TAB_CLOSED and msg.tab_handle == new_tab_handle:
                    print("Received MSG_TAB_CLOSED!")
                    break

            # Wait for FULL_SNAPSHOT of original tab
            print("Waiting for FULL_SNAPSHOT of original tab...")
            while True:
                msg = parse_message(await asyncio.wait_for(ws.recv(), timeout=10.0))
                if msg.type == MSG_FULL_SNAPSHOT and msg.tab_handle == original_tab_handle:
                    print("Received FULL_SNAPSHOT of original tab!")
                    break

            # 5. Verify original window content
            src = await run_cmd(ws, CMD_PAGE_SOURCE, {})
            assert "Opening a new window" in src["page_source"], "Original window content missing!"
            print("Original window content verified. ALL TESTS PASSED!")

    finally:
        worker_proc.terminate()
        worker_proc.wait()
        server.stop()
