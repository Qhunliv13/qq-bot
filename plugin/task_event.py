"""Minimal event adapter for persisted tasks, reusing normal tool and media handling."""
from types import SimpleNamespace


class TaskEvent:
    """Retain the original message destination without inventing an authenticated speaker."""

    def __init__(self, plugin, task):
        self.plugin = plugin
        self.task = task
        self.unified_msg_origin = str(task.get("origin") or "")
        self.message_obj = SimpleNamespace(message_id="", message=[])
        self.bot = SimpleNamespace(api=_TaskAPI(plugin))

    def get_self_id(self):
        """No QQ identity is promoted from task/tool evidence."""
        return ""

    def get_sender_id(self):
        """A task is not a new human message or an owner-authorized identity."""
        return "background-task"

    def get_sender_name(self):
        """Use an explicit attributed task label."""
        return "已保存任务"

    def get_group_id(self):
        """Preserve group/private destination separation."""
        scope = str(self.task.get("scope") or "")
        return "" if scope.startswith("private:") else scope

    def get_messages(self):
        """No fabricated human attachments or mentions."""
        return []

    def get_message_str(self):
        """Expose the stored continuation as task data, not a new human command."""
        return str(self.task.get("resume_prompt") or self.task.get("goal") or "")

    async def send(self, chain):
        """Reuse native serialization and require a platform receipt for the saved scope.

        Context.send_message returns a routing boolean (or None), not a OneBot
        receipt. Dispatch once through the existing transport instead: retrying
        an unknown outcome here could duplicate a visible task message.
        """
        import asyncio

        from .qq_delivery import receipt

        # A later failed send must not expose an earlier receipt as its outcome.
        self.last_delivery_receipt = None
        if not self.unified_msg_origin:
            raise RuntimeError("task_origin_missing")
        scope = str(self.task.get("scope") or "")
        private = scope.startswith("private:")
        destination = scope.removeprefix("private:") if private else scope
        # Bind the authenticated stored origin to the same explicit destination;
        # a task must never turn a group origin into an arbitrary private send.
        origin = self.unified_msg_origin.split(":")
        expected_type = "FriendMessage" if private else "GroupMessage"
        if (len(origin) != 3 or not origin[0] or origin[1] != expected_type
                or origin[2] != destination or not destination.isascii()
                or not destination.isdigit() or int(destination) <= 0):
            raise RuntimeError("task_origin_scope_mismatch")

        # This is the installed adapter's actual async serializer, including
        # media conversion, native mentions and file URI handling. Do not build
        # a competing renderer or fall back to CQ string interpolation.
        from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent

        messages = await AiocqhttpMessageEvent._parse_onebot_json(chain)
        if not isinstance(messages, list) or not messages or any(
                not isinstance(segment, dict) or not isinstance(segment.get("type"), str)
                or not isinstance(segment.get("data"), dict) for segment in messages):
            raise RuntimeError("task_message_invalid")
        action = "send_private_msg" if private else "send_group_msg"
        parameters = {"user_id" if private else "group_id": int(destination), "message": messages}
        response = await asyncio.to_thread(self.plugin._onebot_http, action, parameters)
        result = receipt(response, action=action)
        if not result["delivered"]:
            # Never forward raw platform errors, credentials or misleading
            # acknowledgements to callers that treat no exception as delivery.
            raise RuntimeError("task_delivery_unconfirmed")
        self.last_delivery_receipt = result
        # The existing foreground sender consumes iterable native receipt items.
        return [result]


class _TaskAPI:
    """Adapt existing OneBot HTTP helper for group-file/forward tools, not separate tool logic."""

    def __init__(self, plugin):
        self.plugin = plugin

    async def call_action(self, action, **parameters):
        """Match CQHttp's unwrapped data contract after confirming the native envelope."""
        import asyncio

        from .qq_native_actions import _status

        response = await asyncio.to_thread(self.plugin._onebot_http, action, parameters)
        # Existing tools consume CQHttp action data (files/url/messages), not the
        # HTTP envelope. Unknown acknowledgements must not become empty success.
        if _status(response) != "confirmed":
            raise RuntimeError("task_action_unconfirmed")
        # A confirmed null result is legitimate for actions without return data;
        # preserve it rather than inventing a dict or a delivery receipt.
        return response["data"]
