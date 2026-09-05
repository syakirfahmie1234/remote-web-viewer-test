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
    MSG_COMMAND_RESULT,
    CMD_NAVIGATE,
    CMD_CLICK,
    CMD_TYPE,
    CMD_PAGE_SOURCE,
    CMD_SCROLL
)

from tests.test_e2e_integration import UvicornTestServer
from server.main import app
from server.config import CONTROLLER_TOKEN, WORKER_TOKEN

async def run_cmd(ws, command, payload, timeout=20.0):
    await ws.send(serialize_message(create_command("scenario-worker", command, payload)))
    while True:
        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        msg = parse_message(raw)
        if msg.type == MSG_COMMAND_RESULT and msg.command == command:
            if not msg.success:
                raise Exception(f"Command {command} failed: {msg.error}")
            return msg.payload

@pytest.mark.asyncio
async def test_scenarios():
    server = UvicornTestServer(app)
    server.start()
    server_ws_url = f"ws://127.0.0.1:{server.port}/ws"

    env = os.environ.copy()
    env["SERVER_WS_URL"] = f"{server_ws_url}/worker"
    env["WORKER_ID"] = "scenario-worker"
    env["WORKER_TOKEN"] = WORKER_TOKEN
    env["EXPLICIT_WAIT_TIMEOUT"] = "10.0"

    import subprocess
    worker_proc = subprocess.Popen(
        [sys.executable, "-m", "worker.worker"],
        env=env
    )
    
    try:
        async with websockets.connect(f"{server_ws_url}/controller?token={CONTROLLER_TOKEN}") as ws:
            await ws.send(serialize_message(create_controller_register(client_id="test-scenarios", subscribed_worker_ids=["scenario-worker"])))

            worker_ready = False
            for _ in range(30):
                msg = parse_message(await ws.recv())
                if msg.type == MSG_FULL_SNAPSHOT and msg.worker_id == "scenario-worker":
                    worker_ready = True
                    break
            assert worker_ready, "Worker failed to start"

            # 1. Add/Remove Elements
            print("1. Add/Remove Elements")
            await run_cmd(ws, CMD_NAVIGATE, {"url": "https://the-internet.herokuapp.com/add_remove_elements/"})
            await run_cmd(ws, CMD_CLICK, {"selector": "button[onclick='addElement()']"})
            await run_cmd(ws, CMD_CLICK, {"selector": "button.added-manually"})
            src = await run_cmd(ws, CMD_PAGE_SOURCE, {})
            assert "added-manually" not in src["page_source"]

            # 2. Challenging DOM
            print("2. Challenging DOM")
            await run_cmd(ws, CMD_NAVIGATE, {"url": "https://the-internet.herokuapp.com/challenging_dom"})
            await run_cmd(ws, CMD_CLICK, {"selector": ".large-2.columns .button:nth-child(1)"})
            
            # 3. Entry Ad
            print("3. Entry Ad")
            await run_cmd(ws, CMD_NAVIGATE, {"url": "https://the-internet.herokuapp.com/entry_ad"})
            await run_cmd(ws, CMD_CLICK, {"selector": ".modal-footer p"})
            
            # 4. Infinite Scroll
            print("4. Infinite Scroll")
            await run_cmd(ws, CMD_NAVIGATE, {"url": "https://the-internet.herokuapp.com/infinite_scroll"})
            src1 = await run_cmd(ws, CMD_PAGE_SOURCE, {})
            await run_cmd(ws, CMD_SCROLL, {"y": 5000}) 
            await asyncio.sleep(1)
            src2 = await run_cmd(ws, CMD_PAGE_SOURCE, {})
            assert len(src2["page_source"]) > len(src1["page_source"])
            
            # 5. Disappearing Elements
            print("5. Disappearing Elements")
            await run_cmd(ws, CMD_NAVIGATE, {"url": "https://the-internet.herokuapp.com/disappearing_elements"})
            await run_cmd(ws, CMD_CLICK, {"selector": "a[href='/about/']"})
            
            # 7. Dynamic Controls
            print("7. Dynamic Controls")
            await run_cmd(ws, CMD_NAVIGATE, {"url": "https://the-internet.herokuapp.com/dynamic_controls"})
            await run_cmd(ws, CMD_CLICK, {"selector": "#checkbox-example button"})
            await asyncio.sleep(4) 
            src = await run_cmd(ws, CMD_PAGE_SOURCE, {})
            assert "It's gone!" in src["page_source"]

            # 8. Dynamic Loading
            print("8. Dynamic Loading")
            await run_cmd(ws, CMD_NAVIGATE, {"url": "https://the-internet.herokuapp.com/dynamic_loading/2"})
            await run_cmd(ws, CMD_CLICK, {"selector": "#start button"})
            await asyncio.sleep(6)
            src = await run_cmd(ws, CMD_PAGE_SOURCE, {})
            assert "Hello World!" in src["page_source"]

            # 9. SauceDemo Login
            print("9. SauceDemo Login")
            await run_cmd(ws, CMD_NAVIGATE, {"url": "https://www.saucedemo.com/"})
            await run_cmd(ws, CMD_TYPE, {"selector": "#user-name", "text": "standard_user"})
            await run_cmd(ws, CMD_TYPE, {"selector": "#password", "text": "secret_sauce"})
            await run_cmd(ws, CMD_CLICK, {"selector": "#login-button"})
            await asyncio.sleep(1)
            src = await run_cmd(ws, CMD_PAGE_SOURCE, {})
            assert "inventory.html" in src["page_source"] or "Products" in src["page_source"]
            
            print("ALL TESTS PASSED!")

    finally:
        worker_proc.terminate()
        worker_proc.wait()
        server.stop()
