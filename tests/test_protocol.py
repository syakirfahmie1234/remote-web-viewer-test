"""
Unit tests for shared/protocol.py, shared/models.py, and shared/messages.py.
Verifies message type models, universal envelope fields, worker_id requirement,
protocol_version enforcement, command allowlist validation, and serialization round-trips.
"""

import json
import unittest

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
    ROLE_WORKER,
    ROLE_CONTROLLER,
    STATUS_CONNECTED,
    STATUS_DISCONNECTED,
    STATUS_CRASHED,
    COMMAND_ALLOWLIST,
    FORBIDDEN_COMMANDS,
    OP_ADD,
    OP_REMOVE,
    OP_REPLACE,
    OP_TEXT,
    OP_ATTRIBUTE,
    OP_VALUE,
)
from shared.models import (
    DOMDiffOp,
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
)
from shared.messages import (
    ProtocolVersionMismatchError,
    UnknownMessageTypeError,
    MissingWorkerIdError,
    InvalidCommandError,
    MessageValidationError,
    MessageDeduplicator,
    create_hello,
    create_auth,
    create_worker_register,
    create_controller_register,
    create_worker_status,
    create_command,
    create_command_result,
    create_full_snapshot,
    create_dom_update,
    create_resync_request,
    create_error,
    create_ping,
    create_pong,
    serialize_message,
    parse_message,
    message_to_dict,
)


class TestProtocolAndModels(unittest.TestCase):
    """Test suite verifying shared protocol, models, and message helpers."""

    def test_universal_envelope_fields_present_on_all_messages(self):
        """Every message must contain type, message_id, timestamp, and protocol_version=1."""
        messages = [
            create_hello(ROLE_WORKER),
            create_auth("token-secret-123"),
            create_worker_register("worker-01", {"browser": "chrome"}),
            create_controller_register(client_id="ctrl-01"),
            create_worker_status("worker-01", STATUS_CONNECTED, dom_version=10),
            create_command("worker-01", "click", {"selector": "#search"}),
            create_command_result("worker-01", "click", success=True),
            create_full_snapshot("worker-01", 1, "https://site.com", "Site", "<html></html>"),
            create_dom_update("worker-01", 1, 2, [DOMDiffOp(op=OP_TEXT, selector="#val", text="42")]),
            create_resync_request("worker-01", "version mismatch"),
            create_error("ERR_UNKNOWN", "Something went wrong", worker_id="worker-01"),
            create_ping("ping-payload"),
            create_pong("pong-payload"),
        ]

        for msg in messages:
            with self.subTest(msg_type=msg.type):
                self.assertIsInstance(msg.type, str)
                self.assertTrue(len(msg.type) > 0)
                self.assertIsInstance(msg.message_id, str)
                self.assertTrue(len(msg.message_id) > 0)
                self.assertIsInstance(msg.timestamp, str)
                self.assertTrue(len(msg.timestamp) > 0)
                self.assertEqual(msg.protocol_version, PROTOCOL_VERSION)
                self.assertEqual(msg.protocol_version, 1)

    def test_worker_id_strictly_enforced_on_all_worker_scoped_messages(self):
        """It must be impossible to construct or parse any Worker-scoped message without worker_id."""
        # Constructors must raise MissingWorkerIdError on empty / whitespace / None worker_id
        with self.assertRaises(MissingWorkerIdError):
            create_worker_register("")

        with self.assertRaises(MissingWorkerIdError):
            create_worker_register("   ")

        with self.assertRaises(MissingWorkerIdError):
            create_worker_status("", STATUS_CONNECTED)

        with self.assertRaises(MissingWorkerIdError):
            create_command("", "click", {"selector": "#btn"})

        with self.assertRaises(MissingWorkerIdError):
            create_command_result("", "click", success=True)

        with self.assertRaises(MissingWorkerIdError):
            create_full_snapshot("", 1, "https://site.com", "Site", "<html></html>")

        with self.assertRaises(MissingWorkerIdError):
            create_dom_update("", 1, 2, [])

        with self.assertRaises(MissingWorkerIdError):
            create_resync_request("")

    def test_parse_message_rejects_worker_scoped_messages_missing_worker_id(self):
        """Incoming payloads for worker-scoped message types must fail parsing if worker_id is missing."""
        invalid_payloads = [
            {"type": MSG_WORKER_REGISTER, "message_id": "1", "timestamp": "now", "protocol_version": 1},
            {"type": MSG_WORKER_STATUS, "status": "connected", "message_id": "2", "timestamp": "now", "protocol_version": 1},
            {"type": MSG_COMMAND, "command": "click", "payload": {}, "message_id": "3", "timestamp": "now", "protocol_version": 1},
            {"type": MSG_COMMAND_RESULT, "command": "click", "success": True, "message_id": "4", "timestamp": "now", "protocol_version": 1},
            {"type": MSG_FULL_SNAPSHOT, "version": 1, "url": "u", "title": "t", "html": "h", "message_id": "5", "timestamp": "now", "protocol_version": 1},
            {"type": MSG_DOM_UPDATE, "base_version": 1, "version": 2, "ops": [], "message_id": "6", "timestamp": "now", "protocol_version": 1},
            {"type": MSG_RESYNC_REQUEST, "message_id": "7", "timestamp": "now", "protocol_version": 1},
        ]

        for payload in invalid_payloads:
            with self.subTest(msg_type=payload["type"]):
                with self.assertRaises(MissingWorkerIdError):
                    parse_message(payload)

    def test_protocol_version_mismatch_rejected(self):
        """A receiver that gets a message with an unrecognized protocol_version must reject it."""
        bad_versions = [0, 2, 99, -1]
        for ver in bad_versions:
            raw = {
                "type": MSG_HELLO,
                "role": ROLE_WORKER,
                "message_id": "abc-123",
                "timestamp": "2026-08-28T09:00:00Z",
                "protocol_version": ver,
            }
            with self.subTest(version=ver):
                with self.assertRaises(ProtocolVersionMismatchError) as ctx:
                    parse_message(raw)
                self.assertEqual(ctx.exception.expected, 1)
                self.assertEqual(ctx.exception.received, ver)

    def test_unknown_message_type_rejected(self):
        """Messages with an unknown type must raise UnknownMessageTypeError."""
        raw = {
            "type": "some_random_unsupported_type",
            "message_id": "abc-123",
            "timestamp": "2026-08-28T09:00:00Z",
            "protocol_version": 1,
        }
        with self.assertRaises(UnknownMessageTypeError):
            parse_message(raw)

    def test_command_allowlist_enforcement(self):
        """Only allowlisted commands are accepted; forbidden or arbitrary commands are rejected."""
        worker_id = "worker-01"

        # All allowlisted commands must succeed
        for cmd in COMMAND_ALLOWLIST:
            with self.subTest(command=cmd):
                msg = create_command(worker_id=worker_id, command=cmd, payload={"selector": "#el"})
                self.assertEqual(msg.command, cmd)
                # Ensure parsing also succeeds
                parsed = parse_message(serialize_message(msg))
                self.assertIsInstance(parsed, CommandMessage)
                self.assertEqual(parsed.command, cmd)

        # Forbidden commands must raise InvalidCommandError
        for forbidden in FORBIDDEN_COMMANDS:
            with self.subTest(forbidden=forbidden):
                with self.assertRaises(InvalidCommandError):
                    create_command(worker_id=worker_id, command=forbidden)

                raw = {
                    "type": MSG_COMMAND,
                    "worker_id": worker_id,
                    "command": forbidden,
                    "payload": {},
                    "message_id": "cmd-test-1",
                    "timestamp": "2026-08-28T09:00:00Z",
                    "protocol_version": 1,
                }
                with self.assertRaises(InvalidCommandError):
                    parse_message(raw)

    def test_dom_diff_operations(self):
        """Test DOMDiffOp creation, validation, and conversion."""
        ops = [
            DOMDiffOp(op=OP_ADD, selector="#list", position="beforeend", html="<li>Item 3</li>"),
            DOMDiffOp(op=OP_REMOVE, selector="#old-banner"),
            DOMDiffOp(op=OP_REPLACE, selector="#card-1", html="<div id='card-1'>New</div>"),
            DOMDiffOp(op=OP_TEXT, selector="#header-title", text="Updated Title"),
            DOMDiffOp(op=OP_ATTRIBUTE, selector="#submit-btn", attr="disabled", value="true"),
            DOMDiffOp(op=OP_VALUE, selector="#search-input", value="John Doe"),
        ]

        # Invalid diff op
        with self.assertRaises(ValueError):
            DOMDiffOp(op="invalid_op", selector="#main")

        # Empty selector
        with self.assertRaises(ValueError):
            DOMDiffOp(op=OP_TEXT, selector="")

        # Round trip via DomUpdateMessage
        dom_msg = create_dom_update(
            worker_id="worker-01",
            base_version=10,
            version=11,
            ops=ops,
            compressed=False,
        )
        serialized = serialize_message(dom_msg)
        parsed = parse_message(serialized)

        self.assertIsInstance(parsed, DomUpdateMessage)
        self.assertEqual(parsed.worker_id, "worker-01")
        self.assertEqual(parsed.base_version, 10)
        self.assertEqual(parsed.version, 11)
        self.assertEqual(len(parsed.ops), len(ops))
        for original_op, parsed_op in zip(ops, parsed.ops):
            self.assertEqual(original_op.op, parsed_op.op)
            self.assertEqual(original_op.selector, parsed_op.selector)
            self.assertEqual(original_op.html, parsed_op.html)
            self.assertEqual(original_op.text, parsed_op.text)
            self.assertEqual(original_op.attr, parsed_op.attr)
            self.assertEqual(original_op.value, parsed_op.value)

    def test_serialization_round_trip_all_message_types(self):
        """Ensure all 13 message types serialize to JSON and parse back identically."""
        sample_messages = [
            create_hello(ROLE_CONTROLLER),
            create_auth("super-secure-token-xyz"),
            create_worker_register("worker-alpha", {"os": "windows", "resolution": "1920x1080"}),
            create_controller_register(client_id="controller-99", subscribed_worker_id="worker-alpha"),
            create_worker_status("worker-alpha", STATUS_CRASHED, dom_version=105),
            create_command("worker-alpha", "type", {"selector": "#input-search", "text": "Medical Report"}),
            create_command_result("worker-alpha", "type", success=True, payload={"typed": True}),
            create_full_snapshot("worker-alpha", 100, "https://target.local/patients", "Patients", "<div>Content</div>", True),
            create_dom_update("worker-alpha", 100, 101, [DOMDiffOp(op=OP_REPLACE, selector="#sub", html="<span>A</span>")]),
            create_resync_request("worker-alpha", "stale after reconnect"),
            create_error("AUTH_FAILED", "Invalid token provided", worker_id="worker-alpha"),
            create_ping("heartbeat"),
            create_pong("heartbeat-ack"),
        ]

        for original in sample_messages:
            with self.subTest(msg_type=original.type):
                raw_json = serialize_message(original)
                self.assertIsInstance(raw_json, str)
                parsed = parse_message(raw_json)

                self.assertEqual(type(original), type(parsed))
                self.assertEqual(original.type, parsed.type)
                self.assertEqual(original.message_id, parsed.message_id)
                self.assertEqual(original.timestamp, parsed.timestamp)
                self.assertEqual(original.protocol_version, parsed.protocol_version)

                if isinstance(original, WorkerScopedMessage):
                    self.assertIsInstance(parsed, WorkerScopedMessage)
                    self.assertEqual(original.worker_id, parsed.worker_id)

    def test_message_deduplicator(self):
        """Verify that MessageDeduplicator tracks seen message_ids and enforces idempotency."""
        dedup = MessageDeduplicator(max_size=3)

        # First time seeing IDs
        self.assertTrue(dedup.record("id-1"))
        self.assertTrue(dedup.record("id-2"))
        self.assertTrue(dedup.record("id-3"))

        # Duplicate checks
        self.assertTrue(dedup.is_duplicate("id-1"))
        self.assertTrue(dedup.is_duplicate("id-2"))
        self.assertFalse(dedup.record("id-1"))  # Already seen

        # Exceed capacity, oldest should be evicted
        self.assertTrue(dedup.record("id-4"))
        self.assertFalse(dedup.is_duplicate("id-1"))  # Evicted
        self.assertTrue(dedup.is_duplicate("id-2"))
        self.assertTrue(dedup.is_duplicate("id-3"))
        self.assertTrue(dedup.is_duplicate("id-4"))


if __name__ == "__main__":
    unittest.main()
