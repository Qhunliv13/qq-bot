"""Legacy QQ actions through an injected native OneBot action bridge only.

No VM HTTP fallback, session credentials, automatic mutation retry, or new model
schemas live here. A native upload acknowledgement is distinct from message-ID
proof: upload actions legitimately return null data on success.
"""
import asyncio
from pathlib import Path, PurePosixPath
import subprocess
from uuid import uuid4


def _identifier(value):
    """Accept positive decimal identifiers while rejecting booleans and coercion."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("invalid_identifier")
    text = str(value)
    if not text.isascii() or not text.isdecimal() or int(text) <= 0:
        raise ValueError("invalid_identifier")
    return int(text)


def _status(response):
    """Classify full action envelopes; root health and missing envelopes are unknown."""
    if not isinstance(response, dict) or not {"status", "retcode", "data"}.issubset(response):
        return "unknown"
    # The root endpoint's health greeting is not proof of any requested action.
    if "is running" in str(response.get("message") or "").lower():
        return "unknown"
    code = response["retcode"]
    if type(code) is not int:
        return "unknown"
    if response["status"] == "failed" and code != 0:
        return "failed"
    if response["status"] == "ok" and code == 0:
        return "confirmed"
    return "unknown"


async def recall(group_id, count=1, all_messages=False, call_action=None):
    """Recall only the logged-in account's recent group messages, never retrying.

    The legacy VM contract supports count, not an all-messages mode. Preserve its
    history window and reverse selection, but never hardcode the bot's identity.
    """
    try:
        group_id = _identifier(group_id)
        count = _identifier(count)
        if all_messages is not False or not callable(call_action):
            raise ValueError("unsupported_recall_arguments")
    except (TypeError, ValueError):
        return {"ok": False, "error": "invalid_recall_arguments", "recall_count": 0}
    try:
        login = await call_action("get_login_info", {})
        if _status(login) != "confirmed" or not isinstance(login["data"], dict):
            return {"ok": False, "error": "login_identity_unconfirmed", "recall_count": 0}
        self_id = _identifier(login["data"].get("user_id"))
        history = await call_action("get_group_msg_history", {"group_id": group_id, "count": max(20, count * 5)})
        if _status(history) != "confirmed" or not isinstance(history["data"], dict):
            return {"ok": False, "error": "history_unconfirmed", "recall_count": 0}
        messages = history["data"].get("messages")
        if not isinstance(messages, list):
            return {"ok": False, "error": "history_invalid", "recall_count": 0}
        mine = [message for message in messages if isinstance(message, dict)
                and isinstance(message.get("sender"), dict)
                and str(message["sender"].get("user_id")) == str(self_id)]
        selected = [_identifier(message.get("message_id")) for message in list(reversed(mine))[:count]]
    except Exception:
        # Read failures have no mutation outcome and must not leak transport details.
        return {"ok": False, "error": "recall_lookup_failed", "recall_count": 0}
    if not selected:
        return {"ok": False, "error": "own_message_not_found", "recall_count": 0, "requested": count}
    results = []
    for message_id in selected:
        try:
            status = _status(await call_action("delete_msg", {"message_id": message_id}))
        except Exception:
            # A timeout can follow acceptance: record unknown and never resend it.
            status = "unknown"
        results.append({"message_id": message_id, "ok": status == "confirmed", "status": status})
    confirmed = sum(result["ok"] for result in results)
    return {"ok": confirmed == len(results), "recall_count": confirmed, "requested": count,
            "group": str(group_id), "results": results}


def media_kind(path):
    """Classify the captured VM's exact extension sets for native plugin handoff."""
    extension = Path(path).suffix.lower()
    if extension in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}:
        return "image"
    if extension in {".mp4", ".mov", ".mkv", ".avi", ".webm", ".flv"}:
        return "video"
    if extension in {".mp3", ".wav", ".amr", ".silk", ".flac", ".ogg"}:
        return "record"
    return "file"


async def prepare_container_file(path, container="napcat"):
    """Stage a validated local file in the captured production NapCat namespace.

    The captured VM implementation uses container napcat. UUID staging names do
    not overwrite concurrent uploads. Never fall back to an inaccessible host
    path after copy failure or delete a staged file during an unknown outcome.
    """
    local = Path(path)
    if not local.is_absolute() or not local.is_file() or container != "napcat":
        raise ValueError("invalid_staging_arguments")
    inner = "/tmp/higan-upload-" + uuid4().hex
    completed = await asyncio.to_thread(subprocess.run,
                                        ["docker", "cp", str(local), container + ":" + inner],
                                        capture_output=True, timeout=120, check=False)
    if completed.returncode != 0:
        # Never include stdout/stderr: container output can contain private paths.
        raise RuntimeError("file_staging_failed")
    return inner


async def upload(kind, path, user_id=0, group_id=0, call_action=None, name="", prepare_file=None):
    """Upload an existing local file with explicit destination and native receipt.

    The caller owns destination-policy checks. Native upload acceptance does not
    manufacture a send-message ID or assert message-ID-based delivery proof.
    Production callers inject prepare_container_file when NapCat runs in Docker;
    absent preparation is reserved for bridges sharing the host file namespace.
    """
    try:
        if kind not in ("group", "private") or not isinstance(path, str) or not callable(call_action):
            raise ValueError("invalid_upload_arguments")
        local = Path(path)
        if not local.is_absolute() or not local.is_file():
            raise ValueError("local_file_required")
        if local.stat().st_size > 200 * 1024 * 1024:
            raise ValueError("file_too_large")
        key = "user_id" if kind == "private" else "group_id"
        destination = _identifier(user_id if kind == "private" else group_id)
        if not isinstance(name, str) or (prepare_file is not None and not callable(prepare_file)):
            raise ValueError("invalid_filename_or_preparer")
        action = "upload_private_file" if kind == "private" else "upload_group_file"
        parameters = {key: destination, "file": str(local), "name": name or local.name}
    except (TypeError, ValueError, OSError):
        return {"ok": False, "accepted": False, "delivered": False, "error": "invalid_upload_arguments"}
    if prepare_file is not None:
        try:
            staged = await prepare_file(str(local))
            if not isinstance(staged, str) or not PurePosixPath(staged).is_absolute() or "\x00" in staged:
                raise ValueError("invalid_staged_path")
            parameters["file"] = staged
        except Exception:
            # No native upload was submitted; staging failure cannot be acceptance.
            return {"ok": False, "accepted": False, "delivered": False, "error": "file_staging_failed"}
    try:
        status = _status(await call_action(action, parameters))
    except Exception:
        status = "unknown"
    accepted = status == "confirmed"
    return {"ok": accepted, "accepted": accepted, "delivered": False,
            "delivery_confirmation": "native_upload_acknowledged" if accepted else status,
            "action": action, "name": parameters["name"], "status": status}
