import asyncio
import time
import pytest
from fastapi.testclient import TestClient
from fastapi.websockets import WebSocket

from server.session import SessionManager, ControllerSession
from server.authentication import get_authorized_workers_for_token
from server.config import CONTROLLER_TOKEN

# 1. Session creation and lookup
def test_session_creation():
    mgr = SessionManager()
    class MockWS: pass
    ws = MockWS()
    
    session = mgr.create_session(ws, client_id="test_client", authorized_workers=frozenset(["w1"]))
    assert session.session_id.startswith("sess-")
    assert session.client_id == "test_client"
    assert session.authorized_worker_ids == frozenset(["w1"])
    
    fetched = mgr.get_session(ws)
    assert fetched is session

# 2. Session touch updates activity
def test_session_touch():
    mgr = SessionManager()
    class MockWS: pass
    ws = MockWS()
    
    session = mgr.create_session(ws)
    initial_activity = session.last_activity_at
    
    time.sleep(0.01)
    mgr.touch_session(ws)
    
    assert session.last_activity_at > initial_activity

# 3. Session expiry detection
def test_session_expiry():
    mgr = SessionManager()
    class MockWS: pass
    ws = MockWS()
    
    session = mgr.create_session(ws)
    session.last_activity_at = time.monotonic() - 100  # Fake being old
    
    expired = mgr.get_expired_sessions(timeout_seconds=50)
    assert len(expired) == 1
    assert expired[0] is session
    
    not_expired = mgr.get_expired_sessions(timeout_seconds=200)
    assert len(not_expired) == 0

# 4. Access control allow
def test_access_control_allow():
    mgr = SessionManager()
    class MockWS: pass
    ws = MockWS()
    
    mgr.create_session(ws, authorized_workers=frozenset(["w1", "w2"]))
    assert mgr.is_authorized(ws, "w1") == True
    assert mgr.is_authorized(ws, "w2") == True

# 5. Access control deny
def test_access_control_deny():
    mgr = SessionManager()
    class MockWS: pass
    ws = MockWS()
    
    mgr.create_session(ws, authorized_workers=frozenset(["w1"]))
    assert mgr.is_authorized(ws, "w2") == False

# 6. Access control None = allow all
def test_access_control_allow_all():
    mgr = SessionManager()
    class MockWS: pass
    ws = MockWS()
    
    mgr.create_session(ws, authorized_workers=None)
    assert mgr.is_authorized(ws, "w1") == True
    assert mgr.is_authorized(ws, "w99") == True

# 7. Test get_authorized_workers_for_token parsing
def test_authorized_workers_parsing(monkeypatch):
    import server.config
    monkeypatch.setattr(server.config, "CONTROLLER_ALLOWED_WORKERS", f"token1:w1,w2;{CONTROLLER_TOKEN}:w3,w4")
    
    # Matching token
    workers = get_authorized_workers_for_token(CONTROLLER_TOKEN)
    assert workers == frozenset(["w3", "w4"])
    
    # Non-matching token
    assert get_authorized_workers_for_token("wrong_token") is None

# 8. Test subscription filtering logic in controller_manager
def test_subscription_filtering():
    from server.controller_manager import ControllerManager
    
    session_mgr = SessionManager()
    ctrl_mgr = ControllerManager(session_mgr=session_mgr)
    
    class MockWS: pass
    ws = MockWS()
    
    ctrl_mgr.register_controller(ws, authorized_workers=frozenset(["w1", "w2"]))
    
    # Try to subscribe to w1, w2, w3
    ctrl_mgr.set_subscriptions(ws, {"w1", "w2", "w3"})
    
    # Should only be subscribed to w1, w2
    subs = ctrl_mgr.get_subscriptions(ws)
    assert subs == {"w1", "w2"}

# 9. Test command routing rejects unauthorized
@pytest.mark.asyncio
async def test_command_routing_unauthorized():
    from server.message_router import MessageRouter
    from server.worker_manager import WorkerManager
    from server.controller_manager import ControllerManager
    from shared.messages import create_command, serialize_message, parse_message
    
    worker_mgr = WorkerManager()
    session_mgr = SessionManager()
    ctrl_mgr = ControllerManager(session_mgr=session_mgr)
    router = MessageRouter(worker_mgr, ctrl_mgr, session_mgr)
    
    class MockWS:
        def __init__(self):
            self.sent = []
        async def send_text(self, text):
            self.sent.append(text)
            
    ws = MockWS()
    ctrl_mgr.register_controller(ws, authorized_workers=frozenset(["w1"]))
    
    # Send command to w2 (unauthorized)
    cmd = create_command(worker_id="w2", command="click", payload={"target_id": "btn"})
    await router.handle_controller_message(ws, serialize_message(cmd))
    
    assert len(ws.sent) == 1
    err = parse_message(ws.sent[0])
    assert err.type == "error"
    assert err.code == "UNAUTHORIZED"
