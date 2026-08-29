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
    DOMDiffOp,
)
from shared.protocol import (
    STATUS_CONNECTED,
    STATUS_DISCONNECTED,
    OP_ADD,
    OP_REMOVE,
    OP_REPLACE,
    OP_TEXT,
    OP_ATTRIBUTE,
    OP_VALUE,
)

from shared.dom_differ import apply_diff
from shared.compression import decompress_payload

logger = logging.getLogger("controller.state_manager")


@dataclass
class WorkerStateSlot:
    """Independent state mirror for a single Worker instance."""
    worker_id: str
    status: str = STATUS_DISCONNECTED
    dom_version: int = 0
    is_stale: bool = True
    url: str = ""
    title: str = ""
    html: str = ""
    last_screenshot_b64: Optional[str] = None
    bytes_sent: int = 0
    bytes_received: int = 0
    commands_sent: int = 0
    commands_failed: int = 0
    snapshots_received: int = 0
    diffs_received: int = 0
    last_error: Optional[str] = None


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

    def apply_full_snapshot(self, msg: FullSnapshotMessage) -> bool:
        """
        Apply a FULL_SNAPSHOT to the corresponding worker_id's state slot.
        Resets stale flag and advances version counter.
        Transparently decompresses payload if compressed over the wire.
        """
        slot = self.get_or_create_slot(msg.worker_id)
        slot.dom_version = msg.version
        slot.url = msg.url
        slot.title = msg.title
        slot.html = decompress_payload(msg.html, msg.compressed)
        slot.is_stale = False
        slot.snapshots_received += 1
        logger.info(f"Applied FULL_SNAPSHOT for '{msg.worker_id}' (v={msg.version}, url={msg.url}, compressed={msg.compressed})")
        return True

    def apply_dom_update(self, msg: DomUpdateMessage) -> bool:
        """
        Apply incremental DOM_UPDATE.
        Checks that current_version == base_version for this worker_id.
        Returns True if applied successfully, False if version mismatch (stale).
        """
        slot = self.get_or_create_slot(msg.worker_id)

        if slot.is_stale or slot.dom_version != msg.base_version:
            logger.warning(
                f"Version mismatch for '{msg.worker_id}': local v={slot.dom_version} != base v={msg.base_version}. Flagging stale."
            )
            slot.is_stale = True
            return False

        # Apply operations to HTML mirror using DOM differ patch engine
        slot.html = apply_diff(slot.html, msg.ops)
        slot.dom_version = msg.version
        slot.diffs_received += 1
        logger.info(f"Applied DOM_UPDATE for '{msg.worker_id}' (v={msg.base_version} -> {msg.version})")
        return True

    def update_status(self, worker_id: str, status: str, dom_version: Optional[int] = None) -> None:
        """Update status for a specific worker_id."""
        slot = self.get_or_create_slot(worker_id)
        slot.status = status
        if dom_version is not None:
            slot.dom_version = dom_version
        if status == STATUS_DISCONNECTED:
            slot.is_stale = True

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
        if msg.payload and "screenshot_base64" in msg.payload:
            slot.last_screenshot_b64 = msg.payload["screenshot_base64"]

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
        Called unconditionally upon Controller reconnect.
        """
        for slot in self._slots.values():
            slot.is_stale = True
        logger.info("Marked all worker state slots as stale following Controller reconnect")
