"""
Protocol constants and definitions for Website-Specific Remote Selenium System.
Authoritative source for protocol_version, message types, command allowlists, and DOM ops.
"""

from typing import Final, FrozenSet

# Current protocol version - Must be kept strictly synchronized across all modules.
# Any version bump must be done in lockstep across server, worker, and controller.
PROTOCOL_VERSION: Final[int] = 1

# Message Type Constants
MSG_HELLO: Final[str] = "hello"
MSG_AUTH: Final[str] = "auth"
MSG_WORKER_REGISTER: Final[str] = "worker_register"
MSG_CONTROLLER_REGISTER: Final[str] = "controller_register"
MSG_WORKER_STATUS: Final[str] = "worker_status"
MSG_COMMAND: Final[str] = "command"
MSG_COMMAND_RESULT: Final[str] = "command_result"
MSG_FULL_SNAPSHOT: Final[str] = "full_snapshot"
MSG_DOM_UPDATE: Final[str] = "dom_update"
MSG_RESYNC_REQUEST: Final[str] = "resync_request"
MSG_ERROR: Final[str] = "error"
MSG_PING: Final[str] = "ping"
MSG_PONG: Final[str] = "pong"
MSG_THROTTLE_CONFIG: Final[str] = "throttle_config"
MSG_BROWSER_CONFIG: Final[str] = "browser_config"

# Set of all valid message types
ALL_MESSAGE_TYPES: Final[FrozenSet[str]] = frozenset({
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
})

# Worker-scoped message types where worker_id is strictly MANDATORY
WORKER_SCOPED_TYPES: Final[FrozenSet[str]] = frozenset({
    MSG_WORKER_REGISTER,
    MSG_WORKER_STATUS,
    MSG_COMMAND,
    MSG_COMMAND_RESULT,
    MSG_FULL_SNAPSHOT,
    MSG_DOM_UPDATE,
    MSG_RESYNC_REQUEST,
    MSG_THROTTLE_CONFIG,
    MSG_BROWSER_CONFIG,
})

# Connection-level message types where worker_id is not required
CONNECTION_SCOPED_TYPES: Final[FrozenSet[str]] = frozenset({
    MSG_HELLO,
    MSG_AUTH,
    MSG_CONTROLLER_REGISTER,
    MSG_PING,
    MSG_PONG,
})

# Valid client roles in HELLO message
ROLE_WORKER: Final[str] = "worker"
ROLE_CONTROLLER: Final[str] = "controller"
ALL_ROLES: Final[FrozenSet[str]] = frozenset({ROLE_WORKER, ROLE_CONTROLLER})

# Valid Worker status values
STATUS_CONNECTED: Final[str] = "connected"
STATUS_DISCONNECTED: Final[str] = "disconnected"
STATUS_CRASHED: Final[str] = "crashed"
STATUS_IDLE: Final[str] = "idle"
STATUS_BUSY: Final[str] = "busy"
ALL_WORKER_STATUSES: Final[FrozenSet[str]] = frozenset({
    STATUS_CONNECTED,
    STATUS_DISCONNECTED,
    STATUS_CRASHED,
    STATUS_IDLE,
    STATUS_BUSY,
})

# Command allowlist - strictly enforced at server routing and worker execution layers
CMD_NAVIGATE: Final[str] = "navigate"
CMD_CLICK: Final[str] = "click"
CMD_TYPE: Final[str] = "type"
CMD_CLEAR: Final[str] = "clear"
CMD_KEYPRESS: Final[str] = "keypress"
CMD_SCROLL: Final[str] = "scroll"
CMD_BACK: Final[str] = "back"
CMD_FORWARD: Final[str] = "forward"
CMD_REFRESH: Final[str] = "refresh"
CMD_SCREENSHOT: Final[str] = "screenshot"
CMD_HIGHLIGHT: Final[str] = "highlight"
CMD_PAGE_SOURCE: Final[str] = "page_source"

COMMAND_ALLOWLIST: Final[FrozenSet[str]] = frozenset({
    CMD_NAVIGATE,
    CMD_CLICK,
    CMD_TYPE,
    CMD_CLEAR,
    CMD_KEYPRESS,
    CMD_SCROLL,
    CMD_BACK,
    CMD_FORWARD,
    CMD_REFRESH,
    CMD_SCREENSHOT,
    CMD_HIGHLIGHT,
    CMD_PAGE_SOURCE,
})

# Forbidden commands - explicitly rejected to prevent arbitrary execution
FORBIDDEN_COMMANDS: Final[FrozenSet[str]] = frozenset({
    "execute_shell",
    "execute_python",
    "execute_arbitrary_javascript",
    "eval",
    "exec",
})

# DOM Diff Operations
OP_ADD: Final[str] = "add"
OP_REMOVE: Final[str] = "remove"
OP_REPLACE: Final[str] = "replace"
OP_TEXT: Final[str] = "text"
OP_ATTRIBUTE: Final[str] = "attribute"
OP_VALUE: Final[str] = "value"

ALL_DOM_DIFF_OPS: Final[FrozenSet[str]] = frozenset({
    OP_ADD,
    OP_REMOVE,
    OP_REPLACE,
    OP_TEXT,
    OP_ATTRIBUTE,
    OP_VALUE,
})
