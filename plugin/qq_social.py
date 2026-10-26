"""NapCat 4.18.37 social tools through an injected, internally authenticated bridge.

Capabilities describe the installed API contract, not universal QQ support or a
live probe. No cookies, HTTP client, shared preferences, or runtime imports live
here. Mutations are permission-gated and durably claimed before bridge calls.
"""

import asyncio
import hashlib
import json
from pathlib import Path
import re
import sqlite3


# This mapping comes from the installed send_qzone_msg registration.
_VISIBILITY = {"public": 1, "friends": 4, "selected": 16, "private": 64, "excluded": 128}
_WRITE_NAMES = frozenset({"qq_set_signature", "qq_publish_post", "qq_set_profile"})
_PROFILE_FIELDS = ("nickname", "long_nick", "sex", "age", "level", "country", "province", "city", "personal_note")


def schema():
    """Return neutral function schemas; every mutation requires a stable operation ID."""
    operation = {"type": "string", "minLength": 1, "description": "Stable caller operation ID; reuse for the same intent, never retry unknown outcomes with a new ID."}
    definitions = [
        ("qq_account_info", "Read this account's login identity and filtered descriptive profile using installed NapCat APIs; no credentials returned.", {}, []),
        ("qq_set_signature", "Set the account signature using installed NapCat set_self_longnick. An explicit empty string clears it. Acceptance and readback verification are reported separately.", {"signature": {"type": "string"}, "operation_id": operation}, ["signature", "operation_id"]),
        ("qq_publish_post", "Submit a Qzone post through installed NapCat send_qzone_msg with explicit visibility. A returned tid proves acceptance only, not published readback. Native authentication is internal.", {
            "content": {"type": "string"},
            "visibility": {"type": "string", "enum": list(_VISIBILITY)},
            "target_uins": {"type": "array", "items": {"anyOf": [{"type": "integer", "minimum": 1}, {"type": "string", "pattern": "^[0-9]+$"}]}, "description": "Required nonempty for selected/excluded visibility; otherwise omit."},
            "operation_id": operation,
        }, ["content", "visibility", "operation_id"]),
        ("qq_set_profile", "Set the installed NapCat account profile: nickname and optional personal_note/sex only. Acceptance does not imply readback verification.", {
            "nickname": {"type": "string"}, "personal_note": {"type": "string"},
            "sex": {"type": "string", "enum": ["0", "1", "2"]}, "operation_id": operation,
        }, ["nickname", "operation_id"]),
        ("qq_social_status", "Read static installed NapCat social capabilities and caller write-policy eligibility. This does not probe runtime availability or prove activity.", {}, []),
    ]
    return [{"type": "function", "function": {"name": name, "description": description, "parameters": {"type": "object", "properties": properties, "required": required, "additionalProperties": False}}} for name, description, properties, required in definitions]


def _error(code, *, outcome=None):
    """Return fixed diagnostics without leaking native exceptions, content, or secrets."""
    result = {"ok": False, "error": code}
    if outcome is not None:
        result["outcome"] = outcome
    return result


def _identifier(value):
    """Validate numeric identifiers without silently accepting booleans or objects."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError("invalid identifier")
    text = str(value)
    if not re.fullmatch(r"[0-9]+", text) or not text.strip("0"):
        raise ValueError("invalid identifier")
    return text


def _validate(name, args):
    """Validate the full object before any bridge call and preserve raw string values."""
    definitions = {item["function"]["name"]: item["function"]["parameters"] for item in schema()}
    if name not in definitions or not isinstance(args, dict):
        raise ValueError("invalid tool or arguments")
    spec = definitions[name]
    if set(args) - set(spec["properties"]) or not set(spec["required"]).issubset(args):
        raise ValueError("missing or unexpected arguments")
    if name in _WRITE_NAMES:
        if not isinstance(args["operation_id"], str) or not args["operation_id"].strip():
            raise ValueError("operation ID required")
    for key in ("signature", "content", "nickname", "personal_note"):
        if key in args and not isinstance(args[key], str):
            raise ValueError("raw string required")
    if name == "qq_set_signature":
        return "set_self_longnick", {"longNick": args["signature"]}
    if name == "qq_set_profile":
        if "sex" in args and (not isinstance(args["sex"], str) or args["sex"] not in ("0", "1", "2")):
            raise ValueError("sex must be a supported string")
        return "set_qq_profile", {key: args[key] for key in ("nickname", "personal_note", "sex") if key in args}
    if name == "qq_publish_post":
        visibility = args["visibility"]
        if not isinstance(visibility, str) or visibility not in _VISIBILITY:
            raise ValueError("explicit visibility required")
        payload = {"content": args["content"], "ugc_right": _VISIBILITY[visibility]}
        if visibility in ("selected", "excluded"):
            if not isinstance(args.get("target_uins"), list) or not args["target_uins"]:
                raise ValueError("targets required for selected/excluded")
            # Validate without coercing the bridge's allowed number/string ID union.
            for value in args["target_uins"]:
                _identifier(value)
            payload["target_uins"] = list(args["target_uins"])
        elif "target_uins" in args:
            raise ValueError("targets are only valid for selected/excluded")
        return "send_qzone_msg", payload
    return None, None


def _response(response, *, allow_null=False):
    """Classify complete OneBot responses; missing/contradictory envelopes are unknown."""
    if not isinstance(response, dict) or not {"status", "retcode", "data"}.issubset(response):
        return "unknown", None
    status, code, data = response["status"], response["retcode"], response["data"]
    if type(code) is not int:
        return "unknown", None
    if status == "failed" and code != 0:
        return "failed", None
    if status != "ok" or code != 0 or (not isinstance(data, dict) and not (allow_null and data is None)):
        return "unknown", None
    return "accepted", data


class Journal:
    """SQLite at-most-once claims across processes; never persist request text or native data."""

    def __init__(self, path):
        self.path = Path(path)

    def _connect(self):
        """Create only the dedicated mutation journal, with durable transaction semantics."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("CREATE TABLE IF NOT EXISTS social_operations (operation_id TEXT PRIMARY KEY, payload_hash TEXT NOT NULL, state TEXT NOT NULL, receipt TEXT)")
        except Exception:
            connection.close()
            raise
        return connection

    def claim(self, operation_id, payload_hash):
        """Atomically insert pending or return a prior receipt; pending never gets retried."""
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT payload_hash, state, receipt FROM social_operations WHERE operation_id=?", (operation_id,)).fetchone()
            if row is not None:
                connection.commit()
                if row[0] != payload_hash:
                    return _error("operation_conflict")
                if row[1] == "pending":
                    return _error("operation_pending_or_unknown", outcome="unknown")
                # Only locally generated, sanitized receipt objects are ever saved.
                receipt = json.loads(row[2])
                if not isinstance(receipt, dict):
                    raise ValueError("invalid journal receipt")
                return receipt
            connection.execute("INSERT INTO social_operations VALUES (?, ?, 'pending', NULL)", (operation_id, payload_hash))
            connection.commit()
            return None
        finally:
            connection.close()

    def finish(self, operation_id, payload_hash, receipt):
        """Finalize exactly one claim; interrupted persistence leaves a fail-closed pending row."""
        connection = self._connect()
        try:
            with connection:
                cursor = connection.execute("UPDATE social_operations SET state=?, receipt=? WHERE operation_id=? AND payload_hash=? AND state='pending'", (receipt.get("outcome", "unknown"), json.dumps(receipt, ensure_ascii=False), operation_id, payload_hash))
                if cursor.rowcount != 1:
                    raise ValueError("journal claim missing")
        finally:
            connection.close()


async def _account_info(call_action):
    """Read own identity then allowlist descriptive profile fields, excluding credentials."""
    state, login = _response(await call_action("get_login_info", {}))
    if state != "accepted":
        return _error("account_read_failed", outcome=state)
    user_id = _identifier(login.get("user_id"))
    state, profile = _response(await call_action("get_stranger_info", {"user_id": user_id}))
    if state != "accepted":
        return _error("profile_read_failed", outcome=state)
    result = {"user_id": user_id}
    if isinstance(login.get("nickname"), str):
        result["nickname"] = login["nickname"]
    for key in _PROFILE_FIELDS:
        value = profile.get(key)
        if isinstance(value, (str, int)) and not isinstance(value, bool):
            result[key] = value
    return {"ok": True, "account": result}


async def _signature_verification(signature, call_action):
    """Read native long_nick after acceptance without retrying the mutation on read failure."""
    try:
        state, login = _response(await call_action("get_login_info", {}))
        if state != "accepted":
            return {"state": "unknown", "matched": None}
        user_id = _identifier(login.get("user_id"))
        state, profile = _response(await call_action("get_stranger_info", {"user_id": user_id, "no_cache": True}))
        if state != "accepted" or not isinstance(profile.get("long_nick"), str):
            return {"state": "unknown", "matched": None}
        return {"state": "matched" if profile["long_nick"] == signature else "mismatch", "matched": profile["long_nick"] == signature}
    except (Exception, asyncio.CancelledError):
        # Readback can fail after a successful write; never turn that into a retry.
        return {"state": "unknown", "matched": None}


async def dispatch(name, args, call_action, *, can_write: bool, audit_path: Path | None = None) -> dict:
    """Dispatch validated tools with trusted parent permission and durable write deduplication.

    The bridge signature is ``await call_action(action_name, params_dict)``.
    Exceptions/native messages are never reflected. An unknown write outcome is
    terminal for its operation ID, including after a process restart.
    """
    try:
        action, payload = _validate(name, args)
    except (ValueError, TypeError):
        return _error("invalid_arguments")
    if name == "qq_social_status":
        return {"ok": True, "backend": "napcat", "runtime_verified": False, "write_enabled": can_write is True, "capabilities": {tool["function"]["name"]: {"declared_supported": True, "write_enabled": can_write is True if tool["function"]["name"] in _WRITE_NAMES else False} for tool in schema()}, "source": "installed NapCat 4.18.37 registration; static declaration, not a live probe"}
    if name == "qq_account_info":
        try:
            return await _account_info(call_action)
        except Exception:
            return _error("account_read_failed", outcome="unknown")
    # Only a real boolean True from the trusted parent authorizes writes.
    if can_write is not True:
        return _error("permission_denied")
    operation_id = args["operation_id"]
    # Escaped canonical JSON also handles unusual Unicode without altering bridge text.
    digest = hashlib.sha256(json.dumps({"action": action, "payload": payload}, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    # The parent owns the runtime root; never guess a repository or production path.
    if audit_path is None:
        return _error("journal_unavailable")
    journal = Journal(audit_path)
    try:
        prior = journal.claim(operation_id, digest)
    except Exception:
        return _error("journal_unavailable")
    if prior is not None:
        return prior
    try:
        response = await call_action(action, payload)
        state, data = _response(response, allow_null=name != "qq_publish_post")
        if state != "accepted":
            receipt = _error("native_rejected" if state == "failed" else "native_response_unknown", outcome=state)
        elif name == "qq_publish_post":
            tid = data.get("tid")
            if not isinstance(tid, str) or not tid.strip():
                receipt = _error("post_receipt_unknown", outcome="unknown")
            else:
                receipt = {"ok": True, "outcome": "accepted", "tid": tid, "verification": {"state": "unavailable", "published": None}}
        else:
            verification = await _signature_verification(args["signature"], call_action) if name == "qq_set_signature" else {"state": "unavailable", "matched": None}
            receipt = {"ok": True, "outcome": "accepted", "verification": verification}
    except (Exception, asyncio.CancelledError):
        # Timeout, cancellation, disconnect and other bridge failures can follow a commit.
        receipt = _error("write_outcome_unknown", outcome="unknown")
    try:
        journal.finish(operation_id, digest, receipt)
    except Exception:
        # A write may have completed; persistence failure must never invite a retry.
        return _error("journal_finish_unknown", outcome="unknown")
    return receipt
