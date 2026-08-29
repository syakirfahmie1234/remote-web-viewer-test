import pytest
from unittest.mock import patch, MagicMock

from shared.models import BrowserConfigMessage
from shared.messages import (
    create_browser_config,
    serialize_message,
    parse_message,
    MissingWorkerIdError,
)
from worker.browser import BrowserManager, BrowserConfig

# 1-4: BrowserConfig and Chrome Args
@patch("worker.browser.webdriver.Chrome")
def test_browser_config_args(mock_chrome):
    # Test 1: Full proxy args
    config = BrowserConfig(headless=True, proxy_url="http://proxy.com:80", proxy_username="usr", proxy_password="pwd")
    bm = BrowserManager()
    bm._current_config = config
    bm.start()
    
    # Get the Options object passed to Chrome
    options = mock_chrome.call_args[1]["options"]
    args = options.arguments
    
    assert "--headless=new" in args
    assert "--proxy-server=http://proxy.com:80" in args
    assert any(arg.startswith("--load-extension=") for arg in args)  # Proxy auth extension created
    
    # Test 2: No proxy
    mock_chrome.reset_mock()
    config2 = BrowserConfig(headless=False, proxy_url=None)
    bm2 = BrowserManager()
    bm2._current_config = config2
    bm2.start()
    
    options2 = mock_chrome.call_args[1]["options"]
    args2 = options2.arguments
    
    assert "--headless=new" not in args2
    assert not any(arg.startswith("--proxy-server=") for arg in args2)
    assert not any(arg.startswith("--load-extension=") for arg in args2)


# 5-6: Message round trip and validation
def test_browser_config_message_round_trip():
    msg = create_browser_config(worker_id="w1", headless=False, proxy_url="socks5://test:1080")
    assert msg.type == "browser_config"
    assert msg.headless is False
    assert msg.proxy_url == "socks5://test:1080"
    
    serialized = serialize_message(msg)
    parsed = parse_message(serialized)
    
    assert isinstance(parsed, BrowserConfigMessage)
    assert parsed.worker_id == "w1"
    assert parsed.headless is False
    assert parsed.proxy_url == "socks5://test:1080"

def test_browser_config_message_worker_id_required():
    with pytest.raises(MissingWorkerIdError):
        create_browser_config(worker_id="")


# 7: Restart with config
def test_restart_with_config_calls_quit_and_start():
    bm = BrowserManager()
    bm.quit = MagicMock()
    bm.start = MagicMock()
    
    new_config = BrowserConfig(headless=False)
    bm.restart_with_config(new_config)
    
    assert bm._current_config.headless is False
    bm.quit.assert_called_once()
    bm.start.assert_called_once()


# 8-9: Server routing
@pytest.mark.asyncio
async def test_browser_config_routing():
    from server.message_router import MessageRouter
    from server.worker_manager import WorkerManager
    from server.controller_manager import ControllerManager
    from server.session import SessionManager
    
    worker_mgr = WorkerManager()
    session_mgr = SessionManager()
    ctrl_mgr = ControllerManager(session_mgr=session_mgr)
    router = MessageRouter(worker_mgr, ctrl_mgr, session_mgr)
    
    class MockWS:
        def __init__(self):
            self.sent = []
        async def send_text(self, text):
            self.sent.append(text)
            
    # Mock worker ws
    worker_ws = MockWS()
    await worker_mgr.register_worker("w1", worker_ws)
    
    # Mock controller ws (authorized for w1)
    ctrl_ws = MockWS()
    ctrl_mgr.register_controller(ctrl_ws, authorized_workers=frozenset(["w1"]))
    
    # Test 8: Routed correctly to worker
    msg = create_browser_config(worker_id="w1", headless=False)
    await router.handle_controller_message(ctrl_ws, serialize_message(msg))
    
    assert len(worker_ws.sent) == 1
    fwd = parse_message(worker_ws.sent[0])
    assert fwd.type == "browser_config"
    assert fwd.headless is False
    
    # Test 9: Unauthorized rejected
    ctrl_ws_unauth = MockWS()
    ctrl_mgr.register_controller(ctrl_ws_unauth, authorized_workers=frozenset(["w2"]))
    
    await router.handle_controller_message(ctrl_ws_unauth, serialize_message(msg))
    assert len(ctrl_ws_unauth.sent) == 1
    err = parse_message(ctrl_ws_unauth.sent[0])
    assert err.type == "error"
    assert err.code == "UNAUTHORIZED"
