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
        """Send through the same framework destination; never silently claim delivery without an origin."""
        if not self.unified_msg_origin:
            raise RuntimeError("task_origin_missing")
        return await self.plugin.context.send_message(self.unified_msg_origin, chain)


class _TaskAPI:
    """Adapt existing OneBot HTTP helper for group-file/forward tools, not separate tool logic."""

    def __init__(self, plugin):
        self.plugin = plugin

    async def call_action(self, action, **parameters):
        """Reuse the existing OneBot request implementation without blocking the event loop."""
        import asyncio
        return await asyncio.to_thread(self.plugin._onebot_http, action, parameters)
