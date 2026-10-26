"""Background deliberation adapter for the existing shared native agent path.

Triggers are source data, not new human messages. This module owns no model,
tool implementation, content template, permission override or execution budget.
"""
import asyncio
from copy import deepcopy

from .task_event import TaskEvent
from .reply_selection import decode_reply, is_silence


class AutonomyEvent(TaskEvent):
    """Reuse authentic task transport without inventing a speaker or an @ mention."""

    def __init__(self, plugin, scope, origin, triggers):
        super().__init__(plugin, {"scope": scope, "origin": origin})
        self.triggers = deepcopy(triggers)

    def get_sender_id(self):
        """An internal opportunity is not an authenticated human identity."""
        return "autonomous"

    def get_sender_name(self):
        """Label the source without copying a member's identity or persona."""
        return "自主活动机会"

    def get_message_str(self):
        """Do not promote a trigger into a fabricated human request."""
        return ""


def _blocked(policy, scope):
    """Apply existing exact scope restrictions without relaxing mention-only."""
    return any(scope in {str(item) for item in policy.get(key, [])}
               for key in ("muted", "mention_only"))


async def run_turn(plugin, scope, triggers, controller):
    """Run one eligible opportunity under the same lock as chat and durable tasks.

    Busy foreground work defers without consuming opportunities. The fresh policy,
    context and authentic origin are checked after acquisition. Acknowledgement
    happens immediately before the first model call: an uncertain external action
    must never be replayed just because the call later fails. Acknowledgement is
    not a claim that the model's goal, a message, or any social action completed.
    The shared sender already records delivered visible output exactly once.
    """
    scope = str(scope)
    if not hasattr(plugin, "_execution_lock"):
        plugin._execution_lock = asyncio.Lock()
    lock = plugin._execution_lock
    if getattr(plugin, "_stopping", False):
        return {"status": "deferred", "reason": "stopping", "scope": scope}
    if lock.locked() or any(getattr(plugin, "_pending", {}).values()):
        return {"status": "deferred", "reason": "foreground_work", "scope": scope}
    async with lock:
        # The foreground may have become pending between the initial check and acquisition.
        if getattr(plugin, "_stopping", False) or any(getattr(plugin, "_pending", {}).values()):
            return {"status": "deferred", "reason": "foreground_work", "scope": scope}
        try:
            policy = plugin._group_policy(scope)
            if _blocked(policy, scope):
                return {"status": "blocked", "reason": "scope_policy", "scope": scope}
            origin = controller.origin(scope)
            if not origin:
                return {"status": "deferred", "reason": "origin_missing", "scope": scope}
            candidates = [trigger for trigger in triggers if str(trigger.get("scope")) == scope]
            if not candidates:
                return {"status": "deferred", "reason": "no_scope_trigger", "scope": scope}
            # Only still-due identities may start. This rejects stale opportunities
            # after another turn or a model's explicit rescheduling changed state.
            due = {trigger["event_id"] for trigger in controller.opportunities(policy)}
            candidates = [trigger for trigger in candidates if trigger.get("event_id") in due]
            if not candidates:
                return {"status": "deferred", "reason": "opportunity_changed", "scope": scope}
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return {"status": "blocked", "reason": "state_unavailable",
                    "error_type": type(exc).__name__, "scope": scope}

        contexts = getattr(plugin, "_turn_context", None)
        if contexts is None:
            contexts = {}
            plugin._turn_context = contexts
        sentinel = object()
        previous = contexts.get(scope, sentinel)
        contexts[scope] = {"history": list(getattr(plugin, "_recent", {}).get(scope, [])),
                           "clock": plugin._clock_context(), "messages": [],
                           "image_refs": [], "image_sources": [],
                           "trigger": {"kind": "autonomous_opportunity", "events": deepcopy(candidates)}}
        try:
            # No external effect starts if all acknowledgements were stale.
            started = [trigger for trigger in candidates if controller.ack(trigger)]
            if not started:
                return {"status": "deferred", "reason": "opportunity_changed", "scope": scope}
            contexts[scope]["trigger"]["events"] = deepcopy(started)
            event = AutonomyEvent(plugin, scope, origin, started)
            raw = await plugin._decide_tools_first(event, scope, "", False)
            selection = decode_reply(raw, [], allow_unattributed=True)
            if selection["error"]:
                return {"status": "failed", "reason": "reply_protocol_invalid", "scope": scope}
            text = selection["text"]
            if is_silence(text):
                return {"status": "completed", "scope": scope, "delivered": False}
            # Normal delivery rechecks the current policy and records actual visibility.
            delivered = await plugin._send(event, scope, text, decoded=True)
            if delivered is None or delivered is False:
                return {"status": "failed", "reason": "delivery_not_confirmed", "scope": scope}
            return {"status": "completed", "scope": scope, "delivered": True}
        except asyncio.CancelledError:
            # The consumed opportunity is intentionally not made due again.
            raise
        except Exception as exc:
            return {"status": "failed", "reason": "agent_turn_failed",
                    "error_type": type(exc).__name__, "scope": scope}
        finally:
            # Restore any prior caller-owned context rather than leaking a background turn.
            if previous is sentinel:
                contexts.pop(scope, None)
            else:
                contexts[scope] = previous
