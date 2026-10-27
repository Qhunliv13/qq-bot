"""Persist native OneBot requests without treating message text as authority.

The repository root license applies. Native flags are private adapter tokens: only
``resolve`` exposes one to the trusted action handler. A recorded event proves
provenance, not authorization to approve or reject a request.
"""

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3


# SQLite INTEGER is signed 64-bit; booleans are never native numeric identifiers.
_MAX_INTEGER = (1 << 63) - 1
_KINDS = frozenset({"friend", "group"})
_STATUSES = frozenset({"handled", "unknown"})
_PUBLIC_COLUMNS = (
    "request_id, kind, user_id, group_id, sub_type, comment, received_at, "
    "status, outcome, origin, account_id, platform"
)


def _positive_id(value):
    """Normalize positive native numeric IDs without accepting bools or floats."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    # Only ASCII decimal identifiers are accepted; no signs or whitespace.
    if isinstance(value, str) and (not value.isascii() or not value.isdecimal()):
        return None
    try:
        number = int(value)
    except ValueError:
        return None
    return number if 0 < number <= _MAX_INTEGER else None


def _check_kind(kind, allow_all=False):
    """Reject mistyped filters rather than silently broadening request scope."""
    allowed = _KINDS | {"all"} if allow_all else _KINDS
    if not isinstance(kind, str) or kind not in allowed:
        raise ValueError("invalid request kind")


def _record(row):
    """Return an independent plain record with a convenient public ID alias."""
    if row is None:
        return None
    result = dict(row)
    result["id"] = result["request_id"]
    result["self_id"] = result["account_id"]
    return result


class RequestStore:
    """Durably retain first-seen native metadata; never parse opaque flags.

    Each operation owns its connection, supporting independent plugin threads and
    process restarts. Public listings retain untrusted comments verbatim but omit
    native tokens. Callers must authorize mutations independently of event data.
    """

    def __init__(self, path):
        """Create a private database and idempotent schema at the chosen path."""
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Exclusive creation avoids a permissive SQLite creation window on POSIX.
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        if os.name == "posix":
            os.chmod(self.path, 0o600)
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS qq_requests ("
                "request_id TEXT PRIMARY KEY, kind TEXT NOT NULL, flag TEXT NOT NULL, "
                "user_id INTEGER NOT NULL, group_id INTEGER, sub_type TEXT, "
                "comment TEXT NOT NULL, received_at INTEGER, "
                "status TEXT NOT NULL DEFAULT 'pending', outcome TEXT, "
                "origin TEXT NOT NULL DEFAULT 'request_event', account_id INTEGER, platform TEXT, "
                "account_key TEXT NOT NULL, platform_key TEXT NOT NULL, "
                "UNIQUE(kind, flag, account_key, platform_key))"
            )

    def _connect(self):
        """Use bounded contention waits and full SQLite commit durability."""
        connection = sqlite3.connect(str(self.path), timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    def capture(self, event_data, *, platform=None):
        """Capture raw native metadata with optional trusted adapter-instance scope.

        ``platform`` is supplied by the event adapter, never message/tool content.
        Native self_id scopes tokens to their bot account when present; absence
        remains explicitly unknown. Missing native time is never fabricated.
        Duplicate kind/flag/account/platform records retain first-seen metadata.
        """
        if not isinstance(event_data, dict) or event_data.get("post_type") != "request":
            return None
        kind = event_data.get("request_type")
        if not isinstance(kind, str) or kind not in _KINDS:
            return None
        flag = event_data.get("flag")
        if not isinstance(flag, str) or not flag.strip():
            return None
        user_id = _positive_id(event_data.get("user_id"))
        if user_id is None:
            return None
        account_id = None
        if "self_id" in event_data:
            account_id = _positive_id(event_data["self_id"])
            if account_id is None:
                return None
        if platform is not None and (not isinstance(platform, str) or not platform.strip()):
            return None
        # Empty internal scope keys make SQLite uniqueness apply even to unknown scopes.
        account_key = str(account_id) if account_id is not None else ""
        platform_key = platform if platform is not None else ""
        group_id = None
        sub_type = None
        # Group scope and native subtype are mandatory; friend events ignore them.
        if kind == "group":
            group_id = _positive_id(event_data.get("group_id"))
            sub_type = event_data.get("sub_type")
            if group_id is None or sub_type not in ("add", "invite"):
                return None
        comment = event_data.get("comment", "")
        if not isinstance(comment, str):
            return None
        received_at = event_data.get("time")
        if received_at is not None and (
            type(received_at) is not int or not 0 <= received_at <= _MAX_INTEGER
        ):
            return None
        # JSON framing prevents delimiter-containing opaque tokens from aliasing scopes.
        identity = json.dumps([kind, flag, account_key, platform_key], ensure_ascii=True)
        request_id = "qqr_" + hashlib.sha256(identity.encode("ascii")).hexdigest()[:32]
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT OR IGNORE INTO qq_requests "
                "(request_id, kind, flag, user_id, group_id, sub_type, comment, received_at, "
                "account_id, platform, account_key, platform_key) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (request_id, kind, flag, user_id, group_id, sub_type, comment, received_at,
                 account_id, platform, account_key, platform_key),
            )
            row = connection.execute(
                f"SELECT {_PUBLIC_COLUMNS} FROM qq_requests "
                "WHERE kind=? AND flag=? AND account_key=? AND platform_key=?",
                (kind, flag, account_key, platform_key),
            ).fetchone()
            return _record(row)

    def list_requests(self, kind="all", include_handled=False, limit=50):
        """List bounded public snapshots; unknown outcomes stay out of pending lists."""
        _check_kind(kind, allow_all=True)
        if type(include_handled) is not bool:
            raise ValueError("include_handled must be boolean")
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be an integer from 1 to 1000")
        clauses = []
        parameters = []
        if kind != "all":
            clauses.append("kind=?")
            parameters.append(kind)
        if not include_handled:
            clauses.append("status='pending'")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        parameters.append(limit)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"SELECT {_PUBLIC_COLUMNS} FROM qq_requests{where} "
                "ORDER BY rowid DESC LIMIT ?", parameters,
            ).fetchall()
        return [_record(row) for row in rows]

    def resolve(self, request_id, kind=None):
        """Return a fresh internal token-bearing snapshot, not mutation authorization."""
        if kind is not None:
            _check_kind(kind)
        if not isinstance(request_id, str) or not request_id:
            return None
        with closing(self._connect()) as connection:
            row = connection.execute(
                f"SELECT {_PUBLIC_COLUMNS}, flag FROM qq_requests WHERE request_id=?",
                (request_id,),
            ).fetchone()
        result = _record(row)
        if result is not None and kind is not None and result["kind"] != kind:
            return None
        return result

    def mark(self, request_id, status, outcome=None):
        """Persist a handled/unknown result; the action journal guards remote at-most-once."""
        if not isinstance(status, str) or status not in _STATUSES:
            raise ValueError("status must be handled or unknown")
        if outcome is not None and not isinstance(outcome, str):
            raise ValueError("outcome must be a string or None")
        if not isinstance(request_id, str) or not request_id:
            return False
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                "UPDATE qq_requests SET status=?, outcome=? WHERE request_id=?",
                (status, outcome, request_id),
            )
            return cursor.rowcount == 1
