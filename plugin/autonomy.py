"""Durable, content-neutral opportunities for the shared native agent.

This controller schedules chances to deliberate, never messages or social actions.
Callers own agent execution, policy rechecks and acknowledgement after acquisition
of the existing execution lock. No I/O happens merely by importing this module.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import tempfile
import threading
import time


# Editable opportunity cadence, not a model round, duration or action limit.
DEFAULT_INTERVAL_SECONDS = 600
LOCAL_TIMEZONE = timezone(timedelta(hours=8))
_LOCKS = {}
_LOCKS_GUARD = threading.Lock()
# Only actual delegated-worker completion currently produces activity notifications.
_ACTIVITY_KINDS = frozenset({"delegated_result"})


def _event_id(value):
    """Validate opaque receipt identities without interpreting content or truncating ids."""
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise ValueError("event_id must be a nonempty string of at most 200 characters")
    return value


def _finite(value, positive=False):
    """Reject ambiguous/nonfinite timings rather than silently clamping a plan."""
    if isinstance(value, bool):
        raise ValueError("timing must be finite numeric data")
    number = float(value)
    if not math.isfinite(number) or (positive and number <= 0):
        raise ValueError("timing must be finite and positive" if positive else "timing must be finite")
    return number


def _scope(value):
    """Keep group and private namespaces distinct without inventing a target."""
    text = str(value or "").strip()
    identity = text.split(":", 1)[1] if text.startswith("private:") else text
    if not identity.isascii() or not identity.isdigit() or int(identity) <= 0:
        raise ValueError("explicit group or private scope required")
    return ("private:" if text.startswith("private:") else "") + str(int(identity))


class AutonomousController:
    """Persist scheduling metadata and authentic framework origins, not chat content."""

    def __init__(self, path, now=time.time):
        self.path = Path(path).resolve()
        self._now = now
        with _LOCKS_GUARD:
            self._lock = _LOCKS.setdefault(str(self.path), threading.RLock())

    def _load(self):
        """Merge explicit settings with defaults; corrupted state must fail closed."""
        state = {"enabled": True, "interval_seconds": DEFAULT_INTERVAL_SECONDS,
                 "scopes": [], "last_run": 0, "origins": {}, "poll_last": {},
                 "wake_slots": {}, "next_checks": {}, "last_scope": "",
                 "pending_events": {}, "notifications_seen": []}
        try:
            with self.path.open(encoding="utf-8") as handle:
                saved = json.load(handle)
        except FileNotFoundError:
            return state
        if not isinstance(saved, dict):
            raise ValueError("autonomy state must be an object")
        state.update(saved)
        if not isinstance(state["enabled"], bool) or not isinstance(state["scopes"], list):
            raise ValueError("invalid autonomy settings")
        state["interval_seconds"] = _finite(state["interval_seconds"], positive=True)
        state["last_run"] = _finite(state["last_run"])
        for field in ("origins", "poll_last", "wake_slots", "next_checks", "pending_events"):
            if not isinstance(state[field], dict):
                raise ValueError("invalid autonomy state: " + field)
        if not isinstance(state["notifications_seen"], list):
            raise ValueError("invalid autonomy state: notifications_seen")
        # Keep consumed receipt identities durable so repeated completion callbacks
        # cannot resurrect an opportunity after acknowledgement or a hot reload.
        seen = {_event_id(item) for item in state["notifications_seen"]}
        if len(seen) != len(state["notifications_seen"]):
            raise ValueError("duplicate autonomy notification identity")
        for identity, event in state["pending_events"].items():
            _event_id(identity)
            if not isinstance(event, dict):
                raise ValueError("invalid pending activity event")
            scope = _scope(event.get("scope"))
            kind = event.get("kind")
            origin = state["origins"].get(scope)
            if (event.get("scope") != scope or not isinstance(kind, str)
                    or kind not in _ACTIVITY_KINDS or not isinstance(origin, str)
                    or not origin.strip()):
                raise ValueError("invalid pending activity provenance")
            event["observed_at"] = _finite(event.get("observed_at"))
            # Accept pending data from an earlier writer without losing deduplication.
            if identity not in seen:
                state["notifications_seen"].append(identity)
                seen.add(identity)
        state["scopes"] = list(dict.fromkeys(_scope(item) for item in state["scopes"]))
        return state

    def _save(self, state):
        """Atomically flush a unique private temporary file before replacing state."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".autonomy-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(state, handle, ensure_ascii=False, indent=2, allow_nan=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def snapshot(self):
        """Return independent scheduling metadata without mutating settings."""
        with self._lock:
            return deepcopy(self._load())

    def origin(self, scope):
        """Return only a previously observed authentic origin, never synthesize one."""
        with self._lock:
            value = self._load()["origins"].get(_scope(scope), "")
            return value if isinstance(value, str) else ""

    def observe(self, scope, origin):
        """Persist a real event's destination id; an absent origin grants nothing."""
        scope = _scope(scope)
        if not isinstance(origin, str) or not origin.strip():
            return False
        with self._lock:
            state = self._load()
            if state["origins"].get(scope) == origin:
                return False
            # Start the first heartbeat interval at authentic observation, not epoch zero.
            if not state["origins"] and not state["last_run"]:
                state["last_run"] = _finite(self._now())
            state["origins"][scope] = origin
            self._save(state)
            return True

    def notify(self, scope, event_id, *, kind="delegated_result", now=None):
        """Queue typed completion metadata for a known origin, never run or send anything.

        Receipt identities remain seen after consumption, including across reloads.
        Disabled or restricted scopes retain their pending events until eligible.
        """
        scope = _scope(scope)
        event_id = _event_id(event_id)
        if not isinstance(kind, str) or kind not in _ACTIVITY_KINDS:
            raise ValueError("unsupported activity notification kind")
        observed_at = _finite(self._now() if now is None else now)
        with self._lock:
            state = self._load()
            origin = state["origins"].get(scope)
            # Completion data cannot bootstrap a destination or impersonate a human.
            if not isinstance(origin, str) or not origin.strip():
                return False
            if event_id in state["notifications_seen"]:
                return False
            state["pending_events"][event_id] = {
                "scope": scope, "kind": kind, "observed_at": observed_at}
            state["notifications_seen"].append(event_id)
            self._save(state)
            return True

    @staticmethod
    def _allowed(scope, conf):
        """Preserve muted and actual-mention-only policy for every opportunity."""
        restricted = {str(item) for key in ("muted", "mention_only")
                      for item in conf.get(key, [])}
        return scope not in restricted

    def opportunities(self, conf, now=None):
        """List due data-only events without consuming them or making model calls.

        Poll/wall-clock scopes retain their own configured cadence. Heartbeat
        chooses one observed eligible scope in round-robin order per global tick.
        Unknown-origin explicit polling may be used for read-only deliberation;
        the host must refuse delivery rather than invent a framework origin.
        """
        now = _finite(self._now() if now is None else now)
        if not isinstance(conf, dict):
            raise ValueError("group policies must be an object")
        with self._lock:
            state = self._load()
        events = []
        local = datetime.fromtimestamp(now, LOCAL_TIMEZONE)
        day, minute = local.strftime("%Y-%m-%d"), local.strftime("%H:%M")

        def add(kind, scope, due_at, key):
            """Transport trigger provenance without creating human message content."""
            events.append({"kind": kind, "scope": scope, "event_id": key,
                           "observed_at": now, "due_at": due_at,
                           "origin": state["origins"].get(scope, "")})

        for raw_scope, minutes in (conf.get("poll_minutes") or {}).items():
            scope = _scope(raw_scope)
            if not self._allowed(scope, conf):
                continue
            interval = _finite(minutes, positive=True) * 60
            last = _finite(state["poll_last"].get(scope, 0))
            due_at = last + interval if last else now
            if now >= due_at:
                add("poll_interval", scope, due_at, "poll:" + scope + ":" + str(last))
        for raw_scope, slots in (conf.get("wake_at") or {}).items():
            scope = _scope(raw_scope)
            if not self._allowed(scope, conf):
                continue
            for slot in slots or []:
                slot = str(slot).strip()
                key = scope + "@" + slot
                if slot == minute and state["wake_slots"].get(key) != day:
                    add("scheduled_time", scope, local.replace(second=0, microsecond=0).timestamp(),
                        "wake:" + key + ":" + day)
        if not state["enabled"]:
            return events
        # Actual result arrivals bypass heartbeat latency, not eligibility or model
        # policy. Reading them never acknowledges a receipt or moves any timer.
        for identity, event in state["pending_events"].items():
            scope = event["scope"]
            if self._allowed(scope, conf) and now >= event["observed_at"]:
                events.append({"kind": "activity_result", "scope": scope,
                               "source_type": event["kind"], "event_id": "activity:" + identity,
                               "observed_at": event["observed_at"], "due_at": event["observed_at"],
                               "origin": state["origins"][scope]})
        for scope, check in state["next_checks"].items():
            scope = _scope(scope)
            if self._allowed(scope, conf) and now >= _finite(check["next_at"]):
                add("model_schedule", scope, check["next_at"],
                    "model:" + scope + ":" + str(check["next_at"]))
        scopes = state["scopes"] or list(state["origins"])
        scopes = [scope for scope in scopes if self._allowed(scope, conf) and state["origins"].get(scope)]
        due_at = _finite(state.get("next_at", state["last_run"] + state["interval_seconds"]))
        if scopes and now >= due_at:
            previous = state.get("last_scope", "")
            index = (scopes.index(previous) + 1) % len(scopes) if previous in scopes else 0
            add("heartbeat", scopes[index], due_at, "heartbeat:" + str(due_at))
        return events

    def ack(self, trigger, now=None):
        """Consume only the matching started opportunity; repeated acks are no-ops.

        The host calls this inside its execution lock after policy/context refresh,
        immediately before the agent starts. Unknown operation outcomes must not
        be retried merely because a model or network request later fails.
        """
        now = _finite(self._now() if now is None else now)
        scope, kind = _scope(trigger["scope"]), trigger["kind"]
        with self._lock:
            state = self._load()
            key = str(trigger["event_id"])
            if kind == "activity_result":
                # Match the exact pending receipt before touching other trigger types;
                # stale, repeated or mismatched acknowledgements consume nothing.
                if not key.startswith("activity:"):
                    return False
                identity = key[len("activity:"):]
                event = state["pending_events"].get(identity)
                if (not event or event["scope"] != scope
                        or event["kind"] != trigger.get("source_type")
                        or event["observed_at"] != trigger.get("observed_at")):
                    return False
                del state["pending_events"][identity]
            elif kind == "poll_interval":
                last = _finite(state["poll_last"].get(scope, 0))
                if key != "poll:" + scope + ":" + str(last):
                    return False
                state["poll_last"][scope] = now
            elif kind == "scheduled_time":
                prefix = "wake:" + scope + "@"
                if not key.startswith(prefix):
                    return False
                slot, day = key[len(prefix):].rsplit(":", 1)
                slot_key = scope + "@" + slot
                if state["wake_slots"].get(slot_key, "") >= day:
                    return False
                state["wake_slots"][slot_key] = day
            elif kind == "model_schedule":
                check = state["next_checks"].get(scope)
                if not check or key != "model:" + scope + ":" + str(check["next_at"]):
                    return False
                state["next_checks"].pop(scope)
            elif kind == "heartbeat":
                due_at = _finite(state.get("next_at", state["last_run"] + state["interval_seconds"]))
                if key != "heartbeat:" + str(due_at):
                    return False
                state["last_run"], state["last_scope"] = now, scope
                state.pop("next_at", None)
            else:
                raise ValueError("unknown autonomous trigger")
            # This is an opportunity acknowledgement, never a goal-completion claim.
            state["last_started"] = {"event_id": key, "scope": scope, "kind": kind,
                                     "started_at": now, "outcome": "started"}
            self._save(state)
            return True

    def schedule(self, scope="", *, next_at=None, interval_seconds=None, enabled=None,
                 reason="", scopes=None):
        """Let the model choose the next check or cadence without dictating actions.

        Positive timing values are validated, not clamped to arbitrary ceilings.
        Scope policy is still checked at opportunity generation and effect time.
        """
        if scope:
            scope = _scope(scope)
        if next_at is not None:
            next_at = _finite(next_at)
        if interval_seconds is not None:
            interval_seconds = _finite(interval_seconds, positive=True)
        if enabled is not None and not isinstance(enabled, bool):
            raise ValueError("enabled must be boolean")
        if scopes is not None:
            if not isinstance(scopes, list):
                raise ValueError("scopes must be a list")
            scopes = list(dict.fromkeys(_scope(item) for item in scopes))
        with self._lock:
            state = self._load()
            if scope and next_at is not None:
                state["next_checks"][scope] = {"next_at": next_at, "reason": str(reason)}
            elif next_at is not None:
                state["next_at"] = next_at
            if interval_seconds is not None:
                state["interval_seconds"] = interval_seconds
            if enabled is not None:
                state["enabled"] = enabled
            if scopes is not None:
                state["scopes"] = scopes
            state["reason"] = str(reason)
            self._save(state)
            return deepcopy(state)
