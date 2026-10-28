"""Neutral tool-description metadata shared by schema-producing callers.

The native schema builders and dispatchers remain authoritative for capabilities,
parameters and required fields. This module only replaces known descriptions and
removes duplicate names; it does not implement tools or infer argument contracts.
"""
from copy import deepcopy

from .qq_social import schema as social_schema
from .workspace_tools import schema as workspace_schema
from .tool_discovery import schema as discovery_schema
from .delegation import schema as delegation_schema
from .task_planning import schema as planning_schema
from .interim_reply import schema as interim_schema
from .image_search import schema as image_schema
from .qq_requests import schema as request_schema
from .message_transfer import schema as transfer_schema
from .fact_tools import schemas as fact_schema


# Keys follow the distinct names declared by the native schema builders. Values
# describe functionality rather than persona, tool-selection policy or protocol.
TOOL_DESCRIPTIONS = {
    "autonomy_status": "查看主动观察的启用状态、间隔、下一次机会与已知会话范围。",
    "autonomy_schedule": "设置下一次主动观察时间、机会间隔或启用状态；不要求到点发言。",
    "web_search": "搜索公开网页，返回来源标题、URL 和摘要。",
    "web_fetch": "读取公开 HTTP(S) 网页正文及来源。",
    "tool_observations": "查看当前会话的工具执行结果、时间和执行状态。",
    "vm_shot": "截取虚拟机屏幕，返回截图文件路径。",
    "vm_browse": "在虚拟机浏览器中打开指定网址。",
    "vm_click_confirm": "执行当前点击预览所记录的点击操作。",
    "vm_click_preview": "截取屏幕并在指定坐标绘制点击位置标记，生成点击预览。",
    "vm_click_text": "按指定文字、匹配方式和索引点击页面元素。",
    "vm_click": "点击虚拟机屏幕上的指定坐标。",
    "vm_type": "向虚拟机输入指定文本。",
    "vm_key": "向虚拟机发送指定按键。",
    "vm_job_start": "启动后台 Python 或 Bash 脚本，返回任务标识。",
    "vm_job_list": "列出后台脚本任务的标识、运行状态和输出摘要。",
    "vm_job_status": "查询后台脚本的状态、退出码和有限长度输出。",
    "vm_job_cancel": "停止指定后台脚本对应的任务进程组。",
    "vm_exec": "在虚拟机中执行 Python 或 Shell 脚本并返回结果。",
    "vm_files": "列出虚拟机指定目录中的文件。",
    "vm_recall": "撤回最近发送的指定数量的消息。",
    "vm_eval": "在浏览器当前页面中执行 JavaScript 并返回结果。",
    "vm_slider_gap": "根据背景图和拼图 URL 计算滑块缺口横坐标及置信度。",
    "mail_recent": "读取最近邮件的内容、验证码和链接。",
    "mail_list": "列出最近邮件，支持按未读状态筛选。",
    "mail_web_login": "打开 Outlook 网页版并使用已配置的凭据登录。",
    "mail_web_read": "读取当前邮件网页中的列表文本。",
    "mail_code": "从最近邮件中提取验证码和链接。",
    "group_files": "列出群文件的名称、大小、上传者和上传时间。",
    "group_file_download": "根据群文件标识获取下载链接。",
    "group_policy": "查看或设置群消息处理模式、轮询间隔和回复概率。",
    "self_status": "查看群消息处理配置、记忆条目数量、模型和运行时间。",
    "send_sticker": "按关键词或文件名选择并发送表情包。",
    "send_file": "发送虚拟机中的文件，支持指定文件名及群聊或用户目标。",
    "wake_person_info": "查看指定唤醒人的群关联、回复设置、唤醒配置和记忆记录。",
    "wake_person_set": "设置指定唤醒人的回复概率、唤醒词和唤醒时间段。",
    "wake_person_show": "查看指定唤醒人的配置。",
    "wake_list": "查看唤醒人和唤醒词的群统计或指定群名单。",
    "wake_add": "添加唤醒人或唤醒词，支持指定群。",
    "wake_remove": "移除唤醒人或唤醒词，支持指定群。",
    "wake_search": "按关键词搜索唤醒人和唤醒词。",
    "wp_fact_move": "将指定通用记忆条目移动到指定唤醒人的记忆记录。",
    "wp_fact_add": "向指定唤醒人的记忆记录添加文本条目。",
    "wp_fact_list": "列出指定唤醒人的记忆条目。",
    "wp_fact_search": "按关键词搜索指定唤醒人的记忆条目。",
    "wp_fact_delete": "按编号或关键词删除指定唤醒人的记忆条目。",
    "facts_list": "列出带编号的通用长期记忆条目。",
    "facts_search": "按关键词搜索已保存的通用长期记忆条目。",
    "facts_edit": "按编号修改事实文本和可选 images；省略 images 保留图片，空数组清除图片。",
    "facts_read_image": "按事实 ID 和从 0 开始的 index 读取一张可见图片，供模型视觉查看，不发送到 QQ。",
    "facts_delete": "按编号或关键词删除通用长期记忆条目。",
    "remember": "保存长期事实、自由分类及可选 images 图片引用；下载保存图片内容，text 可空但须有图片，不自动记忆收到的图片。",
    "vm_fetch": "将指定 URL 或消息关联的文件下载到虚拟机。",
    "send_shot": "发送指定路径的截图，未指定路径时发送最新截图。",
    "qq_send": "向指定群聊、私聊或临时会话发送原生数组消息；群内 mentions 为 QQ ID 列表，文本按字面发送，返回 message_id 才确认。",
    "vm_hold_drag": "在指定坐标按住指定时长，再横向拖动指定距离并松开。",
    "vm_frame_eval": "在匹配指定条件的页面或 iframe 中执行 JavaScript 并返回结果。",
    "vm_slider_drag": "从指定坐标横向拖动滑块指定距离。",
    "task_add": "添加后台任务。run_at 为绝对 Unix 秒、delay_s 为相对延迟，不能混用；fixed_rate 保持周期相位且跳过错过轮次。返回状态和实际到期时间，创建不代表已执行或已送达。",
    "read_forward": "根据转发标识读取合并转发消息的内容。",
    "forward_expand": "根据转发标识或消息号展开合并转发消息中的各条内容。",
    "history_search": "按关键词搜索聊天档案，支持限定会话范围和结果数量。",
    "task_list": "按范围和状态列出后台任务。",
    "task_done": "将指定后台任务标记为完成并停止后续调度。",
    "task_cancel": "取消指定后台任务。",
    "task_add_script": "添加由脚本检测结果触发的后台任务。",
    "task_pause": "暂停指定后台任务的后续调度。",
    "task_resume": "恢复指定已暂停后台任务的调度。",
    "task_update": "更新后台任务的目标、简述、续做提示、调度或触发配置；run_at 为绝对 Unix 秒，不能与 delay_s 混用，cadence 可选 fixed_rate 或 after_completion。",
}


# Import-owned schemas own their descriptions as well as their argument contracts.
# This snapshot shares metadata without duplicating tool implementations or prose.
for schema_builder in (social_schema, workspace_schema, discovery_schema, delegation_schema, planning_schema, interim_schema, image_schema, request_schema, transfer_schema, fact_schema):
    TOOL_DESCRIPTIONS.update({item["function"]["name"]: item["function"]["description"]
                              for item in schema_builder()})
NATIVE_TOOL_COUNT = len(TOOL_DESCRIPTIONS)


def neutralize_schemas(schemas) -> list:
    """Deep-copy schemas, apply known descriptions and retain one schema per name.

    Duplicate names keep their last declaration, matching the native catalog's
    later, group-aware wake-tool contracts, while preserving first-seen order.
    Invalid or absent names raise ValueError instead of silently losing tools.
    Unknown names and all parameter metadata are preserved verbatim: inventing
    descriptions or rewriting nested schemas would guess a capability contract.
    """
    by_name = {}
    for schema in schemas:
        # Reject malformed identities explicitly; parameter validation belongs to
        # the original schema owner, not this description-only adapter.
        if not isinstance(schema, dict) or not isinstance(schema.get("function"), dict):
            raise ValueError("Tool schema must contain a function object")
        name = schema["function"].get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Tool schema must contain a non-empty function name")
        copied = deepcopy(schema)
        # Exact-name metadata is intentional: no phrase heuristics are applied to
        # descriptions, defaults, enums, nested properties or extension fields.
        if name in TOOL_DESCRIPTIONS:
            copied["function"]["description"] = TOOL_DESCRIPTIONS[name]
        by_name[name] = copied
    return list(by_name.values())
