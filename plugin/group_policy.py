"""Thread-safe, fail-closed group-policy persistence without plugin dependencies.

``poll`` clears mention-only/muted restrictions so polling can actually run, but
preserves reply probability. ``probability`` changes only the probability, as in
the existing tool. Only normal/mention_only/muted reset both optional overrides.
Locks coordinate instances in this process, not independent writer processes.
"""

import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading


# Resolve aliases before looking up a shared lock: replacement changes inodes.
_LOCKS = {}
_LOCKS_GUARD = threading.Lock()
_MODES = frozenset({"show", "normal", "mention_only", "muted", "probability", "poll"})


def _reject_constant(value):
    """Reject nonstandard JSON NaN/Infinity even inside preserved extra fields."""
    raise ValueError(f"invalid JSON numeric constant: {value}")


def _group_id(value):
    """Accept only positive decimal group identifiers, never private scopes."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("group must be a positive numeric group ID")
    text = str(value).strip()
    if not re.fullmatch(r"[0-9]+", text) or not text.strip("0"):
        raise ValueError("group must be a positive numeric group ID")
    return text.lstrip("0")


def _number(value, minimum, maximum, integer=False):
    """Normalize finite numeric overrides using the existing tool's bounds."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError("policy value must be numeric")
    try:
        number = float(value)
    except (ValueError, OverflowError) as exc:
        raise ValueError("policy value must be finite and numeric") from exc
    if not math.isfinite(number):
        raise ValueError("policy value must be finite and numeric")
    # The legacy tool truncates poll minutes, then clamps to one day.
    if integer:
        number = int(number)
    return max(minimum, min(maximum, number))


def _normalize(config):
    """Copy extras and normalize known fields; reject corrupted schemas loudly."""
    if not isinstance(config, dict):
        raise ValueError("group policy file must contain a JSON object")
    result = dict(config)
    for key in ("mention_only", "muted"):
        values = config.get(key, [])
        if not isinstance(values, list):
            raise ValueError(f"{key} must be a list")
        result[key] = list(dict.fromkeys(_group_id(value) for value in values))
    # Muted is the stricter existing runtime gate, so it wins legacy overlaps.
    result["mention_only"] = [
        group for group in result["mention_only"] if group not in result["muted"]
    ]
    for key, minimum, maximum, integer in (
        ("poll_minutes", 1, 1440, True),
        ("reply_pct", 0.0, 100.0, False),
    ):
        values = config.get(key, {})
        if not isinstance(values, dict):
            raise ValueError(f"{key} must be an object")
        result[key] = {
            _group_id(group): _number(value, minimum, maximum, integer)
            for group, value in values.items()
        }
    return result


class GroupPolicyStore:
    """Read and atomically update one JSON policy file using a shared RLock.

    Missing files mean default policy. Invalid JSON/schema and all I/O failures
    propagate, never masquerading as an empty file or a successful update. On
    POSIX, durability includes parent-directory fsync after replacement; Windows
    supports file fsync but has no portable directory-fsync API. A failure after
    replacement may have committed data, but is still reported as a failure.
    """

    def __init__(self, path):
        self.path = Path(path).resolve()
        canonical = os.path.normcase(str(self.path))
        with _LOCKS_GUARD:
            self._lock = _LOCKS.setdefault(canonical, threading.RLock())

    def read(self):
        """Return an independent normalized snapshot without changing the file."""
        with self._lock:
            try:
                with self.path.open(encoding="utf-8") as handle:
                    config = json.load(handle, parse_constant=_reject_constant)
            except FileNotFoundError:
                # Only an absent policy file is a legitimate default state.
                config = {}
            return _normalize(config)

    def _write(self, config):
        """Flush a private same-directory temporary file, replace, then fsync."""
        temporary = None
        directory_fd = None
        try:
            # Directory errors should fail before replacement whenever possible.
            if os.name != "nt":
                directory_fd = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            fd, temporary = tempfile.mkstemp(
                prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
            )
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                # mkstemp already uses 0600; enforce it before any data is written.
                os.chmod(temporary, 0o600)
                json.dump(config, handle, ensure_ascii=False, indent=2, allow_nan=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            temporary = None
            if directory_fd is not None:
                os.fsync(directory_fd)
        finally:
            # Do not leave sensitive temporary policy snapshots on failed writes.
            try:
                if temporary is not None:
                    os.unlink(temporary)
            finally:
                if directory_fd is not None:
                    os.close(directory_fd)

    def update(self, group, mode, pct=None, minutes=None):
        """Apply an exact supported mode and return success only after persistence.

        ``show`` permits an omitted group and never writes. Probability defaults
        to 100 (or legacy ``minutes``), poll defaults to 10; values are finite and
        clamped to 0..100 percent / 1..1440 minutes. Optional arguments never turn
        an unrelated explicit mode into probability mode.
        """
        if not isinstance(mode, str) or mode not in _MODES:
            raise ValueError("unsupported group policy mode")
        group = "" if mode == "show" and group in (None, "") else _group_id(group)
        with self._lock:
            config = self.read()
            if mode == "show":
                return {**config, "ok": True, "group": group, "mode": mode}
            if mode in ("normal", "mention_only", "muted", "poll"):
                # Poll must clear gates that would otherwise suppress its runs.
                for key in ("mention_only", "muted"):
                    config[key] = [item for item in config[key] if item != group]
            if mode in ("normal", "mention_only", "muted"):
                config["poll_minutes"].pop(group, None)
                config["reply_pct"].pop(group, None)
                if mode != "normal":
                    config[mode].append(group)
            elif mode == "probability":
                value = pct if pct is not None else (minutes if minutes is not None else 100)
                config["reply_pct"][group] = _number(value, 0.0, 100.0)
            elif mode == "poll":
                config["poll_minutes"][group] = _number(
                    10 if minutes is None else minutes, 1, 1440, integer=True
                )
            self._write(config)
            result = {**config, "ok": True, "group": group, "mode": mode}
            if mode == "probability":
                result["pct"] = config["reply_pct"][group]
            elif mode == "poll":
                result["minutes"] = config["poll_minutes"][group]
            return result
