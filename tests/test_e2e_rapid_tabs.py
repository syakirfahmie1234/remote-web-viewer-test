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
    MSG_COMMAND_RESULT,
    CMD_NAVIGATE,
    CMD_NEW_TAB,
    CMD_SWITCH_TAB,
)

from tests.test_e2e_integration import UvicornTestServer
from server.main import app
from server.config import CONTROLLER_TOKEN, WORKER_TOKEN

@pytest.mark.asyncio
async def test_e2e_rapid_tab_switching():
    server = UvicornTestServer(app)
    server.start()
    server_ws_url = f"ws://127.0.0.1:{server.port}/ws"
    
    env = os.environ.copy()
    env["SERVER_WS_URL"] = f"{server_ws_url}/worker"
    
    import subprocess
    env["WORKER_ID"] = "rapid-worker"
    env["WORKER_TOKEN"] = WORKER_TOKEN
    worker_proc = subprocess.Popen(
        [sys.executable, "-m", "worker.worker"],
        env=env
    )
    
    try:
        async with websockets.connect(f"{server_ws_url}/controller?token={CONTROLLER_TOKEN}") as ws:
            await ws.send(serialize_message(create_controller_register(client_id="test-e2e-rapid", subscribed_worker_ids=["rapid-worker"])))
            
            worker_connected = False
            for _ in range(30):
                msg = parse_message(await ws.recv())
                if msg.type == MSG_FULL_SNAPSHOT and msg.worker_id == "rapid-worker":
                    worker_connected = True
                    break
            assert worker_connected, "Worker failed to connect"
            
            await ws.send(serialize_message(create_command("rapid-worker", CMD_NAVIGATE, {"url": "https://example.com/"})))
            
            tab_a_handle = None
            for _ in range(20):
                msg = parse_message(await ws.recv())
                if msg.type == MSG_FULL_SNAPSHOT and "example.com" in msg.url:
                    tab_a_handle = msg.tab_handle
                    break
            assert tab_a_handle is not None, "Failed to navigate Tab A"
            
            await ws.send(serialize_message(create_command("rapid-worker", CMD_NEW_TAB, {"url": "https://the-internet.herokuapp.com/"})))
            
            tab_b_handle = None
            for _ in range(20):
                msg = parse_message(await ws.recv())
                if msg.type == MSG_TAB_OPENED:
                    tab_b_handle = msg.tab_handle
                if msg.type == MSG_FULL_SNAPSHOT and "the-internet.herokuapp.com" in msg.url:
                    tab_b_handle = msg.tab_handle
                    break
            assert tab_b_handle is not None, "Failed to open Tab B"
            assert tab_a_handle != tab_b_handle, "Handles should be different"
            
            print(f"Rapid switching between {tab_a_handle} and {tab_b_handle}")
            # Sequence: 0:B, 1:A, 2:B, 3:A, 4:B, 5:A, 6:B, 7:A, 8:B, 9:A
            for i in range(10):
                target_handle = tab_b_handle if i % 2 == 0 else tab_a_handle
                await ws.send(serialize_message(create_command("rapid-worker", CMD_SWITCH_TAB, {"handle": target_handle})))
                
            switch_results_received = 0
            final_active_handle = None
            
            while switch_results_received < 10:
                raw_msg = await asyncio.wait_for(ws.recv(), timeout=20.0)
                msg = parse_message(raw_msg)
                
                if msg.type == MSG_COMMAND_RESULT and msg.command == CMD_SWITCH_TAB:
                    switch_results_received += 1
                elif msg.type == MSG_FULL_SNAPSHOT:
                    final_active_handle = msg.tab_handle
                    
            assert final_active_handle == tab_a_handle, f"Expected final tab to be {tab_a_handle}, got {final_active_handle}"
            print("Successfully processed rapid tab switches!")
            
    finally:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(worker_proc.pid)], capture_output=True)
        server.stop()
