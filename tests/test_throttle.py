"""
Unit and integration tests for Bandwidth Optimization & Throttling Profiles (Phase 16).
"""

import asyncio
import json
import pytest
from typing import cast
from PySide6.QtWidgets import QApplication

from shared.throttle import (
    ThrottleProfile,
    get_profile,
    get_default_profile,
    get_profile_names,
)
from shared.models import ThrottleConfigMessage
from shared.messages import (
    create_throttle_config,
    serialize_message,
    parse_message,
)
from controller.state_manager import ControllerStateManager
from controller.command_panel import CommandPanel
from server.worker_manager import WorkerManager
from fastapi import WebSocket


@pytest.fixture
def mock_websocket():
    class MockWS:
        def __init__(self):
            self.sent = []
        async def send_text(self, data):
            self.sent.append(data)
        async def close(self, code=1000, reason=""):
            pass
    return cast(WebSocket, MockWS())


def test_throttle_profiles_registry():
    """1. Verify the three built-in profiles exist and have correct values."""
    names = get_profile_names()
    assert set(names) == {"realtime", "balanced", "low_bandwidth"}
    
    realtime = get_profile("realtime")
    assert realtime is not None
    assert realtime.min_snapshot_interval_ms == 0
    assert realtime.compression_level == 1
    
    low_bandwidth = get_profile("low_bandwidth")
    assert low_bandwidth is not None
    assert low_bandwidth.min_snapshot_interval_ms == 2000
    assert low_bandwidth.compression_level == 9

    assert get_default_profile().name == "balanced"


def test_throttle_config_message_round_trip():
    """2. Serialize and deserialize ThrottleConfigMessage."""
    msg = create_throttle_config(
        worker_id="test_worker_1",
        profile_name="low_bandwidth",
        compression_level=9,
        compression_threshold=256,
        max_snapshot_bytes=2048,
        min_snapshot_interval_ms=2000,
    )
    
    serialized = serialize_message(msg)
    data = json.loads(serialized)
    assert data["type"] == "throttle_config"
    assert data["profile_name"] == "low_bandwidth"
    
    parsed = parse_message(data)
    assert isinstance(parsed, ThrottleConfigMessage)
    assert parsed.worker_id == "test_worker_1"
    assert parsed.compression_level == 9
    assert parsed.min_snapshot_interval_ms == 2000


@pytest.mark.asyncio
async def test_server_rate_limiting_logic(mock_websocket):
    """3 & 4. Verify server rate-limiting dropping fast snapshots and allowing slow ones."""
    worker_mgr = WorkerManager()
    await worker_mgr.register_worker("worker_1", mock_websocket)
    
    # Apply low_bandwidth profile (2s interval)
    worker_mgr.set_throttle_profile("worker_1", "low_bandwidth", 2000)
    
    # First snapshot should be allowed
    assert worker_mgr.should_throttle_snapshot("worker_1") is False
    worker_mgr.record_snapshot("worker_1")
    
    # Immediate second snapshot should be throttled
    assert worker_mgr.should_throttle_snapshot("worker_1") is True
    
    # Change to realtime profile (0ms interval)
    worker_mgr.set_throttle_profile("worker_1", "realtime", 0)
    
    # Should now be allowed immediately
    assert worker_mgr.should_throttle_snapshot("worker_1") is False


def test_controller_bandwidth_tracking():
    """6. Verify bytes_received increments in WorkerStateSlot."""
    state_mgr = ControllerStateManager()
    state_mgr.record_bytes_received("worker_1", 1024)
    state_mgr.record_bytes_received("worker_1", 512)
    state_mgr.record_bytes_sent("worker_1", 128)
    
    slot = state_mgr.get_slot("worker_1")
    assert slot.bytes_received == 1536
    assert slot.bytes_sent == 128


def test_throttle_profile_selector_ui():
    """7. Verify CommandPanel has QComboBox with profiles and emits signal."""
    # Ensure QApplication exists for testing widgets
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
        
    panel = CommandPanel()
    assert panel.profile_combo.count() == 3
    assert panel.profile_combo.currentText() == "balanced"
    
    # Test signal emission
    emitted_profiles = []
    panel.throttle_profile_changed.connect(lambda p: emitted_profiles.append(p))
    
    panel.profile_combo.setCurrentText("low_bandwidth")
    assert emitted_profiles == ["low_bandwidth"]
