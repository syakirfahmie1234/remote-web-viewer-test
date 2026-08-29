"""
Controller Worker Selection and Inventory Manager.
Tracks all discovered Workers, their online/offline state, and the active Worker selection.
"""

from __future__ import annotations
import logging
from typing import Dict, List, Optional
from PySide6.QtCore import QObject, Signal

logger = logging.getLogger("controller.worker_manager")


class ControllerWorkerManager(QObject):
    """
    Manages the list of known Workers and the currently active Worker selection.
    Emits Qt signals when workers are updated or selection changes.
    """
    workers_updated = Signal()  # Emitted when worker list/status changes
    active_worker_changed = Signal(str)  # Emitted with new active worker_id

    def __init__(self) -> None:
        super().__init__()
        # worker_id -> Dict metadata
        self._workers: Dict[str, Dict[str, any]] = {}
        self._active_worker_id: Optional[str] = None

    @property
    def active_worker_id(self) -> Optional[str]:
        """Get the currently selected active worker_id."""
        return self._active_worker_id

    def get_known_workers(self) -> List[Dict[str, any]]:
        """Return list of all known workers with their current status."""
        return list(self._workers.values())

    def update_worker_status(
        self,
        worker_id: str,
        status: str,
        dom_version: Optional[int] = None,
    ) -> None:
        """Add or update a worker in the known list."""
        if worker_id not in self._workers:
            self._workers[worker_id] = {
                "worker_id": worker_id,
                "status": status,
                "dom_version": dom_version or 0,
            }
        else:
            self._workers[worker_id]["status"] = status
            if dom_version is not None:
                self._workers[worker_id]["dom_version"] = dom_version

        # Default active worker to first discovered worker if none active
        if self._active_worker_id is None:
            self.select_worker(worker_id)

        self.workers_updated.emit()

    def select_worker(self, worker_id: str) -> None:
        """
        Switch the active Worker to worker_id.
        Does NOT touch or discard other workers' state.
        """
        if self._active_worker_id != worker_id:
            self._active_worker_id = worker_id
            logger.info(f"Switched active worker to '{worker_id}'")
            self.active_worker_changed.emit(worker_id)
            self.workers_updated.emit()
