"""
Controller Command Queue.
Manages per-Worker command queuing, enforces strict sequential execution per Worker,
tracks command states (QUEUED, IN_FLIGHT, SUCCESS, FAILED, TIMED_OUT), and emits Qt signals for UI.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
import logging
from typing import Callable, Dict, List, Optional
import uuid

from PySide6.QtCore import QObject, Signal, QTimer

from shared.models import CommandMessage, CommandResultMessage
from shared.messages import create_command

logger = logging.getLogger("controller.command_queue")

STATE_QUEUED = "QUEUED"
STATE_IN_FLIGHT = "IN_FLIGHT"
STATE_SUCCESS = "SUCCESS"
STATE_FAILED = "FAILED"
STATE_TIMED_OUT = "TIMED_OUT"


@dataclass
class CommandItem:
    """Individual command lifecycle record."""
    command_id: str
    worker_id: str
    command: str
    payload: dict
    state: str = STATE_QUEUED
    submitted_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    duration_ms: Optional[float] = None
    error: Optional[str] = None
    result_payload: Optional[dict] = None


class WorkerCommandQueue:
    """Queue and in-flight tracker for a single Worker instance."""
    def __init__(self, worker_id: str) -> None:
        self.worker_id = worker_id
        self.queue: List[CommandItem] = []
        self.in_flight: Optional[CommandItem] = None
        self.history: List[CommandItem] = []  # Completed commands


class ControllerCommandQueue(QObject):
    """
    Orchestrates command queues across all Workers.
    Enforces sequential execution per Worker without blocking other Workers.
    """
    command_state_changed = Signal(object)  # Emits CommandItem
    queue_updated = Signal(str)            # Emits worker_id when its queue changes
    command_failed = Signal(object)         # Emits CommandItem when a command fails

    def __init__(
        self,
        send_message_fn: Optional[Callable[[CommandMessage], None]] = None,
        default_timeout_seconds: float = 15.0,
    ) -> None:
        super().__init__()
        self._send_message_fn = send_message_fn
        self.default_timeout_seconds = default_timeout_seconds
        # worker_id -> WorkerCommandQueue
        self._worker_queues: Dict[str, WorkerCommandQueue] = {}

    def set_send_message_fn(self, fn: Callable[[CommandMessage], None]) -> None:
        """Register the WebSocket send callback."""
        self._send_message_fn = fn

    def _get_or_create_queue(self, worker_id: str) -> WorkerCommandQueue:
        if worker_id not in self._worker_queues:
            self._worker_queues[worker_id] = WorkerCommandQueue(worker_id)
        return self._worker_queues[worker_id]

    def enqueue_command(self, worker_id: str, command: str, payload: dict) -> CommandItem:
        """
        Enqueue a command for a specific Worker.
        If no command is currently in-flight for this Worker, dispatches immediately.
        """
        cmd_id = f"cmd-{uuid.uuid4().hex[:8]}"
        item = CommandItem(
            command_id=cmd_id,
            worker_id=worker_id,
            command=command,
            payload=payload,
            state=STATE_QUEUED,
        )

        wq = self._get_or_create_queue(worker_id)
        wq.queue.append(item)
        logger.info(f"Enqueued command '{command}' ({cmd_id}) for '{worker_id}'. Queue length: {len(wq.queue)}")

        self.command_state_changed.emit(item)
        self.queue_updated.emit(worker_id)

        # Trigger processing if idle
        self._process_next(worker_id)
        return item

    def handle_command_result(self, msg: CommandResultMessage) -> Optional[CommandItem]:
        """
        Process COMMAND_RESULT from server for a Worker.
        Updates state of the in-flight command and triggers dispatch of the next queued command.
        """
        wq = self._get_or_create_queue(msg.worker_id)
        in_flight = wq.in_flight

        if in_flight is None:
            logger.warning(f"Received result for '{msg.worker_id}' but no command was marked in-flight")
            return None

        now = datetime.now()
        in_flight.completed_at = now
        in_flight.duration_ms = (now - in_flight.submitted_at).total_seconds() * 1000.0
        in_flight.result_payload = msg.payload

        if msg.success:
            in_flight.state = STATE_SUCCESS
            logger.info(f"Command '{in_flight.command}' ({in_flight.command_id}) on '{msg.worker_id}' SUCCEEDED ({in_flight.duration_ms:.1f}ms)")
        else:
            in_flight.state = STATE_FAILED
            in_flight.error = msg.error or "Unknown error"
            logger.warning(f"Command '{in_flight.command}' ({in_flight.command_id}) on '{msg.worker_id}' FAILED: {in_flight.error}")
            self.command_failed.emit(in_flight)

        # Move to history
        wq.history.append(in_flight)
        completed_item = in_flight
        wq.in_flight = None

        self.command_state_changed.emit(completed_item)
        self.queue_updated.emit(msg.worker_id)

        # Process next command in queue for this Worker
        self._process_next(msg.worker_id)
        return completed_item

    def handle_timeout(self, worker_id: str, command_id: str) -> None:
        """Handle command timeout when no result is received within deadline."""
        wq = self._get_or_create_queue(worker_id)
        in_flight = wq.in_flight

        if in_flight and in_flight.command_id == command_id:
            now = datetime.now()
            in_flight.completed_at = now
            in_flight.duration_ms = (now - in_flight.submitted_at).total_seconds() * 1000.0
            in_flight.state = STATE_TIMED_OUT
            in_flight.error = f"Command timed out after {self.default_timeout_seconds}s"
            logger.error(f"Command '{in_flight.command}' ({command_id}) on '{worker_id}' TIMED OUT")

            wq.history.append(in_flight)
            wq.in_flight = None

            self.command_state_changed.emit(in_flight)
            self.command_failed.emit(in_flight)
            self.queue_updated.emit(worker_id)

            self._process_next(worker_id)

    def _process_next(self, worker_id: str) -> None:
        """Dispatch the next queued command for worker_id if no command is in-flight."""
        wq = self._get_or_create_queue(worker_id)

        if wq.in_flight is not None:
            # Already executing a command on this Worker
            return

        if not wq.queue:
            # Nothing in queue
            return

        next_item = wq.queue.pop(0)
        next_item.state = STATE_IN_FLIGHT
        wq.in_flight = next_item

        logger.info(f"Dispatching '{next_item.command}' ({next_item.command_id}) to '{worker_id}'")
        self.command_state_changed.emit(next_item)
        self.queue_updated.emit(worker_id)

        # Send over WebSocket
        if self._send_message_fn:
            msg = create_command(
                worker_id=worker_id,
                command=next_item.command,
                payload=next_item.payload,
            )
            try:
                self._send_message_fn(msg)
            except Exception as e:
                logger.error(f"Failed to dispatch command message: {e}")
                self.handle_command_result(
                    CommandResultMessage(
                        worker_id=worker_id,
                        command=next_item.command,
                        success=False,
                        error=f"Dispatch error: {str(e)}",
                    )
                )

    def get_items_for_worker(self, worker_id: str) -> List[CommandItem]:
        """Get all items (in-flight, queued, and recent history) for a specific Worker."""
        wq = self._get_or_create_queue(worker_id)
        items: List[CommandItem] = []
        if wq.in_flight:
            items.append(wq.in_flight)
        items.extend(wq.queue)
        # Append last 20 history items
        items.extend(reversed(wq.history[-20:]))
        return items

    def clear_history(self, worker_id: str) -> None:
        """Clear completed command history for a Worker."""
        wq = self._get_or_create_queue(worker_id)
        wq.history.clear()
        self.queue_updated.emit(worker_id)
