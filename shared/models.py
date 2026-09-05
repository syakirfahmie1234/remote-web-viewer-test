"""
Data models for Website-Specific Remote Selenium System.
All message models inherit from BaseMessage or WorkerScopedMessage.
WorkerScopedMessage strictly requires worker_id.
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
import uuid
from typing import Any, Dict, List, Optional

from shared.protocol import (
    PROTOCOL_VERSION,
    MSG_HELLO,
    MSG_AUTH,
    MSG_WORKER_REGISTER,
    MSG_CONTROLLER_REGISTER,
    MSG_WORKER_STATUS,
    MSG_COMMAND,
    MSG_COMMAND_RESULT,
    MSG_FULL_SNAPSHOT,
    MSG_DOM_UPDATE,
    MSG_RESYNC_REQUEST,
    MSG_ERROR,
    MSG_PING,
    MSG_PONG,
    MSG_THROTTLE_CONFIG,
    MSG_BROWSER_CONFIG,
    MSG_ALERT_OPENED,
    MSG_TAB_OPENED,
    MSG_TAB_CLOSED,
    ALL_DOM_DIFF_OPS,
    ALL_ROLES,
    ALL_WORKER_STATUSES,
    STATUS_CONNECTED,
)


def generate_message_id() -> str:
    """Generate a random UUID4 string for message idempotency."""
    return str(uuid.uuid4())


def get_current_utc_iso() -> str:
    """Generate UTC ISO-8601 timestamp string (e.g. 2026-08-28T09:45:00.000000Z)."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class DOMDiffOp:
    """Represents a single structural DOM difference operation."""
    op: str
    selector: str
    html: Optional[str] = None
    text: Optional[str] = None
    attr: Optional[str] = None
    value: Optional[str] = None
    position: Optional[str] = None

    def __post_init__(self) -> None:
        if self.op not in ALL_DOM_DIFF_OPS:
            raise ValueError(f"Invalid DOM diff op '{self.op}'. Must be one of {sorted(ALL_DOM_DIFF_OPS)}")
        if not self.selector or not isinstance(self.selector, str):
            raise ValueError("DOMDiffOp requires a valid non-empty string selector")

    def to_dict(self) -> Dict[str, Any]:
        """Serialize operation to dictionary excluding None values."""
        res: Dict[str, Any] = {"op": self.op, "selector": self.selector}
        if self.html is not None:
            res["html"] = self.html
        if self.text is not None:
            res["text"] = self.text
        if self.attr is not None:
            res["attr"] = self.attr
        if self.value is not None:
            res["value"] = self.value
        if self.position is not None:
            res["position"] = self.position
        return res

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> DOMDiffOp:
        return cls(
            op=d["op"],
            selector=d["selector"],
            html=d.get("html"),
            text=d.get("text"),
            attr=d.get("attr"),
            value=d.get("value"),
            position=d.get("position"),
        )


@dataclass
class BaseMessage:
    """
    Universal base message envelope. Every message sent over the wire contains
    type, message_id, timestamp, and protocol_version.
    """
    type: str
    message_id: str = field(default_factory=generate_message_id)
    timestamp: str = field(default_factory=get_current_utc_iso)
    protocol_version: int = PROTOCOL_VERSION

    def __post_init__(self) -> None:
        if not self.type:
            raise ValueError("Message type cannot be empty")
        if not self.message_id:
            raise ValueError("Message message_id cannot be empty")
        if self.protocol_version != PROTOCOL_VERSION:
            raise ValueError(
                f"Protocol version mismatch: expected {PROTOCOL_VERSION}, got {self.protocol_version}"
            )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class WorkerScopedMessage(BaseMessage):
    """
    Base message for all Worker-scoped communication.
    Enforces that worker_id is strictly provided, non-empty, and a string.
    """
    worker_id: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.worker_id or not isinstance(self.worker_id, str) or not self.worker_id.strip():
            raise ValueError(f"Worker-scoped message '{self.type}' strictly requires a non-empty worker_id")
        self.worker_id = self.worker_id.strip()


# Specific Message Definitions

@dataclass
class HelloMessage(BaseMessage):
    role: str = ""

    def __post_init__(self) -> None:
        self.type = MSG_HELLO
        super().__post_init__()
        if self.role not in ALL_ROLES:
            raise ValueError(f"Invalid role '{self.role}'. Must be one of {sorted(ALL_ROLES)}")


@dataclass
class AuthMessage(BaseMessage):
    token: str = ""

    def __post_init__(self) -> None:
        self.type = MSG_AUTH
        super().__post_init__()
        if not self.token or not isinstance(self.token, str):
            raise ValueError("Auth message requires a non-empty token string")


@dataclass
class WorkerRegisterMessage(WorkerScopedMessage):
    capabilities: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.type = MSG_WORKER_REGISTER
        super().__post_init__()


@dataclass
class ControllerRegisterMessage(BaseMessage):
    client_id: Optional[str] = None
    subscribed_worker_id: Optional[str] = None
    subscribed_worker_ids: Optional[List[str]] = None

    def __post_init__(self) -> None:
        self.type = MSG_CONTROLLER_REGISTER
        super().__post_init__()


@dataclass
class WorkerStatusMessage(WorkerScopedMessage):
    status: str = STATUS_CONNECTED
    dom_version: Optional[int] = None

    def __post_init__(self) -> None:
        self.type = MSG_WORKER_STATUS
        super().__post_init__()
        if self.status not in ALL_WORKER_STATUSES:
            raise ValueError(f"Invalid worker status '{self.status}'. Must be one of {sorted(ALL_WORKER_STATUSES)}")


@dataclass
class CommandMessage(WorkerScopedMessage):
    command: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.type = MSG_COMMAND
        super().__post_init__()
        if not self.command or not isinstance(self.command, str):
            raise ValueError("CommandMessage requires a valid non-empty command name")


@dataclass
class CommandResultMessage(WorkerScopedMessage):
    command: str = ""
    success: bool = True
    error: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None

    def __post_init__(self) -> None:
        self.type = MSG_COMMAND_RESULT
        super().__post_init__()
        if not self.command:
            raise ValueError("CommandResultMessage requires command name")


@dataclass
class FullSnapshotMessage(WorkerScopedMessage):
    version: int = 0
    url: str = ""
    title: str = ""
    html: str = ""
    compressed: bool = False
    tab_handle: str = ""

    def __post_init__(self) -> None:
        self.type = MSG_FULL_SNAPSHOT
        super().__post_init__()
        if not isinstance(self.version, int):
            raise ValueError("FullSnapshotMessage version must be an integer")


@dataclass
class DomUpdateMessage(WorkerScopedMessage):
    base_version: int = 0
    version: int = 0
    ops: List[DOMDiffOp] = field(default_factory=list)
    compressed: bool = False
    url: str = ""
    tab_handle: str = ""

    def __post_init__(self) -> None:
        self.type = MSG_DOM_UPDATE
        super().__post_init__()
        if not isinstance(self.base_version, int) or not isinstance(self.version, int):
            raise ValueError("DomUpdateMessage base_version and version must be integers")
        # Ensure ops are DOMDiffOp instances
        normalized_ops: List[DOMDiffOp] = []
        for op in self.ops:
            if isinstance(op, dict):
                normalized_ops.append(DOMDiffOp.from_dict(op))
            elif isinstance(op, DOMDiffOp):
                normalized_ops.append(op)
            else:
                raise ValueError(f"Invalid diff op type: {type(op)}")
        self.ops = normalized_ops

    def to_dict(self) -> Dict[str, Any]:
        d = super().to_dict()
        d["ops"] = [op.to_dict() if isinstance(op, DOMDiffOp) else op for op in self.ops]
        return d


@dataclass
class ResyncRequestMessage(WorkerScopedMessage):
    reason: str = ""

    def __post_init__(self) -> None:
        self.type = MSG_RESYNC_REQUEST
        super().__post_init__()


@dataclass
class ErrorMessage(BaseMessage):
    code: str = ""
    detail: str = ""
    worker_id: Optional[str] = None

    def __post_init__(self) -> None:
        self.type = MSG_ERROR
        super().__post_init__()
        if not self.code:
            raise ValueError("ErrorMessage requires a non-empty code")


@dataclass
class PingMessage(BaseMessage):
    payload: Optional[str] = None

    def __post_init__(self) -> None:
        self.type = MSG_PING
        super().__post_init__()


@dataclass
class PongMessage(BaseMessage):
    payload: Optional[str] = None

    def __post_init__(self) -> None:
        self.type = MSG_PONG
        super().__post_init__()


@dataclass
class ThrottleConfigMessage(WorkerScopedMessage):
    """
    Sent from Controller to Worker (via Server) to change the active throttle profile.
    Contains the profile parameters the Worker should apply to compression and snapshot timing.
    """
    profile_name: str = "balanced"
    compression_level: int = 3
    compression_threshold: int = 1024
    max_snapshot_bytes: int = 0
    min_snapshot_interval_ms: int = 500

    def __post_init__(self) -> None:
        self.type = MSG_THROTTLE_CONFIG
        super().__post_init__()
        if not 1 <= self.compression_level <= 9:
            raise ValueError(f"compression_level must be 1-9, got {self.compression_level}")


@dataclass
class BrowserConfigMessage(WorkerScopedMessage):
    """
    Sent from Controller to Worker to change browser configuration (headless/proxy) at runtime.
    Requires the Worker to restart its internal Chrome session.
    """
    headless: bool = True
    proxy_url: Optional[str] = None

    def __post_init__(self) -> None:
        self.type = MSG_BROWSER_CONFIG
        super().__post_init__()
@dataclass
class AlertOpenedMessage(WorkerScopedMessage):
    alert_text: str = ""

    def __post_init__(self) -> None:
        self.type = MSG_ALERT_OPENED
        super().__post_init__()


@dataclass
class TabOpenedMessage(WorkerScopedMessage):
    tab_handle: str = ""
    tab_title: str = ""

    def __post_init__(self) -> None:
        self.type = MSG_TAB_OPENED
        super().__post_init__()


@dataclass
class TabClosedMessage(WorkerScopedMessage):
    tab_handle: str = ""

    def __post_init__(self) -> None:
        self.type = MSG_TAB_CLOSED
        super().__post_init__()

