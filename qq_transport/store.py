"""Standalone durable at-most-once claims; no network or application imports.

A committed pending row is an unknown outcome, not permission to retry. Only a
caller that received claimed=True may execute the remote operation. Parameters
are persisted only as a SHA-256 digest; responses remain private local data.
"""

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import unicodedata


# Actions are protocol identifiers, not paths; digits support generated names.
_ACTION = re.compile(r"[A-Za-z_.][A-Za-z0-9_.]{0,255}\Z")


def canonical_json(value):
    """Return deterministic strict UTF-8 JSON, rejecting ambiguous object keys.

    Tuples follow stdlib JSON semantics and become arrays. Reject non-string
    keys before serialization because JSON's implicit key coercion aliases IDs.
    Cycles, excessive nesting, non-finite floats and unsupported types fail with
    ValueError rather than escaping as implementation-dependent exceptions.
    """
    try:
        stack = [value]
        visited = set()
        while stack:
            item = stack.pop()
            if isinstance(item, (dict, list, tuple)):
                # Shared containers need checking once; json.dumps detects cycles.
                if id(item) in visited:
                    continue
                visited.add(id(item))
                if isinstance(item, dict):
                    if any(not isinstance(key, str) for key in item):
                        raise ValueError("JSON object keys must be strings")
                    stack.extend(item.values())
                else:
                    stack.extend(item)
        encoded = json.dumps(
            value, ensure_ascii=False, allow_nan=False,
            sort_keys=True, separators=(",", ":"),
        )
        # Lone surrogates cannot be stored as strict UTF-8 SQLite text.
        encoded.encode("utf-8", errors="strict")
        # Ensure this interpreter can replay every value accepted for storage.
        json.loads(encoded)
        return encoded
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ValueError("Value must be strict UTF-8 JSON") from exc


def copy_json(value):
    """Detach a supported JSON value through the single canonical serializer."""
    return json.loads(canonical_json(value))


def validate_account(account):
    """Return a positive ASCII decimal account string without normalizing it."""
    if (not isinstance(account, str) or not account
            or any(char not in "0123456789" for char in account)
            or not any(char != "0" for char in account)):
        raise ValueError("Account must be a positive ASCII decimal string")
    return account


def validate_identifier(value):
    """Return an opaque <=256-character UTF-8 identifier without controls."""
    if (not isinstance(value, str) or not value
            or len(value) > 256
            or any(unicodedata.category(char) == "Cc" for char in value)):
        raise ValueError("Identifier must be nonempty, <=256, without controls")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise ValueError("Identifier must be strict UTF-8") from exc
    return value


def validate_action(action):
    """Return an explicit ASCII action identifier, including dot-prefixed actions."""
    if (not isinstance(action, str) or not _ACTION.fullmatch(action)
            or action in (".", "..")):
        raise ValueError("Action must be a valid ASCII action identifier")
    return action


def _now():
    """Use explicit UTC timestamps so durable rows have auditable wall times."""
    return datetime.now(timezone.utc).isoformat()


class OperationJournal:
    """Persist atomic claims in an explicitly selected, dedicated SQLite file.

    Each call owns and closes its connection, so a journal may be shared across
    threads. No pending row expires, and completion never overwrites authority.
    """

    def __init__(self, path):
        """Create the requested parent directory and idempotent journal schema."""
        raw_path = os.fspath(path)
        if not isinstance(raw_path, str) or not raw_path or raw_path == ":memory:":
            raise ValueError("Journal requires an explicit durable file path")
        self.path = str(Path(raw_path).resolve())
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS operations (
                    account TEXT NOT NULL,
                    operation_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('pending', 'complete')),
                    response TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(account, operation_id)
                )
            """)

    def _connect(self):
        """Open an isolated connection with bounded SQLite lock contention."""
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def claim(self, account, operation_id, action, parameters):
        """Atomically claim once, replay completion, or report a safe conflict.

        A pending replay is never retryable. Conflicting request bodies receive
        the original state but not its potentially sensitive stored response.
        """
        validate_account(account)
        validate_identifier(operation_id)
        validate_action(action)
        if not isinstance(parameters, dict):
            raise ValueError("Parameters must be a JSON object")
        fingerprint = hashlib.sha256(
            canonical_json(parameters).encode("utf-8")
        ).hexdigest()
        with closing(self._connect()) as connection, connection:
            # The write lock covers both observation and insertion across processes.
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT action, fingerprint, state, response FROM operations "
                "WHERE account=? AND operation_id=?", (account, operation_id),
            ).fetchone()
            if row is None:
                timestamp = _now()
                connection.execute(
                    "INSERT INTO operations VALUES (?, ?, ?, ?, 'pending', NULL, ?, ?)",
                    (account, operation_id, action, fingerprint, timestamp, timestamp),
                )
                return {"claimed": True, "conflict": False,
                        "state": "pending", "response": None}
            conflict = row["action"] != action or row["fingerprint"] != fingerprint
            response = None
            if not conflict and row["state"] == "complete":
                # Decode freshly for every replay; callers cannot mutate authority.
                response = json.loads(row["response"])
            return {"claimed": False, "conflict": conflict,
                    "state": row["state"], "response": response}

    def complete(self, account, operation_id, response):
        """Complete a pending claim once; reject missing or changed completions.

        Repeating the identical canonical response is idempotent. Never insert a
        missing claim or manufacture success when durable storage fails.
        """
        validate_account(account)
        validate_identifier(operation_id)
        if not isinstance(response, dict):
            raise ValueError("Response must be a JSON object")
        encoded = canonical_json(response)
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT state, response FROM operations "
                "WHERE account=? AND operation_id=?", (account, operation_id),
            ).fetchone()
            if row is None:
                raise ValueError("Operation has not been claimed")
            if row["state"] == "complete":
                if row["response"] != encoded:
                    raise ValueError("Completed operation response is immutable")
                return
            connection.execute(
                "UPDATE operations SET state='complete', response=?, updated_at=? "
                "WHERE account=? AND operation_id=? AND state='pending'",
                (encoded, _now(), account, operation_id),
            )
