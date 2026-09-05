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
    CMD_NEW_TAB,
    CMD_CLOSE_TAB,
)

from tests.test_e2e_integration import UvicornTestServer
from server.main import app
from server.config import CONTROLLER_TOKEN, WORKER_TOKEN

@pytest.mark.asyncio
async def test_e2e_rapid_open_close():
    server = UvicornTestServer(app)
    server.start()
    server_ws_url = f"ws://127.0.0.1:{server.port}/ws"
    
    env = os.environ.copy()
    env["SERVER_WS_URL"] = f"{server_ws_url}/worker"
    env["WORKER_ID"] = "rapid-oc-worker"
    env["WORKER_TOKEN"] = WORKER_TOKEN
    
    import subprocess
    worker_proc = subprocess.Popen(
        [sys.executable, "-m", "worker.worker"],
        env=env
    )
    
    try:
        async with websockets.connect(f"{server_ws_url}/controller?token={CONTROLLER_TOKEN}") as ws:
            await ws.send(serialize_message(create_controller_register(client_id="test-e2e-rapid-oc", subscribed_worker_ids=["rapid-oc-worker"])))
            
            worker_connected = False
            for _ in range(30):
                msg = parse_message(await ws.recv())
                if msg.type == MSG_FULL_SNAPSHOT and msg.worker_id == "rapid-oc-worker":
                    worker_connected = True
                    break
            assert worker_connected, "Worker failed to connect"
            
            iterations = 10
            
            print(f"Blasting {iterations} NEW_TAB and CLOSE_TAB commands rapidly...")
            for i in range(iterations):
                await ws.send(serialize_message(create_command("rapid-oc-worker", CMD_NEW_TAB, {"url": "https://example.com/"})))
                await asyncio.sleep(0.2)
                await ws.send(serialize_message(create_command("rapid-oc-worker", CMD_CLOSE_TAB, {})))
                await asyncio.sleep(0.2)
                
            opened_count = 0
            closed_count = 0
            
            expected_results = iterations * 2
            results_received = 0
            
            while results_received < expected_results:
                raw_msg = await asyncio.wait_for(ws.recv(), timeout=20.0)
                msg = parse_message(raw_msg)
                
                if msg.type == MSG_TAB_OPENED:
                    opened_count += 1
                elif msg.type == MSG_TAB_CLOSED:
                    closed_count += 1
                elif msg.type == MSG_COMMAND_RESULT:
                    assert msg.success, f'Command {msg.command} failed: {msg.error}'
                    results_received += 1
                    
            print(f"Received all {expected_results} command results.")
            print(f"Tabs opened: {opened_count}, Tabs closed: {closed_count}")
            
            # Check worker is still alive
            await ws.send(serialize_message(create_command("rapid-oc-worker", CMD_NEW_TAB, {"url": "https://example.com/"})))
            
            alive = False
            for _ in range(20):
                msg = parse_message(await ws.recv())
                if msg.type == MSG_COMMAND_RESULT and msg.command == CMD_NEW_TAB:
                    alive = True
                    break
            assert alive, "Worker did not respond to final command, might have crashed."
            print("Worker survived the rapid open/close bombardment successfully!")
            
    finally:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(worker_proc.pid)], capture_output=True)
        server.stop()
