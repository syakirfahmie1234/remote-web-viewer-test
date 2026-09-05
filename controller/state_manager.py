"""
Controller State Manager.
Maintains synchronized DOM state, versions, stale flags, and metrics independently per worker_id.
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from shared.models import (
    FullSnapshotMessage,
    DomUpdateMessage,
    CommandResultMessage,
    TabOpenedMessage,
    TabClosedMessage,
    DOMDiffOp,
)
from shared.protocol import (
    STATUS_CONNECTED,
    STATUS_DISCONNECTED,
)

from shared.dom_differ import apply_diff
from shared.compression import decompress_payload

logger = logging.getLogger("controller.state_manager")

@dataclass
class TabState:
    handle: str
    url: str = ""
    title: str = ""
    html: str = ""
    dom_version: int = 0
    is_stale: bool = True

@dataclass
class WorkerStateSlot:
    """Independent state mirror for a single Worker instance."""
    worker_id: str
    status: str = STATUS_DISCONNECTED
    last_screenshot_b64: Optional[str] = None
    alert_text: Optional[str] = None
    bytes_sent: int = 0
    bytes_received: int = 0
    commands_sent: int = 0
    commands_failed: int = 0
    snapshots_received: int = 0
    diffs_received: int = 0
    last_error: Optional[str] = None
    active_tab_handle: str = ""
    tabs: Dict[str, TabState] = field(default_factory=dict)

    @property
    def active_tab(self) -> Optional[TabState]:
        return self.tabs.get(self.active_tab_handle)
    
    @property
    def dom_version(self) -> int:
        return self.active_tab.dom_version if self.active_tab else 0
        
    @property
    def url(self) -> str:
        return self.active_tab.url if self.active_tab else ""
        
    @property
    def title(self) -> str:
        return self.active_tab.title if self.active_tab else ""
        
    @property
    def html(self) -> str:
        return self.active_tab.html if self.active_tab else ""
        
    @property
    def is_stale(self) -> bool:
        return self.active_tab.is_stale if self.active_tab else True



class ControllerStateManager:
    """
    Manages state for all remote workers tracked by the Controller.
    Guarantees state isolation across worker_ids.
    """
    def __init__(self) -> None:
        self._slots: Dict[str, WorkerStateSlot] = {}

    def get_or_create_slot(self, worker_id: str) -> WorkerStateSlot:
        """Get or initialize the state slot for a specific worker_id."""
        if worker_id not in self._slots:
            self._slots[worker_id] = WorkerStateSlot(worker_id=worker_id)
        return self._slots[worker_id]

    def get_slot(self, worker_id: str) -> Optional[WorkerStateSlot]:
        """Get existing state slot for worker_id if present."""
        return self._slots.get(worker_id)

    def apply_tab_opened(self, msg: TabOpenedMessage) -> None:
        slot = self.get_or_create_slot(msg.worker_id)
        if msg.tab_handle not in slot.tabs:
            slot.tabs[msg.tab_handle] = TabState(handle=msg.tab_handle, title=msg.tab_title)
        if not slot.active_tab_handle:
            slot.active_tab_handle = msg.tab_handle

    def apply_tab_closed(self, msg: TabClosedMessage) -> None:
        slot = self.get_or_create_slot(msg.worker_id)
        if msg.tab_handle in slot.tabs:
            del slot.tabs[msg.tab_handle]
        if slot.active_tab_handle == msg.tab_handle:
            slot.active_tab_handle = next(iter(slot.tabs.keys())) if slot.tabs else ""

    def apply_full_snapshot(self, msg: FullSnapshotMessage) -> bool:
        """
        Apply a FULL_SNAPSHOT to the corresponding worker_id's state slot tab.
        """
        slot = self.get_or_create_slot(msg.worker_id)
        if msg.tab_handle not in slot.tabs:
            slot.tabs[msg.tab_handle] = TabState(handle=msg.tab_handle)
        
        slot.active_tab_handle = msg.tab_handle
        tab = slot.tabs[msg.tab_handle]
        
        tab.dom_version = msg.version
        tab.url = msg.url
        tab.title = msg.title
        tab.html = decompress_payload(msg.html, msg.compressed)
        tab.is_stale = False
        
        slot.snapshots_received += 1
        logger.info(f"Applied FULL_SNAPSHOT for '{msg.worker_id}' tab '{msg.tab_handle}' (v={msg.version})")
        return True

    def apply_dom_update(self, msg: DomUpdateMessage) -> bool:
        """
        Apply incremental DOM_UPDATE.
        """
        slot = self.get_or_create_slot(msg.worker_id)
        if msg.tab_handle not in slot.tabs:
            return False

        tab = slot.tabs[msg.tab_handle]

        if tab.is_stale or tab.dom_version != msg.base_version:
            logger.warning(f"Version mismatch for '{msg.worker_id}' tab '{msg.tab_handle}'")
            tab.is_stale = True
            return False

        tab.html = apply_diff(tab.html, msg.ops)
        tab.dom_version = msg.version
        if msg.url:
            tab.url = msg.url
            
        slot.diffs_received += 1
        logger.info(f"Applied DOM_UPDATE for '{msg.worker_id}' tab '{msg.tab_handle}' (v={msg.base_version} -> {msg.version})")
        return True

    def update_status(self, worker_id: str, status: str, dom_version: Optional[int] = None) -> None:
        """Update status for a specific worker_id."""
        slot = self.get_or_create_slot(worker_id)
        slot.status = status
        # Note: If a worker connects, we mark tabs stale until they resync.
        if status == STATUS_DISCONNECTED:
            for tab in slot.tabs.values():
                tab.is_stale = True

    def record_command_sent(self, worker_id: str) -> None:
        """Record command dispatch metric."""
        slot = self.get_or_create_slot(worker_id)
        slot.commands_sent += 1

    def record_command_result(self, msg: CommandResultMessage) -> None:
        """Record command execution result."""
        slot = self.get_or_create_slot(msg.worker_id)
        if not msg.success:
            slot.commands_failed += 1
            slot.last_error = msg.error
        if msg.payload:
            if "screenshot_base64" in msg.payload:
                slot.last_screenshot_b64 = msg.payload["screenshot_base64"]
            if "switched_to" in msg.payload:
                slot.active_tab_handle = msg.payload["switched_to"]

    def record_bytes_received(self, worker_id: str, byte_count: int) -> None:
        """Record incoming bandwidth metric."""
        if byte_count > 0:
            slot = self.get_or_create_slot(worker_id)
            slot.bytes_received += byte_count

    def record_bytes_sent(self, worker_id: str, byte_count: int) -> None:
        """Record outgoing bandwidth metric."""
        if byte_count > 0:
            slot = self.get_or_create_slot(worker_id)
            slot.bytes_sent += byte_count

    def mark_all_stale(self) -> None:
        """
        Mark all tracked worker slots as stale.
        """
        for slot in self._slots.values():
            for tab in slot.tabs.values():
                tab.is_stale = True
        logger.info("Marked all worker state slots as stale following Controller reconnect")
