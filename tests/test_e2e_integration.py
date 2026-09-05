import asyncio
import os
import time
import threading
import json
import socket
import http.server
import subprocess
import pytest
import websockets
from contextlib import contextmanager

from shared.messages import (
    create_command,
    create_controller_register,
    create_resync_request,
    parse_message,
    serialize_message,
)
from shared.protocol import (
    MSG_FULL_SNAPSHOT,
    MSG_DOM_UPDATE,
    MSG_COMMAND_RESULT,
    CMD_NAVIGATE,
    CMD_CLICK,
)
from server.main import app
import uvicorn


class UvicornTestServer:
    """Runs FastAPI in a background thread on an ephemeral port."""
    def __init__(self, app):
        self.app = app
        self.server = None
        self.thread = None
        self.port = None

    def start(self):
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


class SimpleHTMLServer:
    """Hosts a simple HTML page in a background thread."""
    def __init__(self, html_content: str):
        content = html_content.encode("utf-8")
        
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-type", "text/html")
                self.end_headers()
                self.wfile.write(content)
                
            def log_message(self, format, *args):
                pass
                
        self.server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
        self.port = self.server.server_port
        self.url = f"http://127.0.0.1:{self.port}/"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2.0)


@pytest.fixture(scope="module")
def live_server():
    server = UvicornTestServer(app)
    server.start()
    yield server
    server.stop()


@pytest.fixture(scope="module")
def local_html_server():
    html = """
    <html>
      <head><title>Test Target</title></head>
      <body>
        <div id="content">Initial Content</div>
        <button id="btn" onclick="document.getElementById('content').innerText = 'Mutated Content';">Click Me</button>
      </body>
    </html>
    """
    server = SimpleHTMLServer(html)
    yield server
    server.stop()


@contextmanager
def run_worker_process(server_port, target_domain, worker_id):
    """Context manager to spawn and safely terminate a worker subprocess."""
    from server.config import WORKER_TOKEN
    env = os.environ.copy()
    env["SERVER_WS_URL"] = f"ws://127.0.0.1:{server_port}/ws/worker"
    env["WORKER_TOKEN"] = WORKER_TOKEN
    env["TARGET_DOMAIN"] = target_domain
    env["WORKER_ID"] = worker_id
    env["HEADLESS"] = "true"
    
    # Verify worker entry point exists
    worker_script = os.path.join(os.path.dirname(os.path.dirname(__file__)), "worker", "worker.py")
    if not os.path.exists(worker_script):
        raise FileNotFoundError(f"Worker entry point not found at: {worker_script}")

    import tempfile
    import sys
    stdout_file = tempfile.NamedTemporaryFile(delete=False, suffix=".out")
    stderr_file = tempfile.NamedTemporaryFile(delete=False, suffix=".err")
    stdout_path = stdout_file.name
    stderr_path = stderr_file.name
    
    proc = subprocess.Popen(
        [sys.executable, "-m", "worker.worker"],
        env=env,
        cwd=os.path.dirname(os.path.dirname(__file__)),
        stdout=stdout_file,
        stderr=stderr_file,
        text=False
    )
    
    stdout_file.close()
    stderr_file.close()
    
    # Wait for Chrome to start and connect via log polling
    for _ in range(30):
        with open(stderr_path, "r") as f:
            if "Connected and authenticated with server" in f.read():
                break
        time.sleep(0.5)
    
    try:
        yield proc
    finally:
        import platform
        if platform.system() == "Windows":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        
        try:
            with open(stdout_path, "r", encoding="utf-8", errors="replace") as f:
                stdout = f.read()
            with open(stderr_path, "r", encoding="utf-8", errors="replace") as f:
                stderr = f.read()
            
            if proc.returncode not in (0, 1, 15):
                print(f"Worker {worker_id} EXIT_CODE: {proc.returncode}")
            if stdout: print(f"Worker {worker_id} STDOUT:\n{stdout}")
            if stderr: print(f"Worker {worker_id} STDERR:\n{stderr}")
        except Exception as e:
            print(f"Failed to read worker logs: {e}")
        finally:
            try: os.remove(stdout_path)
            except OSError: pass
            try: os.remove(stderr_path)
            except OSError: pass


@pytest.fixture
def worker_a(live_server, local_html_server):
    with run_worker_process(live_server.port, local_html_server.url, "e2e-worker-a") as proc:
        yield proc

@pytest.fixture
def worker_b(live_server, local_html_server):
    with run_worker_process(live_server.port, local_html_server.url, "e2e-worker-b") as proc:
        yield proc


class ControllerClient:
    """Programmatic mock for the PySide6 Controller for E2E tests."""
    def __init__(self, ws):
        self.ws = ws
        self.tracked_version = {}
        self.is_stale = {}
        
    async def receive_until(self, expected_type, worker_id=None, timeout=10.0):
        deadline = time.time() + timeout
        while True:
            remain = deadline - time.time()
            if remain <= 0:
                raise TimeoutError(f"Did not receive {expected_type} for {worker_id} within {timeout}s")
            
            try:
                raw = await asyncio.wait_for(self.ws.recv(), timeout=remain)
            except asyncio.TimeoutError:
                raise TimeoutError(f"Did not receive {expected_type} for {worker_id} within {timeout}s")
                
            msg = parse_message(raw)
            if hasattr(msg, "worker_id") and (worker_id is None or msg.worker_id == worker_id):
                if msg.type == MSG_FULL_SNAPSHOT:
                    self.tracked_version[msg.worker_id] = msg.version
                    self.is_stale[msg.worker_id] = False
                elif msg.type == MSG_DOM_UPDATE:
                    self.tracked_version[msg.worker_id] = msg.version
                    
                if msg.type == expected_type:
                    return msg


@pytest.mark.asyncio
async def test_e2e_single_worker_navigation_and_dom_mutation(live_server, local_html_server, worker_a):
    """
    Test 1: Navigate, receive snapshot, click button, receive DOM mutation.
    """
    from server.config import CONTROLLER_TOKEN
    ctrl_url = f"ws://127.0.0.1:{live_server.port}/ws/controller?token={CONTROLLER_TOKEN}"
    
    async with websockets.connect(ctrl_url) as ws:
        client = ControllerClient(ws)
        
        print("T1: Subscribing...")
        reg_msg = create_controller_register(client_id="test-e2e", subscribed_worker_ids=["e2e-worker-a"])
        await ws.send(serialize_message(reg_msg))
        
        print("T1: Requesting initial resync...")
        resync_init = create_resync_request("e2e-worker-a", reason="init")
        await ws.send(serialize_message(resync_init))
        
        print("T1: Waiting for initial snapshot...")
        await client.receive_until(MSG_FULL_SNAPSHOT, "e2e-worker-a", timeout=35.0)
        
        print("T1: Navigating...")
        nav_msg = create_command("e2e-worker-a", CMD_NAVIGATE, {"url": local_html_server.url})
        await ws.send(serialize_message(nav_msg))
        
        print("T1: Waiting for nav result...")
        await client.receive_until(MSG_COMMAND_RESULT, "e2e-worker-a", timeout=10.0)
        
        # Removed sleep workaround
        
        print("T1: Requesting resync...")
        resync = create_resync_request("e2e-worker-a", reason="test_navigate")
        await ws.send(serialize_message(resync))
        
        print("T1: Waiting for snapshot...")
        snap = await client.receive_until(MSG_FULL_SNAPSHOT, "e2e-worker-a", timeout=10.0)
        assert snap.url == local_html_server.url
        assert "Initial Content" in snap.html
        
        print("T1: Clicking...")
        click_msg = create_command("e2e-worker-a", CMD_CLICK, {"selector": "#btn"})
        await ws.send(serialize_message(click_msg))
        
        print("T1: Waiting for click result...")
        await client.receive_until(MSG_COMMAND_RESULT, "e2e-worker-a", timeout=10.0)
        
        print("T1: Requesting resync 2...")
        resync2 = create_resync_request("e2e-worker-a", reason="test_click")
        await ws.send(serialize_message(resync2))
        
        print("T1: Waiting for snapshot 2...")
        snap2 = await client.receive_until(MSG_FULL_SNAPSHOT, "e2e-worker-a", timeout=10.0)
        assert snap2.version >= snap.version
        
        print("T1: Asserting...")
        assert "Mutated Content" in snap2.html
        print("T1: DONE")


@pytest.mark.asyncio
async def test_e2e_multi_worker_complete_isolation(live_server, local_html_server, worker_a, worker_b):
    """
    Test 2: Two workers operate independently with no state leakage.
    """
    from server.config import CONTROLLER_TOKEN
    ctrl_url = f"ws://127.0.0.1:{live_server.port}/ws/controller?token={CONTROLLER_TOKEN}"
    
    async with websockets.connect(ctrl_url) as ws:
        client = ControllerClient(ws)
        
        # Subscribe to both
        from shared.messages import serialize_message
        reg_msg = create_controller_register(client_id="test-e2e", subscribed_worker_ids=["e2e-worker-a", "e2e-worker-b"])
        await ws.send(serialize_message(reg_msg))
        
        # Request initial resync
        resync_a = create_resync_request("e2e-worker-a", reason="init")
        resync_b = create_resync_request("e2e-worker-b", reason="init")
        await ws.send(serialize_message(resync_a))
        await ws.send(serialize_message(resync_b))
        
        # Wait for initial snapshots
        snaps_init = {}
        for _ in range(2):
            msg = await client.receive_until(MSG_FULL_SNAPSHOT, None, timeout=35.0)
            snaps_init[msg.worker_id] = msg
        assert "e2e-worker-a" in snaps_init and "e2e-worker-b" in snaps_init
        
        # Navigate both
        nav_a = create_command("e2e-worker-a", CMD_NAVIGATE, {"url": local_html_server.url})
        nav_b = create_command("e2e-worker-b", CMD_NAVIGATE, {"url": local_html_server.url})
        await ws.send(serialize_message(nav_a))
        await ws.send(serialize_message(nav_b))
        
        # We need to collect 2 COMMAND_RESULT messages without dropping either.
        results = set()
        for _ in range(2):
            msg = await client.receive_until(MSG_COMMAND_RESULT, None, timeout=10.0)
            results.add(msg.worker_id)
        assert "e2e-worker-a" in results and "e2e-worker-b" in results
        
        # Removed sleep workaround
        
        resync_a = create_resync_request("e2e-worker-a", reason="test")
        resync_b = create_resync_request("e2e-worker-b", reason="test")
        await ws.send(serialize_message(resync_a))
        await ws.send(serialize_message(resync_b))
        
        # Collect 2 FULL_SNAPSHOT messages
        snaps = {}
        for _ in range(2):
            msg = await client.receive_until(MSG_FULL_SNAPSHOT, None, timeout=10.0)
            snaps[msg.worker_id] = msg
        snap_a = snaps["e2e-worker-a"]
        snap_b = snaps["e2e-worker-b"]
        
        assert snap_a.version > 0
        assert snap_b.version > 0
        
        # Click only A
        click_a = create_command("e2e-worker-a", CMD_CLICK, {"selector": "#btn"})
        await ws.send(serialize_message(click_a))
        await client.receive_until(MSG_COMMAND_RESULT, "e2e-worker-a", timeout=10.0)
        
        # Removed sleep workaround
        
        # Resync A
        resync2_a = create_resync_request("e2e-worker-a", reason="test_click")
        await ws.send(serialize_message(resync2_a))
        
        snap2_a = await client.receive_until(MSG_FULL_SNAPSHOT, "e2e-worker-a", timeout=10.0)
        assert "Mutated Content" in snap2_a.html
        
        # Verify B did not send an update (we check pending messages)
        # We wait 2 seconds to see if B sends anything. Wait_for raises TimeoutError on timeout.
        try:
            await asyncio.wait_for(client.receive_until(MSG_FULL_SNAPSHOT, "e2e-worker-b", timeout=2.0), timeout=2.0)
            assert False, "Worker B sent a snapshot when it shouldn't have"
        except (TimeoutError, asyncio.TimeoutError):
            pass  # Expected


@pytest.mark.asyncio
async def test_e2e_controller_reconnect_resync(live_server, local_html_server, worker_a, worker_b):
    """
    Test 3: Reconnect forces stale state and resync from all tracked workers.
    """
    from server.config import CONTROLLER_TOKEN
    ctrl_url = f"ws://127.0.0.1:{live_server.port}/ws/controller?token={CONTROLLER_TOKEN}"
    
    async with websockets.connect(ctrl_url) as ws1:
        client1 = ControllerClient(ws1)
        from shared.messages import serialize_message
        
        # Connect and subscribe
        reg_msg = create_controller_register(client_id="test-e2e", subscribed_worker_ids=["e2e-worker-a", "e2e-worker-b"])
        await ws1.send(serialize_message(reg_msg))
        
        # Request initial resync
        resync_a = create_resync_request("e2e-worker-a", reason="init")
        resync_b = create_resync_request("e2e-worker-b", reason="init")
        await ws1.send(serialize_message(resync_a))
        await ws1.send(serialize_message(resync_b))
        
        # Wait for initial snapshots
        snaps_init = {}
        while "e2e-worker-a" not in snaps_init or "e2e-worker-b" not in snaps_init:
            msg = await client1.receive_until(MSG_FULL_SNAPSHOT, None, timeout=35.0)
            snaps_init[msg.worker_id] = msg
        assert "e2e-worker-a" in snaps_init and "e2e-worker-b" in snaps_init
        
        nav_a = create_command("e2e-worker-a", CMD_NAVIGATE, {"url": local_html_server.url})
        nav_b = create_command("e2e-worker-b", CMD_NAVIGATE, {"url": local_html_server.url})
        await ws1.send(serialize_message(nav_a))
        await ws1.send(serialize_message(nav_b))
        
        results = set()
        for _ in range(2):
            msg = await client1.receive_until(MSG_COMMAND_RESULT, None, timeout=10.0)
            results.add(msg.worker_id)
        assert "e2e-worker-a" in results and "e2e-worker-b" in results
        
        # Removed sleep workaround
        
        resync_a = create_resync_request("e2e-worker-a", reason="test")
        resync_b = create_resync_request("e2e-worker-b", reason="test")
        await ws1.send(serialize_message(resync_a))
        await ws1.send(serialize_message(resync_b))
        
        snaps = {}
        for _ in range(2):
            msg = await client1.receive_until(MSG_FULL_SNAPSHOT, None, timeout=10.0)
            snaps[msg.worker_id] = msg
        
        version_a_before = snaps["e2e-worker-a"].version
        version_b_before = snaps["e2e-worker-b"].version

    # WS1 disconnects here (context manager exit)
    # Removed sleep workaround
    
    # Simulate Reconnect logic
    async with websockets.connect(ctrl_url) as ws2:
        client2 = ControllerClient(ws2)
        # Copy prior state to simulate client tracking
        client2.tracked_version["e2e-worker-a"] = version_a_before
        client2.tracked_version["e2e-worker-b"] = version_b_before
        
        # 1. On reconnect, mark all tracked worker states as stale
        client2.is_stale["e2e-worker-a"] = True
        client2.is_stale["e2e-worker-b"] = True
        
        # Re-register
        reg_msg2 = create_controller_register(client_id="test-e2e", subscribed_worker_ids=["e2e-worker-a", "e2e-worker-b"])
        await ws2.send(serialize_message(reg_msg2))
        
        # 2. Send RESYNC_REQUEST for each tracked worker_id
        resync_a = create_resync_request("e2e-worker-a", reason="reconnect")
        resync_b = create_resync_request("e2e-worker-b", reason="reconnect")
        await ws2.send(serialize_message(resync_a))
        await ws2.send(serialize_message(resync_b))
        
        snaps_new = {}
        for _ in range(2):
            msg = await client2.receive_until(MSG_FULL_SNAPSHOT, None, timeout=10.0)
            snaps_new[msg.worker_id] = msg
            
        snap_a_new = snaps_new["e2e-worker-a"]
        snap_b_new = snaps_new["e2e-worker-b"]
        
        # 4. Assert tracked_version for both workers updated to new received version
        assert client2.tracked_version["e2e-worker-a"] == snap_a_new.version
        assert client2.tracked_version["e2e-worker-b"] == snap_b_new.version
        
        # 5. Assert is_stale cleared for both workers after FULL_SNAPSHOT received
        assert client2.is_stale["e2e-worker-a"] is False, f"Stale map: {client2.is_stale}"
        assert client2.is_stale["e2e-worker-b"] is False, f"Stale map: {client2.is_stale}"
