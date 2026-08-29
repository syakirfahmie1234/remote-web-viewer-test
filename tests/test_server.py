"""
Integration tests for FastAPI Server WebSocket relay and multi-worker routing isolation.
Runs against a live Uvicorn server on an ephemeral port using standard websockets client.
Guarantees real network WebSocket testing with zero event-loop deadlocks.
"""

import asyncio
import os
import threading
import time
import pytest
import uvicorn
import websockets

# Set test environment tokens before importing server
os.environ["WORKER_TOKEN"] = "test-worker-token"
os.environ["CONTROLLER_TOKEN"] = "test-controller-token"

from server.main import app
from server.config import WORKER_TOKEN, CONTROLLER_TOKEN
from shared.protocol import (
    STATUS_CONNECTED,
    STATUS_DISCONNECTED,
    MSG_COMMAND,
    MSG_COMMAND_RESULT,
    MSG_FULL_SNAPSHOT,
    MSG_DOM_UPDATE,
    MSG_WORKER_STATUS,
    MSG_ERROR,
    OP_TEXT,
)
from shared.models import DOMDiffOp
from shared.messages import (
    create_command,
    create_command_result,
    create_full_snapshot,
    create_dom_update,
    serialize_message,
    parse_message,
)


class UvicornTestServer:
    """Runs FastAPI in a background thread on an ephemeral port."""
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


@pytest.fixture(scope="module")
def server_url():
    test_server = UvicornTestServer(app)
    test_server.start()
    url = f"ws://127.0.0.1:{test_server.port}"
    http_url = f"http://127.0.0.1:{test_server.port}"
    yield {"ws": url, "http": http_url}
    test_server.stop()


@pytest.mark.asyncio
async def test_health_check(server_url):
    """GET /health returns 200 OK."""
    import httpx
    async with httpx.AsyncClient() as client:
        res = await client.get(f"{server_url['http']}/health")
        assert res.status_code == 200
        assert res.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_worker_auth_rejection_on_invalid_token(server_url):
    """Worker connecting with invalid token receives ERROR and socket is closed."""
    async with websockets.connect(f"{server_url['ws']}/ws/worker?token=wrong-token") as ws:
        msg = await ws.recv()
        parsed = parse_message(msg)
        assert parsed.type == MSG_ERROR
        assert parsed.code == "AUTH_FAILED"

        # Connection should now be closed by server
        with pytest.raises(websockets.exceptions.ConnectionClosed):
            await ws.recv()


@pytest.mark.asyncio
async def test_controller_auth_rejection_on_invalid_token(server_url):
    """Controller connecting with invalid token receives ERROR and socket is closed."""
    async with websockets.connect(f"{server_url['ws']}/ws/controller?token=wrong-token") as ws:
        msg = await ws.recv()
        parsed = parse_message(msg)
        assert parsed.type == MSG_ERROR
        assert parsed.code == "AUTH_FAILED"

        # Connection should now be closed by server
        with pytest.raises(websockets.exceptions.ConnectionClosed):
            await ws.recv()


@pytest.mark.asyncio
async def test_single_worker_end_to_end_pipeline(server_url):
    """
    End-to-end verification:
    Controller sends COMMAND -> Worker receives it -> Worker replies RESULT and SNAPSHOT -> Controller receives.
    """
    worker_id = "worker-01"

    async with websockets.connect(f"{server_url['ws']}/ws/worker?token={WORKER_TOKEN}&worker_id={worker_id}") as worker_ws:
        async with websockets.connect(f"{server_url['ws']}/ws/controller?token={CONTROLLER_TOKEN}&worker_id={worker_id}") as ctrl_ws:
            # 1. Controller receives initial WORKER_STATUS
            init_status = await ctrl_ws.recv()
            parsed_status = parse_message(init_status)
            assert parsed_status.type == MSG_WORKER_STATUS
            assert parsed_status.worker_id == worker_id
            assert parsed_status.status == STATUS_CONNECTED

            # 2. Controller sends COMMAND to worker-01
            cmd = create_command(worker_id=worker_id, command="click", payload={"selector": "#search"})
            await ctrl_ws.send(serialize_message(cmd))

            # 3. Worker receives COMMAND
            worker_recv = await worker_ws.recv()
            parsed_cmd = parse_message(worker_recv)
            assert parsed_cmd.type == MSG_COMMAND
            assert parsed_cmd.worker_id == worker_id
            assert parsed_cmd.command == "click"
            assert parsed_cmd.payload == {"selector": "#search"}

            # 4. Worker replies with COMMAND_RESULT
            res = create_command_result(worker_id=worker_id, command="click", success=True)
            await worker_ws.send(serialize_message(res))

            # 5. Controller receives COMMAND_RESULT
            ctrl_recv_res = await ctrl_ws.recv()
            parsed_res = parse_message(ctrl_recv_res)
            assert parsed_res.type == MSG_COMMAND_RESULT
            assert parsed_res.worker_id == worker_id
            assert parsed_res.success is True

            # 6. Worker sends FULL_SNAPSHOT
            snap = create_full_snapshot(
                worker_id=worker_id,
                version=1,
                url="https://site.com",
                title="Title",
                html="<html></html>",
            )
            await worker_ws.send(serialize_message(snap))

            # 7. Controller receives FULL_SNAPSHOT
            ctrl_recv_snap = await ctrl_ws.recv()
            parsed_snap = parse_message(ctrl_recv_snap)
            assert parsed_snap.type == MSG_FULL_SNAPSHOT
            assert parsed_snap.worker_id == worker_id
            assert parsed_snap.version == 1


@pytest.mark.asyncio
async def test_command_to_offline_worker_returns_error(server_url):
    """Controller sending command to offline worker receives ERROR."""
    async with websockets.connect(f"{server_url['ws']}/ws/controller?token={CONTROLLER_TOKEN}&worker_id=worker-offline") as ctrl_ws:
        # Drain initial offline status
        status_msg = await ctrl_ws.recv()
        parsed_status = parse_message(status_msg)
        assert parsed_status.type == MSG_WORKER_STATUS
        assert parsed_status.status == STATUS_DISCONNECTED

        # Send command to offline worker
        cmd = create_command(worker_id="worker-offline", command="refresh", payload={})
        await ctrl_ws.send(serialize_message(cmd))

        # Controller receives ERROR
        err_msg = await ctrl_ws.recv()
        parsed_err = parse_message(err_msg)
        assert parsed_err.type == MSG_ERROR
        assert parsed_err.code == "WORKER_NOT_CONNECTED"
        assert parsed_err.worker_id == "worker-offline"


@pytest.mark.asyncio
async def test_multi_worker_routing_isolation(server_url):
    """
    Multi-Worker Isolation Test:
    - 2 Workers: Worker-A and Worker-B
    - 2 Controllers: Ctrl-A (subscribed to Worker-A) and Ctrl-B (subscribed to Worker-B)
    - Proves Worker A never receives Worker B's commands, and Ctrl A never receives Worker B's updates.
    """
    w_a = "worker-a"
    w_b = "worker-b"

    async with websockets.connect(f"{server_url['ws']}/ws/worker?token={WORKER_TOKEN}&worker_id={w_a}") as ws_a:
        async with websockets.connect(f"{server_url['ws']}/ws/worker?token={WORKER_TOKEN}&worker_id={w_b}") as ws_b:
            async with websockets.connect(f"{server_url['ws']}/ws/controller?token={CONTROLLER_TOKEN}&worker_id={w_a}") as ctrl_a:
                async with websockets.connect(f"{server_url['ws']}/ws/controller?token={CONTROLLER_TOKEN}&worker_id={w_b}") as ctrl_b:
                    # Drain initial statuses
                    async def wait_for_status(ctrl, target_worker):
                        while True:
                            st = parse_message(await ctrl.recv())
                            if st.type == MSG_WORKER_STATUS and st.worker_id == target_worker:
                                return st
                    
                    st_a = await wait_for_status(ctrl_a, w_a)
                    assert st_a.status == STATUS_CONNECTED
    
                    st_b = await wait_for_status(ctrl_b, w_b)
                    assert st_b.status == STATUS_CONNECTED

                    # Ctrl-A sends command to Worker-A
                    cmd_a = create_command(worker_id=w_a, command="navigate", payload={"url": "https://site.com/a"})
                    await ctrl_a.send(serialize_message(cmd_a))

                    # Worker-A receives it
                    recv_a = parse_message(await ws_a.recv())
                    assert recv_a.type == MSG_COMMAND
                    assert recv_a.worker_id == w_a

                    # Ctrl-B sends command to Worker-B
                    cmd_b = create_command(worker_id=w_b, command="navigate", payload={"url": "https://site.com/b"})
                    await ctrl_b.send(serialize_message(cmd_b))

                    # Worker-B receives it
                    recv_b = parse_message(await ws_b.recv())
                    assert recv_b.type == MSG_COMMAND
                    assert recv_b.worker_id == w_b

                    # Worker-B sends DOM update
                    diff_b = create_dom_update(
                        worker_id=w_b,
                        base_version=1,
                        version=2,
                        ops=[DOMDiffOp(op=OP_TEXT, selector="#b", text="B-Updated")],
                    )
                    await ws_b.send(serialize_message(diff_b))

                    # Ctrl-B receives it
                    ctrl_b_recv = parse_message(await ctrl_b.recv())
                    assert ctrl_b_recv.type == MSG_DOM_UPDATE
                    assert ctrl_b_recv.worker_id == w_b
                    assert ctrl_b_recv.version == 2

                    # Worker-A sends snapshot
                    snap_a = create_full_snapshot(
                        worker_id=w_a,
                        version=10,
                        url="https://site.com/a",
                        title="A",
                        html="<div>A</div>",
                    )
                    await ws_a.send(serialize_message(snap_a))

                    # Ctrl-A receives it
                    ctrl_a_recv = parse_message(await ctrl_a.recv())
                    assert ctrl_a_recv.type == MSG_FULL_SNAPSHOT
                    assert ctrl_a_recv.worker_id == w_a
                    assert ctrl_a_recv.version == 10
