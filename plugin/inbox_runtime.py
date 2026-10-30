"""Host integration for durable native input, independent of live response queues.

Only pending (not previously started) inputs may be reconstructed after matching
an actual adapter/account event. Started/unknown operations are never auto-replayed.
"""
from __future__ import annotations

from pathlib import Path


def store(plugin, root, *, recover_started=False):
    """Create one queue owner; demote started work only after old instances drain."""
    if not hasattr(plugin, "_durable_inbox"):
        from .pending_inbox import PendingInbox
        plugin._durable_inbox = PendingInbox(Path(root) / "pending_inbox.sqlite3",
                                            recover_started=recover_started)
    return plugin._durable_inbox


def ids(plugin, entries):
    """Resolve IDs admitted by this exact instance, never an input-supplied attribute."""
    lookup = getattr(plugin, "_inbox_event_ids", {})
    return [lookup[id(entry[0])] for entry in entries if id(entry[0]) in lookup]


def release(plugin, entries):
    """Remove exact local event mappings on every detached or terminal path."""
    lookup = getattr(plugin, "_inbox_event_ids", {})
    for entry in entries:
        lookup.pop(id(entry[0]), None)


def finish(plugin, entries, status):
    """Finish claimed input without claiming a model silence proves delivery."""
    queue = getattr(plugin, "_durable_inbox", None)
    identifiers = ids(plugin, entries)
    if queue is not None and identifiers and not queue.complete(identifiers, status=status):
        raise RuntimeError("intake_completion_unconfirmed")
    release(plugin, entries)


def admit(plugin, root, scope, event, text, has_image, woken, history):
    """Persist supported native input, or leave transient WebChat/local capabilities live.

    Unsupported transports remain explicit and unchanged, not fake durable QQ
    messages. Storage failures propagate so no side effects begin without a claim.
    """
    # Live WebChat responses cannot be resurrected after their browser request
    # ends; don't create fake durable response capabilities for transient hosts.
    platform_name = getattr(event, "get_platform_name", lambda: "")()
    if platform_name != "aiocqhttp":
        return {"status": "unsupported", "duplicate": False, "reason": "transient_or_non_native"}
    queue = store(plugin, root)
    receipt = queue.enqueue(scope, event, text, has_image, woken, history)
    if receipt.get("id") is not None and not receipt.get("duplicate"):
        plugin._inbox_event_ids = getattr(plugin, "_inbox_event_ids", {})
        plugin._inbox_event_ids[id(event)] = receipt["id"]
    return receipt


def recover(plugin, root, actual_event):
    """Restore pending messages only when an authentic native event confirms the account.

    No new QQ login/API call is made. Other accounts/scopes keep their pending
    rows; current policy is checked again by the normal worker before claiming.
    """
    from .pending_inbox import snapshot_event, restore_event, InboxError
    group = str(actual_event.get_group_id() or "")
    scope = group or "private:" + str(actual_event.get_sender_id() or "")
    try:
        current = snapshot_event(scope, actual_event, actual_event.get_message_str(), False, False)
    except ValueError:
        return []
    if current is None:
        return []
    queue = store(plugin, root)
    restored_scopes = []
    known = set(getattr(plugin, "_inbox_event_ids", {}).values())
    for metadata in queue.pending():
        if metadata["id"] in known:
            continue
        try:
            event = restore_event(plugin, metadata, platform=current["platform"], self_id=current["self_id"])
        except (ValueError, InboxError):
            continue
        destination = metadata["scope"]
        plugin._inbox_event_ids = getattr(plugin, "_inbox_event_ids", {})
        plugin._inbox_event_ids[id(event)] = metadata["id"]
        plugin._pending.setdefault(destination, []).append((event, metadata["text"],
            metadata["has_image"], metadata["woken"], ()))
        known.add(metadata["id"])
        restored_scopes.append(destination)
    return list(dict.fromkeys(restored_scopes))
