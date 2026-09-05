import asyncio
import os
import time
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
    CMD_NEW_TAB,
    CMD_CLOSE_TAB,
)

from tests.test_e2e_integration import UvicornTestServer
from server.main import app
from server.config import CONTROLLER_TOKEN, WORKER_TOKEN

@pytest.mark.asyncio
async def test_e2e_open_close_tabs():
    server = UvicornTestServer(app)
    server.start()
    server_ws_url = f"ws://127.0.0.1:{server.port}/ws"
    
    env = os.environ.copy()
    env["SERVER_WS_URL"] = f"{server_ws_url}/worker"
    env["WORKER_ID"] = "tab-worker"
    env["WORKER_TOKEN"] = WORKER_TOKEN
    
    import subprocess
    worker_proc = subprocess.Popen(
        [sys.executable, "-m", "worker.worker"],
        env=env
    )
    
    try:
        async with websockets.connect(f"{server_ws_url}/controller?token={CONTROLLER_TOKEN}") as ws:
            await ws.send(serialize_message(create_controller_register(client_id="test-e2e-tabs", subscribed_worker_ids=["tab-worker"])))
            
            worker_connected = False
            for _ in range(30):
                msg = parse_message(await ws.recv())
                if msg.type == MSG_FULL_SNAPSHOT and msg.worker_id == "tab-worker":
                    worker_connected = True
                    break
            assert worker_connected, "Worker failed to connect"
            
            # 1. Start with Tab A
            await ws.send(serialize_message(create_command("tab-worker", CMD_NAVIGATE, {"url": "https://example.com/"})))
            tab_a_handle = None
            for _ in range(20):
                msg = parse_message(await ws.recv())
                if msg.type == MSG_FULL_SNAPSHOT and "example.com" in msg.url:
                    tab_a_handle = msg.tab_handle
                    break
            assert tab_a_handle is not None, "Failed to navigate Tab A"
            
            # 2. Open Tab B
            await ws.send(serialize_message(create_command("tab-worker", CMD_NEW_TAB, {"url": "https://example.org/"})))
            tab_b_handle = None
            for _ in range(20):
                msg = parse_message(await ws.recv())
                if msg.type == MSG_TAB_OPENED:
                    tab_b_handle = msg.tab_handle
                    break
            assert tab_b_handle is not None
            
            # 3. Open Tab C
            await ws.send(serialize_message(create_command("tab-worker", CMD_NEW_TAB, {"url": "https://example.net/"})))
            tab_c_handle = None
            for _ in range(20):
                msg = parse_message(await ws.recv())
                if msg.type == MSG_TAB_OPENED:
                    tab_c_handle = msg.tab_handle
                    break
            assert tab_c_handle is not None
            
            # 4. Close Tab B explicitly
            print(f"Closing Tab B: {tab_b_handle}")
            await ws.send(serialize_message(create_command("tab-worker", CMD_CLOSE_TAB, {"handle": tab_b_handle})))
            b_closed = False
            for _ in range(20):
                msg = parse_message(await ws.recv())
                if msg.type == MSG_TAB_CLOSED and msg.tab_handle == tab_b_handle:
                    b_closed = True
                    break
            assert b_closed, "Tab B did not close properly"
            
            # 5. Close active tab (should be C) by passing empty handle
            print("Closing Active Tab (C)")
            await ws.send(serialize_message(create_command("tab-worker", CMD_CLOSE_TAB, {})))
            c_closed = False
            for _ in range(20):
                msg = parse_message(await ws.recv())
                if msg.type == MSG_TAB_CLOSED and msg.tab_handle == tab_c_handle:
                    c_closed = True
                    break
            assert c_closed, "Active Tab (C) did not close properly"
            
            # 6. Close the LAST remaining tab (Tab A) to test the safety rescue logic
            print(f"Closing Last Tab A: {tab_a_handle}")
            await ws.send(serialize_message(create_command("tab-worker", CMD_CLOSE_TAB, {"handle": tab_a_handle})))
            
            a_closed = False
            rescued_tab_handle = None
            
            for _ in range(30):
                msg = parse_message(await ws.recv())
                if msg.type == MSG_TAB_CLOSED and msg.tab_handle == tab_a_handle:
                    a_closed = True
                elif msg.type == MSG_TAB_OPENED and msg.tab_handle != tab_a_handle and msg.tab_handle != tab_b_handle and msg.tab_handle != tab_c_handle:
                    rescued_tab_handle = msg.tab_handle
                elif msg.type == MSG_FULL_SNAPSHOT and msg.tab_handle != tab_a_handle:
                    if "new-tab-page" in msg.url:
                        rescued_tab_handle = msg.tab_handle
                
                if a_closed and rescued_tab_handle:
                    break
                    
            assert a_closed, "The last tab (A) was not closed"
            assert rescued_tab_handle is not None, "Safety rescue tab was not created!"
            print(f"Successfully rescued session with new tab: {rescued_tab_handle}")
            
    finally:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(worker_proc.pid)], capture_output=True)
        server.stop()
