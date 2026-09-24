# -*- coding: utf-8 -*-
"""插件配置：**只放"实例特有"的设置**，代码里不写死任何具体部署的值。

职责边界（避免两处各写一份默认值）
--------------------------------
· **本文件**：随部署而变的东西 —— 管理员 QQ、机器人自称、人设、工作群范围、
  监听群范围、OneBot 接口地址。这些**每个使用者都不一样**，必须可配置。
· **`main.py` 顶部的常量**：通用工程默认值 —— 回复频率、图片压缩边长、超时、
  表情包库上限等。这些是"项目级调参"，对所有使用者都成立，作为代码内默认值并由
  `README` 的配置表统一说明。

配置来源
--------
AstrBot 注入的插件配置（`Star.__init__(config=...)`）：当插件目录存在
`_conf_schema.json` 时，AstrBot 会生成 `data/config/<插件目录名>_config.json`，
并在 WebUI 里提供配置面板；插件在实例化时拿到它。缺失/非法值一律回退默认值并继续运行。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _as_str_set(value: Any) -> set[str]:
    """把配置里的"QQ 号 / 群号列表"统一转成 `set[str]`。

    兼容三种手写格式（用户填配置时格式很随意）：
      · 列表：`["123", 456]`
      · 分隔字符串：`"123, 456、789 012"`（中英文逗号、顿号、空格都当分隔符）
      · 单个数字：`123`
    非法项直接丢弃，不抛异常 —— 一个笔误不该让插件起不来。
    """
    if value is None:
        return set()
    if isinstance(value, (list, tuple, set)):
        items = list(value)
    else:
        text = str(value)
        for sep in ("，", "、", " ", ";", "；", "|", "/"):
            text = text.replace(sep, ",")
        items = [p for p in text.split(",") if p.strip()]
    out: set[str] = set()
    for item in items:
        s = str(item).strip()
        if s.isdigit():
            out.add(s)
    return out


def _as_str(value: Any, default: str = "") -> str:
    """把配置值转成去空白字符串；空值回退默认。"""
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _as_bool(value: Any, default: bool) -> bool:
    """把配置值转成布尔；无法识别时回退默认。

    兼容常见写法：`true/false`、`1/0`、`yes/no`、`on/off`、`是/否`、`开/关`。
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "y", "on", "是", "开"):
        return True
    if text in ("0", "false", "no", "n", "off", "否", "关"):
        return False
    return default


@dataclass
class Settings:
    """实例特有设置。**默认值必须通用**（不含任何具体部署的信息）。"""

    #: 管理员（"主人"）QQ 列表。留空 = 不区分身份，所有发言人都按普通群友对待。
    owner_ids: set[str] = field(default_factory=set)
    #: 机器人在提示词里的自称。留空 = 用 `prompts.DEFAULT_BOT_NAME`。
    bot_name: str = ""
    #: 自定义人设正文。留空 = 用 `prompts.DEFAULT_PERSONA`（通用默认人设）。
    persona: str = ""
    #: 人设文件路径（可选，相对 AstrBot 工作目录）。存在则优先于 `persona`，
    #: 便于把长人设单独维护成文件而不塞进配置字符串。
    persona_file: str = ""
    #: 只在哪些群工作。留空 = 所有群都工作。
    target_groups: set[str] = field(default_factory=set)
    #: **成员变动监听**只针对哪些群。留空 = 不监听（省流量；大群名单很大）。
    #: 与 `target_groups` 分开：监听只是读名单，不该因此限制机器人在别的群也能聊天。
    member_watch_groups: set[str] = field(default_factory=set)
    #: OneBot 实现（NapCat / Lagrange 等）的 HTTP 接口地址。
    #: 用于拉群列表/成员名单。容器化部署时注意"容器内的 127.0.0.1"不是宿主机。
    onebot_base: str = "http://127.0.0.1:3000"
    #: 是否允许联网查询（搜索 / 打开网页 / 搜图）。
    #: 关掉后：提示词不再引导模型写搜索标记，相关能力视为不可用。
    #: 默认开启；对出网受限或不想让机器人上网的部署可关。
    web_enabled: bool = True

    @classmethod
    def from_mapping(cls, raw: Any) -> "Settings":
        """从 AstrBot 注入的配置构造 `Settings`；缺失或非法项一律用默认值。

        @param raw - `Star.__init__` 收到的 config。可能是 None、空 dict，
            也可能是 AstrBot 的配置对象；这里只按"能取键"宽松处理，不做类型假设。
        """
        data: dict = {}
        if raw:
            try:
                data = dict(raw)
            except Exception:
                data = {}
        # 配置对象有时把用户项包在外层键里，宽松地拆一层
        for wrapper in ("settings", "config", "options"):
            inner = data.get(wrapper)
            if isinstance(inner, dict):
                data = {**data, **inner}
                break
        d = cls()
        return cls(
            owner_ids=_as_str_set(data.get("owner_ids")),
            bot_name=_as_str(data.get("bot_name"), d.bot_name),
            persona=_as_str(data.get("persona"), d.persona),
            persona_file=_as_str(data.get("persona_file"), d.persona_file),
            target_groups=_as_str_set(data.get("target_groups")),
            member_watch_groups=_as_str_set(data.get("member_watch_groups")),
            onebot_base=_as_str(data.get("onebot_base"), d.onebot_base).rstrip("/"),
            web_enabled=_as_bool(data.get("web_enabled"), d.web_enabled),
        )

    # ------------------------------------------------------------------ 便捷

    def is_owner(self, qq: str) -> bool:
        """该 QQ 是否是管理员（未配置管理员时一律 False）。"""
        return bool(qq) and qq in self.owner_ids

    def group_allowed(self, group: str) -> bool:
        """该群是否在"工作范围"内（未配置范围 = 所有群都算）。"""
        return (not self.target_groups) or (group in self.target_groups)

    def group_watched(self, group: str) -> bool:
        """该群是否需要监听成员变动（未配置 = 不监听）。"""
        return bool(group) and group in self.member_watch_groups

    def owner_desc(self) -> str:
        """管理员的展示文本（给日志用）。"""
        return "、".join(sorted(self.owner_ids)) if self.owner_ids else "（未配置）"
