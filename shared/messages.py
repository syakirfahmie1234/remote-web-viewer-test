"""
Message creation, serialization, parsing, and validation helpers.
Enforces the mandatory worker_id requirement on Worker-scoped message types and
validates against the fixed command allowlist.
"""

from __future__ import annotations
import json
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Union

from shared.protocol import (
    PROTOCOL_VERSION,
    ALL_MESSAGE_TYPES,
    WORKER_SCOPED_TYPES,
    COMMAND_ALLOWLIST,
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
    MSG_TAB_OPENED,
    MSG_TAB_CLOSED,
    MSG_ALERT_OPENED,
)
from shared.models import (
    BaseMessage,
    WorkerScopedMessage,
    HelloMessage,
    AuthMessage,
    WorkerRegisterMessage,
    ControllerRegisterMessage,
    WorkerStatusMessage,
    CommandMessage,
    CommandResultMessage,
    FullSnapshotMessage,
    DomUpdateMessage,
    ResyncRequestMessage,
    ErrorMessage,
    PingMessage,
    PongMessage,
    ThrottleConfigMessage,
    BrowserConfigMessage,
    DOMDiffOp,
    AlertOpenedMessage,
    TabOpenedMessage,
    TabClosedMessage,
    generate_message_id,
    get_current_utc_iso,
)


class ProtocolError(Exception):
    """Base exception for all protocol-level errors."""
    pass


class ProtocolVersionMismatchError(ProtocolError):
    """Raised when an incoming message has an unrecognized or mismatched protocol_version."""
    def __init__(self, expected: int, received: Any):
        super().__init__(f"Protocol version mismatch: expected {expected}, received {received}")
        self.expected = expected
        self.received = received


class UnknownMessageTypeError(ProtocolError):
    """Raised when an incoming message type is not recognized."""
    def __init__(self, message_type: str):
        super().__init__(f"Unknown message type '{message_type}'")
        self.message_type = message_type


class MissingWorkerIdError(ProtocolError):
    """Raised when a Worker-scoped message is missing the required worker_id."""
    def __init__(self, message_type: str):
        super().__init__(f"Worker-scoped message '{message_type}' requires a non-empty worker_id")
        self.message_type = message_type


class InvalidCommandError(ProtocolError):
    """Raised when a command is not present in the COMMAND_ALLOWLIST."""
    def __init__(self, command: str):
        super().__init__(
            f"Command '{command}' is not allowed. Must be one of {sorted(COMMAND_ALLOWLIST)}"
        )
        self.command = command


class MessageValidationError(ProtocolError):
    """Raised when message content fails validation constraints."""
    pass


# Message Constructors — Enforcing worker_id on all Worker-scoped types

def create_hello(role: str) -> HelloMessage:
    """Create a HELLO message."""
    return HelloMessage(
        type=MSG_HELLO,
        role=role,
        message_id=generate_message_id(),
        timestamp=get_current_utc_iso(),
        protocol_version=PROTOCOL_VERSION,
    )


def create_auth(token: str) -> AuthMessage:
    """Create an AUTH message."""
    return AuthMessage(
        type=MSG_AUTH,
        token=token,
        message_id=generate_message_id(),
        timestamp=get_current_utc_iso(),
        protocol_version=PROTOCOL_VERSION,
    )


def create_worker_register(worker_id: str, capabilities: Optional[Dict[str, Any]] = None) -> WorkerRegisterMessage:
    """Create a WORKER_REGISTER message with mandatory worker_id."""
    if not worker_id or not isinstance(worker_id, str) or not worker_id.strip():
        raise MissingWorkerIdError(MSG_WORKER_REGISTER)
    return WorkerRegisterMessage(
        type=MSG_WORKER_REGISTER,
        worker_id=worker_id.strip(),
        capabilities=capabilities or {},
        message_id=generate_message_id(),
        timestamp=get_current_utc_iso(),
        protocol_version=PROTOCOL_VERSION,
    )


def create_controller_register(
    client_id: Optional[str] = None,
    subscribed_worker_id: Optional[str] = None,
    subscribed_worker_ids: Optional[List[str]] = None,
) -> ControllerRegisterMessage:
    """Create a CONTROLLER_REGISTER message."""
    return ControllerRegisterMessage(
        type=MSG_CONTROLLER_REGISTER,
        client_id=client_id,
        subscribed_worker_id=subscribed_worker_id,
        subscribed_worker_ids=subscribed_worker_ids,
        message_id=generate_message_id(),
        timestamp=get_current_utc_iso(),
        protocol_version=PROTOCOL_VERSION,
    )


def create_worker_status(
    worker_id: str,
    status: str,
    dom_version: Optional[int] = None,
) -> WorkerStatusMessage:
    """Create a WORKER_STATUS message with mandatory worker_id."""
    if not worker_id or not isinstance(worker_id, str) or not worker_id.strip():
        raise MissingWorkerIdError(MSG_WORKER_STATUS)
    return WorkerStatusMessage(
        type=MSG_WORKER_STATUS,
        worker_id=worker_id.strip(),
        status=status,
        dom_version=dom_version,
        message_id=generate_message_id(),
        timestamp=get_current_utc_iso(),
        protocol_version=PROTOCOL_VERSION,
    )


def create_command(
    worker_id: str,
    command: str,
    payload: Optional[Dict[str, Any]] = None,
) -> CommandMessage:
    """Create a COMMAND message with mandatory worker_id and allowlist check."""
    if not worker_id or not isinstance(worker_id, str) or not worker_id.strip():
        raise MissingWorkerIdError(MSG_COMMAND)
    if command not in COMMAND_ALLOWLIST:
        raise InvalidCommandError(command)
    return CommandMessage(
        type=MSG_COMMAND,
        worker_id=worker_id.strip(),
        command=command,
        payload=payload or {},
        message_id=generate_message_id(),
        timestamp=get_current_utc_iso(),
        protocol_version=PROTOCOL_VERSION,
    )


def create_command_result(
    worker_id: str,
    command: str,
    success: bool,
    error: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> CommandResultMessage:
    """Create a COMMAND_RESULT message with mandatory worker_id."""
    if not worker_id or not isinstance(worker_id, str) or not worker_id.strip():
        raise MissingWorkerIdError(MSG_COMMAND_RESULT)
    return CommandResultMessage(
        type=MSG_COMMAND_RESULT,
        worker_id=worker_id.strip(),
        command=command,
        success=success,
        error=error,
        payload=payload,
        message_id=generate_message_id(),
        timestamp=get_current_utc_iso(),
        protocol_version=PROTOCOL_VERSION,
    )


def create_full_snapshot(
    worker_id: str,
    version: int,
    url: str,
    title: str,
    html: str,
    compressed: bool = False,
    tab_handle: str = "",
) -> FullSnapshotMessage:
    """Create a FULL_SNAPSHOT message with mandatory worker_id."""
    if not worker_id or not isinstance(worker_id, str) or not worker_id.strip():
        raise MissingWorkerIdError(MSG_FULL_SNAPSHOT)
    return FullSnapshotMessage(
        type=MSG_FULL_SNAPSHOT,
        worker_id=worker_id.strip(),
        version=version,
        url=url,
        title=title,
        html=html,
        compressed=compressed,
        tab_handle=tab_handle,
        message_id=generate_message_id(),
        timestamp=get_current_utc_iso(),
        protocol_version=PROTOCOL_VERSION,
    )


def create_dom_update(
    worker_id: str,
    base_version: int,
    version: int,
    ops: List[Union[DOMDiffOp, Dict[str, Any]]],
    compressed: bool = False,
    url: str = "",
    tab_handle: str = "",
) -> DomUpdateMessage:
    """Create a DOM_UPDATE message with mandatory worker_id and structured ops."""
    if not worker_id or not isinstance(worker_id, str) or not worker_id.strip():
        raise MissingWorkerIdError(MSG_DOM_UPDATE)
    normalized_ops = [
        op if isinstance(op, DOMDiffOp) else DOMDiffOp.from_dict(op)
        for op in ops
    ]
    return DomUpdateMessage(
        type=MSG_DOM_UPDATE,
        worker_id=worker_id.strip(),
        base_version=base_version,
        version=version,
        ops=normalized_ops,
        compressed=compressed,
        tab_handle=tab_handle,
        message_id=generate_message_id(),
        timestamp=get_current_utc_iso(),
        protocol_version=PROTOCOL_VERSION,
    )


def create_resync_request(worker_id: str, reason: str = "") -> ResyncRequestMessage:
    """Create a RESYNC_REQUEST message with mandatory worker_id."""
    if not worker_id or not isinstance(worker_id, str) or not worker_id.strip():
        raise MissingWorkerIdError(MSG_RESYNC_REQUEST)
    return ResyncRequestMessage(
        type=MSG_RESYNC_REQUEST,
        worker_id=worker_id.strip(),
        reason=reason,
        message_id=generate_message_id(),
        timestamp=get_current_utc_iso(),
        protocol_version=PROTOCOL_VERSION,
    )


def create_error(code: str, detail: str, worker_id: Optional[str] = None) -> ErrorMessage:
    """Create an ERROR message, optionally tagged with worker_id if worker-scoped."""
    return ErrorMessage(
        type=MSG_ERROR,
        code=code,
        detail=detail,
        worker_id=worker_id.strip() if worker_id else None,
        message_id=generate_message_id(),
        timestamp=get_current_utc_iso(),
        protocol_version=PROTOCOL_VERSION,
    )


def create_ping(payload: Optional[str] = None) -> PingMessage:
    """Create a PING message."""
    return PingMessage(
        type=MSG_PING,
        payload=payload,
        message_id=generate_message_id(),
        timestamp=get_current_utc_iso(),
        protocol_version=PROTOCOL_VERSION,
    )


def create_pong(payload: Optional[str] = None) -> PongMessage:
    """Create a PONG message."""
    return PongMessage(
        type=MSG_PONG,
        payload=payload,
        message_id=generate_message_id(),
        timestamp=get_current_utc_iso(),
        protocol_version=PROTOCOL_VERSION,
    )

def create_throttle_config(
    worker_id: str,
    profile_name: str = "balanced",
    compression_level: int = 3,
    compression_threshold: int = 1024,
    max_snapshot_bytes: int = 0,
    min_snapshot_interval_ms: int = 500,
) -> ThrottleConfigMessage:
    """Create a THROTTLE_CONFIG message to send to a Worker."""
    return ThrottleConfigMessage(
        type=MSG_THROTTLE_CONFIG,
        worker_id=worker_id,
        profile_name=profile_name,
        compression_level=compression_level,
        compression_threshold=compression_threshold,
        max_snapshot_bytes=max_snapshot_bytes,
        min_snapshot_interval_ms=min_snapshot_interval_ms,
        message_id=generate_message_id(),
        timestamp=get_current_utc_iso(),
        protocol_version=PROTOCOL_VERSION,
    )


# Serialization and Parsing

def message_to_dict(msg: BaseMessage) -> Dict[str, Any]:
    """Convert any BaseMessage instance to dictionary."""
    return msg.to_dict()

def create_browser_config(
    worker_id: str,
    headless: bool = True,
    proxy_url: Optional[str] = None,
) -> BrowserConfigMessage:
    if not worker_id or not isinstance(worker_id, str) or not worker_id.strip():
        raise MissingWorkerIdError(MSG_BROWSER_CONFIG)
    return BrowserConfigMessage(
        type=MSG_BROWSER_CONFIG,
        worker_id=worker_id,
        headless=headless,
        proxy_url=proxy_url,
        message_id=generate_message_id(),
        timestamp=get_current_utc_iso(),
        protocol_version=PROTOCOL_VERSION,
    )


def serialize_message(msg: BaseMessage) -> str:
    """Serialize any BaseMessage instance to JSON string."""
    return json.dumps(message_to_dict(msg), ensure_ascii=False)


def parse_message(raw: Union[str, bytes, Dict[str, Any]]) -> BaseMessage:
    """
    Parse and validate a raw JSON string, bytes, or dictionary into the appropriate BaseMessage model.
    Strictly checks protocol_version, message type, and worker_id requirements.
    """
    if isinstance(raw, (str, bytes)):
        try:
            data: Dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MessageValidationError(f"Invalid JSON message format: {exc}") from exc
    elif isinstance(raw, dict):
        data = raw
    else:
        raise MessageValidationError(f"Expected str, bytes or dict, got {type(raw).__name__}")

    # Universal envelope fields validation
    msg_type = data.get("type")
    if not msg_type or not isinstance(msg_type, str):
        raise MessageValidationError("Missing or invalid 'type' field in message")

    if msg_type not in ALL_MESSAGE_TYPES:
        raise UnknownMessageTypeError(msg_type)

    protocol_ver = data.get("protocol_version")
    if protocol_ver is None:
        raise MessageValidationError("Missing required 'protocol_version' field")
    if protocol_ver != PROTOCOL_VERSION:
        raise ProtocolVersionMismatchError(expected=PROTOCOL_VERSION, received=protocol_ver)

    msg_id = data.get("message_id")
    if not msg_id or not isinstance(msg_id, str):
        raise MessageValidationError("Missing or invalid 'message_id' field")

    timestamp = data.get("timestamp")
    if not timestamp or not isinstance(timestamp, str):
        raise MessageValidationError("Missing or invalid 'timestamp' field")

    # Check Worker-scoped requirement
    worker_id = data.get("worker_id")
    if msg_type in WORKER_SCOPED_TYPES:
        if not worker_id or not isinstance(worker_id, str) or not worker_id.strip():
            raise MissingWorkerIdError(msg_type)

    # Route to specific model constructor
    try:
        if msg_type == MSG_HELLO:
            return HelloMessage(
                type=MSG_HELLO,
                role=data.get("role", ""),
                message_id=msg_id,
                timestamp=timestamp,
                protocol_version=protocol_ver,
            )
        elif msg_type == MSG_AUTH:
            return AuthMessage(
                type=MSG_AUTH,
                token=data.get("token", ""),
                message_id=msg_id,
                timestamp=timestamp,
                protocol_version=protocol_ver,
            )
        elif msg_type == MSG_WORKER_REGISTER:
            return WorkerRegisterMessage(
                type=MSG_WORKER_REGISTER,
                worker_id=worker_id,
                capabilities=data.get("capabilities", {}),
                message_id=msg_id,
                timestamp=timestamp,
                protocol_version=protocol_ver,
            )
        elif msg_type == MSG_CONTROLLER_REGISTER:
            return ControllerRegisterMessage(
                type=MSG_CONTROLLER_REGISTER,
                client_id=data.get("client_id"),
                subscribed_worker_id=data.get("subscribed_worker_id"),
                subscribed_worker_ids=data.get("subscribed_worker_ids"),
                message_id=msg_id,
                timestamp=timestamp,
                protocol_version=protocol_ver,
            )
        elif msg_type == MSG_WORKER_STATUS:
            return WorkerStatusMessage(
                type=MSG_WORKER_STATUS,
                worker_id=worker_id,
                status=data.get("status", ""),
                dom_version=data.get("dom_version"),
                message_id=msg_id,
                timestamp=timestamp,
                protocol_version=protocol_ver,
            )
        elif msg_type == MSG_COMMAND:
            cmd = data.get("command", "")
            if cmd not in COMMAND_ALLOWLIST:
                raise InvalidCommandError(cmd)
            return CommandMessage(
                type=MSG_COMMAND,
                worker_id=worker_id,
                command=cmd,
                payload=data.get("payload", {}),
                message_id=msg_id,
                timestamp=timestamp,
                protocol_version=protocol_ver,
            )
        elif msg_type == MSG_COMMAND_RESULT:
            return CommandResultMessage(
                type=MSG_COMMAND_RESULT,
                worker_id=worker_id,
                command=data.get("command", ""),
                success=bool(data.get("success", True)),
                error=data.get("error"),
                payload=data.get("payload"),
                message_id=msg_id,
                timestamp=timestamp,
                protocol_version=protocol_ver,
            )
        elif msg_type == MSG_FULL_SNAPSHOT:
            return FullSnapshotMessage(
                type=MSG_FULL_SNAPSHOT,
                worker_id=worker_id,
                version=int(data.get("version", 0)),
                url=str(data.get("url", "")),
                title=str(data.get("title", "")),
                html=str(data.get("html", "")),
                compressed=bool(data.get("compressed", False)),
                tab_handle=str(data.get("tab_handle", "")),
                message_id=msg_id,
                timestamp=timestamp,
                protocol_version=protocol_ver,
            )
        elif msg_type == MSG_DOM_UPDATE:
            raw_ops = data.get("ops", [])
            ops = [
                DOMDiffOp.from_dict(op) if isinstance(op, dict) else op
                for op in raw_ops
            ]
            return DomUpdateMessage(
                type=MSG_DOM_UPDATE,
                worker_id=worker_id,
                base_version=int(data.get("base_version", 0)),
                version=int(data.get("version", 0)),
                ops=ops,
                compressed=bool(data.get("compressed", False)),
                tab_handle=str(data.get("tab_handle", "")),
                message_id=msg_id,
                timestamp=timestamp,
                protocol_version=protocol_ver,
            )
        elif msg_type == MSG_RESYNC_REQUEST:
            return ResyncRequestMessage(
                type=MSG_RESYNC_REQUEST,
                worker_id=worker_id,
                reason=str(data.get("reason", "")),
                message_id=msg_id,
                timestamp=timestamp,
                protocol_version=protocol_ver,
            )
        elif msg_type == MSG_ERROR:
            return ErrorMessage(
                type=MSG_ERROR,
                code=str(data.get("code", "")),
                detail=str(data.get("detail", "")),
                worker_id=data.get("worker_id"),
                message_id=msg_id,
                timestamp=timestamp,
                protocol_version=protocol_ver,
            )
        elif msg_type == MSG_PING:
            return PingMessage(
                type=MSG_PING,
                payload=data.get("payload"),
                message_id=msg_id,
                timestamp=timestamp,
                protocol_version=protocol_ver,
            )
        elif msg_type == MSG_PONG:
            return PongMessage(
                type=MSG_PONG,
                payload=data.get("payload"),
                message_id=msg_id,
                timestamp=timestamp,
                protocol_version=protocol_ver,
            )
        elif msg_type == MSG_THROTTLE_CONFIG:
            return ThrottleConfigMessage(
                type=MSG_THROTTLE_CONFIG,
                worker_id=data.get("worker_id", ""),
                profile_name=data.get("profile_name", "balanced"),
                compression_level=data.get("compression_level", 3),
                compression_threshold=data.get("compression_threshold", 1024),
                max_snapshot_bytes=data.get("max_snapshot_bytes", 0),
                min_snapshot_interval_ms=data.get("min_snapshot_interval_ms", 500),
                message_id=msg_id,
                timestamp=timestamp,
                protocol_version=protocol_ver,
            )
        elif msg_type == MSG_BROWSER_CONFIG:
            return BrowserConfigMessage(
                type=MSG_BROWSER_CONFIG,
                worker_id=worker_id,
                headless=data.get("headless", True),
                proxy_url=data.get("proxy_url"),
                message_id=msg_id,
                timestamp=timestamp,
                protocol_version=protocol_ver,
            )

        elif msg_type == MSG_TAB_OPENED:
            return TabOpenedMessage(
                type=MSG_TAB_OPENED,
                worker_id=worker_id,
                tab_handle=str(data.get("tab_handle", "")),
                tab_title=str(data.get("tab_title", "")),
                message_id=msg_id,
                timestamp=timestamp,
                protocol_version=protocol_ver,
            )
        elif msg_type == MSG_TAB_CLOSED:
            return TabClosedMessage(
                type=MSG_TAB_CLOSED,
                worker_id=worker_id,
                tab_handle=str(data.get("tab_handle", "")),
                message_id=msg_id,
                timestamp=timestamp,
                protocol_version=protocol_ver,
            )
        else:
            raise UnknownMessageTypeError(msg_type)
    except (TypeError, ValueError) as exc:
        raise MessageValidationError(f"Field validation failed for {msg_type}: {exc}") from exc


class MessageDeduplicator:
    """
    In-memory bounded LRU set for tracking message_ids to ensure idempotency.
    Prevents repeated processing of duplicate message IDs.
    """
    def __init__(self, max_size: int = 10000) -> None:
        self.max_size = max_size
        self._seen: OrderedDict[str, None] = OrderedDict()

    def is_duplicate(self, message_id: str) -> bool:
        """Check if message_id has already been recorded."""
        return message_id in self._seen

    def record(self, message_id: str) -> bool:
        """
        Record a message_id. Returns True if this was a new message, or False if duplicate.
        """
        if message_id in self._seen:
            return False
        if len(self._seen) >= self.max_size:
            self._seen.popitem(last=False)
        self._seen[message_id] = None
        return True

    def clear(self) -> None:
        """Clear the deduplicator store."""
        self._seen.clear()


def create_tab_opened(worker_id: str, tab_handle: str, tab_title: str = "") -> TabOpenedMessage:
    return TabOpenedMessage(
        type=MSG_TAB_OPENED,
        worker_id=worker_id,
        tab_handle=tab_handle,
        tab_title=tab_title,
        message_id=generate_message_id(),
        timestamp=get_current_utc_iso(),
        protocol_version=PROTOCOL_VERSION,
    )

def create_tab_closed(worker_id: str, tab_handle: str) -> TabClosedMessage:
    return TabClosedMessage(
        type=MSG_TAB_CLOSED,
        worker_id=worker_id,
        tab_handle=tab_handle,
        message_id=generate_message_id(),
        timestamp=get_current_utc_iso(),
        protocol_version=PROTOCOL_VERSION,
    )

def create_alert_opened(worker_id: str, alert_text: str) -> AlertOpenedMessage:
    return AlertOpenedMessage(
        type=MSG_ALERT_OPENED,
        worker_id=worker_id,
        alert_text=alert_text,
        message_id=generate_message_id(),
        timestamp=get_current_utc_iso(),
        protocol_version=PROTOCOL_VERSION,
    )
