"""彼岸酱的 AstrBot 消息、表情包与工具入口。

按会话接收和合批消息，通过共享模型工具循环裁决与发送，保留消息来源。
执行锁串行化消息与任务；已开始的工具和发送操作不因新消息取消。
表情包支持本地路径与 HTTP 图片，启动扫描索引并补充视觉描述。
后台任务负责成员名单观察、主动机会与持久任务；热加载按实例归属排空工作。
"""

from __future__ import annotations

# 插件目录，用于按路径加载 memory.py。
BASE_DIR = str(__import__("pathlib").Path(__file__).resolve().parent)  # 不依赖 os 的导入顺序。


def _dedupe_tool_schemas(items: list) -> list:
    """按 function.name 去重（保留最后一次定义；上游不接受重名）。"""
    seen: dict = {}
    order: list = []
    for it in items or []:
        try:
            name = ((it.get("function") or {}).get("name") or "")
        except Exception:                                  # noqa: BLE001
            continue
        if not name:
            continue
        if name not in seen:
            order.append(name)
        seen[name] = it
    return [seen[n] for n in order]


#  不按条数限制；只在接近 100 万 token 时忘掉最早 60%
# 中文近似：1 字符 ≈ 1 token，故用"累计字符数 > 1,000,000"作为触发条件
FORGET_TOKEN_LIMIT = 1000000
FORGET_RATIO = 0.6


import asyncio
import hashlib
import json
import os
import re
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone
from .tool_context import ToolContextArchive
from .runtime_paths import data_root
from .runtime_settings import configured_member_watch_groups

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.message_components import At, Image, Reply
from astrbot.api.star import Context, Star, register
from astrbot.core.message.message_event_result import MessageChain

PLUGIN_ID = "astrbot_plugin_higan"
# Single runtime data root shared by status and durable tool observations.
HIGAN_ROOT = str(data_root())
STICKER_DIR = Path("data/stickers")
MANIFEST = STICKER_DIR / "manifest.json"
#: AstrBot 收到图片后会落到这个目录（本地路径形态）
TEMP_DIR = Path("data/temp")

#: 只在这些群里工作（留空 = 所有群）。填群号可避免影响其它群。
TARGET_GROUPS: set[str] = set()
#: 成员变动监听的群名单，与回复范围分开配置。
#: TARGET_GROUPS 控制回复范围；这里仅控制名单观察。

# Empty means no member polling; operators opt in through private settings/env.
MEMBER_WATCH_GROUPS: set[str] = configured_member_watch_groups()
#: 普通回复之间的最小间隔（秒），0 表示无间隔。
MIN_REPLY_INTERVAL = 0
#: 唤醒回复之间的最小间隔（秒），与普通回复独立。


MIN_WOKEN_INTERVAL = 0
#: 每群每分钟普通回复条数上限。
MAX_REPLY_PER_MIN = 1000000
#: 每群每分钟唤醒回复条数上限。
MAX_WOKEN_PER_MIN = 1000000
#: 表情包库上限（超过后按最久未用淘汰）
MAX_STICKERS = 300
#: 单张表情包体积上限（字节）
MAX_STICKER_BYTES = 4 * 1024 * 1024
#: 动图最多抽取的帧数。
GIF_MAX_FRAMES = 4
#: 让模型能主动丢掉没用的图：写 `[删表情:关键词]`（只移出索引，不删文件）
DELETE_STICKER_MARKER = re.compile(
    r"[\[【（(［]\s*删(?:表情|图)\s*[:：]\s*([^\]】）)］]{1,60})\s*[\]】）)］]", re.I)
#: 让模型能下载图并发出：`[下图:关键词]` 或 `[下图:图片直链]`

DOWNLOAD_IMG_MARKER = re.compile(
    r"[\[【（(［]\s*下图\s*[:：]\s*([^\]】）)］]{1,120})\s*[\]】）)］]", re.I)
#: 下载图片的体积上限（字节）
MAX_DOWNLOAD_BYTES = 5 * 1024 * 1024
#: 从搜索结果里找图片时，最多打开几个页面去找 <img>
IMG_SEARCH_PAGES = 3
#: 视觉挑图的候选数量上限，用于控制请求体积。
MAX_CANDIDATES = 5
#: 视觉挑图的图片最长边像素数。
REVIEW_IMAGE_MAX_SIDE = 512
#: 单次视觉挑图超时（秒）；超时后使用调用方的回退路径。
REVIEW_TIMEOUT = 25
#: 生成回复的超时（秒）。超时就本次不回，避免用户无限等待。
CHAT_TIMEOUT = 45
# Optional deployment policy: zero/absent means the model ends its own turn, no round ceiling.
TOOL_ROUNDS = int(os.environ.get("HIGAN_TOOL_ROUNDS", "0")) or None
TOOL_TURN_TIMEOUT = float(os.environ.get("HIGAN_TOOL_TURN_TIMEOUT", "0")) or None
#: 机器人自己的头像缓存（描述文本），避免每次重新拉图与识图
AVATAR_CACHE = Path("data/higan_avatar.json")
#: 下载来的图片（搜图/头像）放这里
DOWNLOAD_DIR = Path("data/downloads")
#: 轮询群成员名单的间隔（秒），用于识别加入和退出。
MEMBER_WATCH_INTERVAL = 60
#: 同机 NapCat 的 OneBot HTTP 地址。
ONEBOT_BASE = "http://127.0.0.1:13000"
#: 每个群记住最近几张图（供"看看刚才那张图"这类请求）
MAX_REMEMBER_IMAGES = 5
#: 单次模型请求图片上限，含引用图片。
MAX_IMAGES_PER_CALL = 3

#: 表情包标记：兼容半角/全角方括号、书名号、圆括号与全角冒号；
#: 标记内容最多 60 字符，支持多个关键词。

STICKER_MARKER = re.compile(
    r"[\[【（(［]\s*表情包\s*[:：]\s*([^\]】）)］]{1,60})\s*[\]】）)］]", re.I)
#: 多关键词的分隔符（中英文逗号、顿号、竖线、斜杠、分号、空白）
KEYWORD_SPLIT = re.compile(r"[,，、|/;；\s]+")
#: 上网搜索标记：模型写 `[搜索:关键词]`，插件搜完把结果喂回模型再作答
SEARCH_MARKER = re.compile(
    r"[\[【（(［]\s*搜索\s*[:：]\s*([^\]】）)］]{1,80})\s*[\]】）)］]", re.I)
#: 查看某人头像的标记：`[头像:QQ号]`
AVATAR_MARKER = re.compile(
    r"[\[【（(［]\s*头像\s*[:：]\s*(\d{5,12})\s*[\]】）)］]", re.I)
#: 搜索结果最多取几条喂给模型
SEARCH_TOP_N = 5
# Generic equivalent query normalization; no product or relevance keyword rules.
SEARCH_QUERY_MAX = 2
#: 搜索后读取正文的页面数量。
PAGE_FETCH_TOP = 2
#: 最多尝试读取的页面数量，用于容忍抓取失败。
PAGE_FETCH_TRY = 4
#: 单个网页正文最多取多少字符（避免 prompt 过长）
PAGE_TEXT_LIMIT = 2500
#: 搜索超时（秒）
SEARCH_TIMEOUT = 20
SILENT_MARKERS = ("SILENT", "silent", "（不回复）", "(不回复)", "不回复")

#: 主人 QQ 名单，用于身份标注与权限判断。
#: 提示词同时提供 QQ 与身份，避免仅凭昵称推断。

from .runtime_settings import configured_owners
OWNER_QQS = configured_owners()

# 现行系统上下文从 conversation_context 组装；私人旧提示词仅保留在归档。
from .conversation_context import system_prompt
import random


@register(PLUGIN_ID, "Q-API", "彼岸酱：模型自主决定是否接话 + 表情包收发", "1.1.0")
class HiganPlugin(Star):
    """彼岸酱的自主回复与表情包能力。"""

    def __init__(self, context: Context) -> None:
        super().__init__(context)
        STICKER_DIR.mkdir(parents=True, exist_ok=True)
        self.manifest: list[dict] = self._load_manifest()
        # 跨群只读的近期聊天缓冲。
        self._all_recent: list = []
        self._last_reply_at: dict[str, float] = {}
        self._reply_times: dict[str, list[float]] = {}
        self._recent: dict[str, list[str]] = {}
        # 按群保存最近图片，供后续指代解析。
        self._recent_images: dict[str, list[str]] = {}
        # 按群保存成员名单快照，用于识别加入和退出。
        self._member_snapshot: dict[str, dict[str, str]] = {}
        # 按会话标记执行状态，待处理消息保存在列表中。
        self._busy: dict[str, bool] = {}
        self._last_sent: dict = {}
        self._pending: dict[str, list] = {}
        self._execution_lock = asyncio.Lock()
        self._ready = asyncio.Event()
        self._background_tasks: set = set()
        self._stopping = False
        # The local acceptor belongs to this exact plugin and shares its execution lock.
        self._local_runtime = None
        # Explicit trusted-host capability, never populated from model arguments.
        # An async adapter receives only the gate's immutable canonical action;
        # no VM path backend is installed by this plugin or inferred from attributes.
        self._vm_pointer_executor = None
        # 独立调用模型时显式拼接人设，不依赖框架注入。

        self.persona: str = self._load_persona()
        scanned = self._scan_sticker_dir()
        logger.info("[higan] 插件加载，表情包库 %d 张（新登记 %d），人设 %d 字"
                    % (len(self.manifest), scanned, len(self.persona)))

    async def initialize(self) -> None:
        """启动表情包描述、成员观察与任务循环，并在实例排空后开放消息入口。

        空描述和通用占位描述均需补充，以便按关键词匹配表情包。
        """
        await self._drain_previous_instances()
        missing = [s for s in self.manifest
                   if (s.get("desc") or "").strip() in ("", "表情包")]
        if missing:
            logger.info("[higan] 待补描述的表情包 %d 张" % len(missing))
            self._start_background(self._describe_missing(missing))
        # 后台轮询群成员变动。
        self._start_background(self._watch_members())

        # 在 initialize 中启动定时观察循环。
        try:
            if getattr(self, "_poll_loop_task", None) is None:
                self._poll_loop_task = self._start_background(self._poll_loop())
                logger.info("[higan] 定时查看循环已启动（30s 一轮）✓")
        except Exception as _pexc:  # noqa: BLE001
            logger.warning("[higan] 定时查看循环启动失败：%r", str(_pexc)[:120])
        self._ensure_task_loop()
        # Start after old-instance admission closes; failure must not disable QQ.
        from .local_runtime import LocalRuntime
        self._local_runtime = LocalRuntime(self, root=Path(HIGAN_ROOT))
        try:
            await self._local_runtime.start()
        except Exception as exc:
            logger.error("[higan] local entry unavailable: %s", type(exc).__name__)
        self._ready.set()
        logger.info("[higan] 已启动群成员变动监听（每 %ds 一次，群=%s）"
                    % (MEMBER_WATCH_INTERVAL,
                       ",".join(sorted(MEMBER_WATCH_GROUPS)) or "未配置"))

    def _start_background(self, coroutine):
        """登记后台任务的实例归属，完成后移出跟踪集合。"""
        task = asyncio.create_task(coroutine)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    async def _drain_previous_instances(self) -> None:
        """按实例归属排空消息与任务，只取消该实例处于空闲等待的后台循环。

        等待正在执行的模型、工具和发送完成，不按任务名取消框架任务。
        """
        known = {"_watch_members", "_poll_loop", "_free_tick", "_task_loop", "_describe_missing"}

        def await_chain(awaitable):
            """Inspect suspended coroutine/generator owners through framework await wrappers."""
            seen = set()
            while awaitable is not None and id(awaitable) not in seen:
                seen.add(id(awaitable))
                frame = (getattr(awaitable, "cr_frame", None)
                         or getattr(awaitable, "ag_frame", None)
                         or getattr(awaitable, "gi_frame", None))
                code = (getattr(awaitable, "cr_code", None)
                        or getattr(awaitable, "ag_code", None)
                        or getattr(awaitable, "gi_code", None))
                yield awaitable, frame.f_locals.get("self") if frame is not None else None, code
                awaitable = (getattr(awaitable, "cr_await", None)
                             or getattr(awaitable, "ag_await", None)
                             or getattr(awaitable, "gi_yieldfrom", None))

        def owned_tasks():
            """Rescan live tasks so handlers awaiting before their busy flag cannot escape."""
            found = {}
            for task in asyncio.all_tasks():
                if task is asyncio.current_task() or task.done():
                    continue
                for coroutine, owner, code in await_chain(task.get_coro()):
                    if (owner is not None and owner is not self
                            and owner.__class__.__name__ == self.__class__.__name__
                            and owner.__class__.__module__ == self.__class__.__module__):
                        found.setdefault(owner, {}).setdefault(task, []).append((coroutine, code))
            return found

        owners = set(owned_tasks())
        canceled = []

        async def wait_quiet():
            """Drain handlers and active background work; cancel only direct idle sleeps."""
            while True:
                current = owned_tasks()
                owners.update(current)
                waiting = False
                for owner in owners:
                    owner._stopping = True
                    # Closing admission never cancels a local turn already accepted.
                    runtime = getattr(owner, "_local_runtime", None)
                    if runtime is not None:
                        await runtime.close_acceptor()
                    if any(getattr(owner, "_busy", {}).values()) or getattr(owner, "_task_executing", False):
                        waiting = True
                    for task, nodes in current.get(owner, {}).items():
                        # Handler precedence protects nested sleeps during model/send work.
                        if any(code is not None and code.co_name in {"on_message", "_drain_pending", "_local_handle"}
                               for _, code in nodes):
                            waiting = True
                            continue
                        background = [(coroutine, code) for coroutine, code in nodes
                                      if code is not None and code.co_name in known]
                        if not background:
                            continue
                        # A sleep nested inside another method is active work, not loop idleness.
                        idle = any(getattr(getattr(coroutine, "cr_await", None), "cr_code", None)
                                   is asyncio.sleep.__code__ for coroutine, _ in background)
                        if idle:
                            task.cancel()
                            canceled.append(task)
                        else:
                            waiting = True
                if canceled:
                    await asyncio.gather(*canceled, return_exceptions=True)
                if not waiting:
                    return
                await asyncio.sleep(0.05)

        # Timeout cancels only this polling coroutine, never the model/send tasks being observed.
        # Propagate timeout so initialize cannot mark a replacement ready alongside active old work.
        await asyncio.wait_for(wait_quiet(), timeout=(TOOL_TURN_TIMEOUT or 600) + 120)
        for owner in owners:
            for field in ("_recent", "_history_origins", "_last_reply_at", "_reply_times", "_last_sent", "_my_recent"):
                old = getattr(owner, field, None)
                if isinstance(old, dict):
                    setattr(self, field, old.copy())
        logger.info("[higan] 旧插件实例已排空并清理 %d 个后台任务", len(canceled))

    async def _local_handle(self, event, prompt: str) -> dict:
        """Run an authenticated local turn on the live plugin's unchanged core."""
        from .local_runtime import LOCAL_SCOPE, is_local_event
        # Capability validation precedes history writes, role promotion and tools.
        if not is_local_event(event, self):
            raise PermissionError("local_event_untrusted")
        await self._ready.wait()
        if self._stopping:
            raise RuntimeError("local_entry_stopping")
        async with self._execution_lock:
            # Already admitted turns finish even if reload closes intake while queued.
            from .history_window import append_history, history_snapshot
            from .message_batch import message_record
            from .reply_selection import decode_reply, quote_target_ids
            group = LOCAL_SCOPE
            self._history_origins = getattr(self, "_history_origins", {})
            history = self._recent.setdefault(group, [])
            if not history:
                history.extend(self._hist().load(group, 200))
            prior = history_snapshot(history, self._history_origins.get(group, 0))
            # Native stored records retain timestamps and source scopes when merged.
            prior_records = self._hist().load_records(group, limit=200)
            record = message_record(event, prompt)
            line = "%s：%s" % (event.get_sender_name(), prompt)
            self._hist().append(group, line, record=record)
            append_history(self._recent, self._history_origins, group, line)
            self._turn_context = getattr(self, "_turn_context", {})
            self._turn_context[group] = {"history": prior, "local_history": prior_records,
                                         "messages": [record], "clock": self._clock_context()}
            # Same model/persona/tools/memory call as foreground QQ, not a new engine.
            reply = await self._decide_tools_first(event, group, prompt, False)
            selection = decode_reply(reply, [record], allowed_quote_ids=quote_target_ids(
                [record], self._turn_context[group].get('history'), scope=group,
                 quote_history=self._turn_context[group].get('quote_history')))
            if not selection["error"] and selection["text"]:
                await self._send(event, group, selection["text"],
                                 quote_id=selection.get("quote_id"), decoded=True)
            return {"text": selection["text"] if not selection["error"] else "",
                    "outputs": list(event.outputs), "transport": "local",
                    "status": "local_recorded" if event.outputs else "local_no_output",
                    "recorded": bool(event.outputs), "client_received": False}

    # ------------------------------------------------------------------ 工具

    @staticmethod
    def _load_persona() -> str:
        """从 AstrBot 的库里只读取出 higan 人设文本。

        只读，不写：直接写 `personas` 表会导致 AstrBot 崩溃循环（字段类型不匹配），
        所以这里只用 SELECT；取不到就返回空串（插件仍可工作，只是语气弱一些）。
        """
        from .runtime_settings import configured_persona
        supplied = configured_persona()
        if supplied is not None:
            return supplied
        import sqlite3
        # Resolve the existing framework data from its selected root, not a
        # different user's checked-out tree. The current working tree stays first.
        for db in dict.fromkeys((Path("data/data_v4.db"),
                                Path(os.environ.get("ASTRBOT_ROOT", Path.cwd())) / "data/data_v4.db")):
            if not os.path.exists(db):
                continue
            try:
                con = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=10)
                row = con.execute(
                    "SELECT system_prompt FROM personas WHERE persona_id='higan'"
                ).fetchone()
                con.close()
                if row and row[0]:
                    return row[0]
            except Exception as exc:
                logger.warning("[higan] 读取人设失败（%s）：%r", db, exc)
        logger.warning("[higan] 未取到 higan 人设，将仅用决策提示词")
        return ""

    def _load_manifest(self) -> list[dict]:
        """读表情包索引（`[{file, desc, ts, uses}]`）。"""
        if MANIFEST.exists():
            try:
                with open(MANIFEST, encoding="utf-8") as fh:
                    return json.load(fh)
            except Exception as exc:
                logger.warning("[higan] 表情包索引损坏，重建：%r", exc)
        return []

    def _save_manifest(self) -> None:
        """写表情包索引。"""
        try:
            with open(MANIFEST, "w", encoding="utf-8") as fh:
                json.dump(self.manifest, fh, ensure_ascii=False, indent=1)
        except Exception as exc:
            logger.warning("[higan] 表情包索引写入失败：%r", exc)

    def _scan_sticker_dir(self) -> int:
        """把目录里未被索引登记的图片补进索引（支持用户手动放进去的表情包）。"""
        known = {s.get("file") for s in self.manifest}
        added = 0
        for path in sorted(STICKER_DIR.iterdir()):
            if not path.is_file() or path.name == MANIFEST.name or path.name in known:
                continue
            if path.suffix.lower() not in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"):
                continue
            self.manifest.append({"file": path.name, "desc": "", "ts": time.time(),
                                  "uses": 0})
            added += 1
        if added:
            self._save_manifest()
        return added

    async def _describe_missing(self, items: list[dict]) -> None:
        """给还没有描述的表情包补一句中文描述（视觉模型），用于关键词匹配。"""
        for item in items[:20]:
            path = STICKER_DIR / item["file"]
            if not path.exists():
                continue
            try:
                desc = await self._describe(str(path))
            except Exception as exc:
                logger.warning("[higan] 补描述失败 %s：%r", item["file"], exc)
                continue
            item["desc"] = desc
            logger.info("[higan] 表情包描述 %s → %s" % (item["file"], desc))
        self._save_manifest()

    def _load_avatar_cache(self) -> dict:
        """读机器人头像的描述缓存（`{qq, desc}`）。"""
        if AVATAR_CACHE.exists():
            try:
                return json.loads(AVATAR_CACHE.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("[higan] 头像缓存损坏：%r", exc)
        return {}

    def _avatar_path(self, qq: str) -> Path:
        """机器人头像的本地文件路径。"""
        return AVATAR_CACHE.parent / ("higan_avatar_%s.jpg" % qq)

    async def _self_knowledge(self, event: AstrMessageEvent) -> str:
        """获取并缓存机器人自身头像的视觉描述，供对话上下文使用。"""
        qq = str(event.get_self_id() or "")
        if not qq:
            return ""
        cache = self._load_avatar_cache()
        if cache.get("qq") == qq and cache.get("desc"):
            return cache["desc"]
        path = self._avatar_path(qq)
        try:
            if not path.exists():
                url = "https://q1.qlogo.cn/g?b=qq&nk=%s&s=640" % qq
                raw = await asyncio.to_thread(self._read_image, url)
                if raw:
                    path.write_bytes(raw)
            desc = await self._describe_image(
                str(path),
                "这是机器人自己的 QQ 头像。用不超过 20 个字描述它长什么样"
                "（只输出描述，不要客套）。")
            if desc:
                AVATAR_CACHE.parent.mkdir(parents=True, exist_ok=True)
                AVATAR_CACHE.write_text(
                    json.dumps({"qq": qq, "desc": desc}, ensure_ascii=False),
                    encoding="utf-8")
                logger.info("[higan] 已记住自己的头像：%s" % desc)
            return desc
        except Exception as exc:
            logger.warning("[higan] 获取/描述自己的头像失败：%r", exc)
            return ""

    def _avatar_target(self, event: AstrMessageEvent, text: str) -> tuple[str, str]:
        """按提问对象解析头像归属，返回 (QQ, 头像说明)。

        依次匹配机器人自己、发言人、At 组件目标与群成员昵称。
        """
        t = text or ""
        if not any(k in t for k in ("头像", "长什么样", "长啥样", "样子", "自拍",
                                    "看看你", "你可爱", "你是谁")):
            return "", ""
        self_id = str(event.get_self_id() or "")
        sender = str(event.get_sender_id() or "")
        # 1) 机器人自己
        if re.search(r"你(的)?(头像|样子)|彼岸酱(的)?(头像|样子)|看看你|你长", t):
            return self_id, "你自己（彼岸酱）的头像"
        # 2) 说话人自己
        if re.search(r"(我|咱|俺)(的)?(头像|样子)", t):
            return sender, "提问者本人的头像"
        # 3) @ 了谁
        for comp in event.get_messages():
            if isinstance(comp, At):
                qq = str(getattr(comp, "qq", "") or "")
                if qq.isdigit() and qq != self_id:
                    return qq, "被 @ 那个人的头像"
        # 4) 按昵称找群成员
        snap = self._member_snapshot.get(str(event.get_group_id() or "")) or {}
        for qq, name in snap.items():
            if name and len(name) >= 2 and name in t:
                return qq, "%s 的头像" % name
        return "", ""

    async def _avatar_ref(self, event: AstrMessageEvent, text: str) -> tuple[str, str]:
        """按问题找出该看谁的头像，返回 `(本地路径, 说明)`；找不到返回 `("", "")`。"""
        qq, desc = self._avatar_target(event, text)
        if not qq:
            return "", ""
        path = await self._member_avatar(qq)
        if not path:
            return "", ""
        return str(path), desc

    async def _file_url_of_message(self, msg_id: str) -> str:
        """按消息号找到那条消息里的文件/语音/图片地址（用于 vm_fetch ）。

        Args:
            msg_id: OneBot 消息号（她上下文里看到的 #1234）。

        Returns:
            str: 可下载的 http 地址或本地路径；找不到返回空串。
        """
        try:
            # Reuse the checked configured transport, including its auth token;
            # a second implicit OneBot port could target a different account.
            _resp = await asyncio.to_thread(
                self._onebot_http, "get_msg", {"message_id": int(str(msg_id).lstrip("#"))})
            _data = (_resp or {}).get("data") or {}
            for _seg in (_data.get("message") or []):
                _t = _seg.get("type")
                _d = _seg.get("data") or {}
                if _t in ("file", "record", "video", "image"):
                    for _k in ("url", "file", "path"):
                        _v = str(_d.get(_k) or "")
                        if _v.startswith("http"):
                            return _v
        except Exception:                                  # noqa: BLE001
            return ""
        return ""

    def _person_conf(self, qq: str) -> dict:
        """取某个唤醒人的专属配置（回复率/专属词/时间段）。

        Args:
            qq: 对方 QQ 号。

        Returns:
            dict: {"pct": float, "words": list, "hours": list}（缺省为全开）。
        """
        try:
            with open(str(Path(HIGAN_ROOT) / "groups.json"), encoding="utf-8") as handle:
                conf = json.load(handle)
        except Exception:                                  # noqa: BLE001
            return {"pct": 100.0, "words": [], "hours": []}
        table = conf.get("wake_person_conf") or {}
        item = table.get(str(qq)) or table.get(str(qq).strip()) or {}
        try:
            pct = float(item.get("pct", 100))
        except Exception:                                  # noqa: BLE001
            pct = 100.0
        return {"pct": max(0.0, min(100.0, pct)),
                "words": [str(x) for x in (item.get("words") or [])],
                "hours": [int(x) for x in (item.get("hours") or [])][:2]}

    @staticmethod
    def _hours_ok(hours: list) -> bool:
        """当前时间是否落在配置的小时区间内（支持跨零点；空列表=不限）。"""
        if not hours or len(hours) < 2:
            return True
        start, end = hours[0], hours[1]
        now = time.localtime().tm_hour
        if start <= end:
            return start <= now < end
        return now >= start or now < end

    def _wake_hit(self, event: AstrMessageEvent, text: str,
                  group: str = "") -> str:
        """命中唤醒词/唤醒人 ⇒ 返回命中原因（"person"/"word"/""）。

        支持按群存储（wake_persons / wake_words 都是 {群号: [..]}）。

        Args:
            event: 当前消息事件。
            text: 消息文本。
            group: 当前群号。

        Returns:
            str: "person" / "word" / ""（未命中）。
        """
        try:
            with open(str(Path(HIGAN_ROOT) / "groups.json"), encoding="utf-8") as handle:
                conf = json.load(handle)
        except Exception:                                  # noqa: BLE001
            return ""

        _grp = str(group or "").strip()
        _grp_num = "".join(ch for ch in _grp if ch.isdigit())

        def _pick(table: dict) -> list:
            """按群号取名单：先精确，再按数字后缀匹配（兼容 int 键 / 前缀键）。"""
            if not isinstance(table, dict):
                return []
            for _k, _v in table.items():
                _ks = str(_k).strip()
                _kn = "".join(ch for ch in _ks if ch.isdigit())
                if _ks == _grp or (_kn and _kn == _grp_num) or (
                        _grp_num and _grp.endswith(_kn)):
                    return [str(x) for x in (_v or [])]
            return []

        def _list_for(key: str) -> list:
            """获取本群名单，同时兼容按群字典与列表两种配置格式。"""
            val = conf.get(key) or {}
            if isinstance(val, dict):
                return _pick(val)
            return [str(x) for x in val]

        try:
            sender = str(event.get_sender_id() or "")
        except Exception:                                  # noqa: BLE001
            sender = ""
        _persons = _list_for("wake_persons")
        if _persons:
            logger.info("[higan] 唤醒人名单(group=%s)：%s ｜ 本条发言人=%s"
                        % (_grp, ",".join(_persons[:6]), sender or "(空)"))
        if sender and sender in _persons:
            return "person"
        for word in _list_for("wake_words"):
            w = word.strip()
            if w and w in (text or ""):
                return "word"
        return ""

    @staticmethod
    def _at_targets(event: AstrMessageEvent) -> list[str]:
        """Read only top-level actual At components/OneBot segments, never quoted or rendered text."""
        streams = []
        try:
            streams.append(event.get_messages() or [])
        except Exception:
            pass
        streams.append(getattr(getattr(event, "message_obj", None), "message", None) or [])
        out = []
        for stream in streams:
            for component in stream:
                if isinstance(component, dict):
                    if str(component.get("type") or "").lower().split(".")[-1] != "at":
                        continue
                    target = (component.get("data") or {}).get("qq")
                else:
                    kind = str(getattr(component, "type", "")).lower().split(".")[-1]
                    if not isinstance(component, At) and kind != "at":
                        continue
                    target = (getattr(component, "qq", None) or getattr(component, "target", None)
                              or getattr(component, "user_id", None))
                target = str(target or "")
                if target.isdigit() and target not in out:
                    out.append(target)
        return out

    def _directly_mentioned(self, event) -> bool:
        """Framework wake flags include quotes/wake words, so cannot authorize mention-only admission."""
        try:
            own = str(event.get_self_id() or "")
        except Exception:
            own = str(getattr(getattr(event, "message_obj", None), "self_id", "") or "")
        return bool(own) and own in self._at_targets(event)

    def _policy_allows(self, event, group: str) -> bool:
        """Recheck persisted restrictions at dequeue and send, so stale queued replies cannot leak."""
        from .local_runtime import is_local_event, local_route
        from .task_event import TaskEvent
        if local_route(group, getattr(event, "unified_msg_origin", None)):
            # Stored local tasks can publish, but are never authenticated humans.
            return is_local_event(event, self) or (isinstance(event, TaskEvent) and event.plugin is self)
        if str(group).startswith("private:"):
            return True
        try:
            policy = self._group_policy(group)
            if str(group) in policy["muted"]:
                return False
            return str(group) not in policy["mention_only"] or self._directly_mentioned(event)
        except Exception as exc:
            logger.warning("[higan] 群策略读取失败，本次不回复：%s", type(exc).__name__)
            return False

    def _recent_others_talking(self, group: str, who: str, window: int = 4) -> bool:
        """识别最近消息中恰好两人交替发言的对话，不把多人闲聊视为两人对话。"""
        history = self._recent.get(group) or []
        seq: list[str] = []
        for line in history[-window:]:
            name = line.split("：", 1)[0]
            if name and name != "彼岸酱":
                seq.append(name)
        if len(seq) < 3:
            return False
        uniq = set(seq)
        if len(uniq) != 2:                       # 多于两人 = 多人闲聊，不算"两人对话"
            return False
        if any(seq[i] == seq[i + 1] for i in range(len(seq) - 1)):
            return False                         # 没有交替 = 不是一问一答
        me = who.split("（", 1)[0]
        return me in uniq

    @staticmethod
    def _who(event: AstrMessageEvent) -> str:
        """用昵称、身份和 QQ 号标注发言人；QQ 缺失时只标明身份未知。"""
        qq = str(event.get_sender_id() or "")
        name = event.get_sender_name() or ""
        # 单独标注机器人自身发言，避免与群成员混淆。
        try:
            _self = str(event.get_self_id() or "")
            if _self and qq and qq == _self:
                return "你（彼岸酱）"
        except Exception:  # noqa: BLE001
            pass
        if not qq or qq == "0":
            return "%s（QQ 未知，身份无法确认）" % (name or "某人")
        if not name:
            name = qq
        role = "主人" if qq in OWNER_QQS else "群友"
        base = "%s（%s，QQ %s）" % (name, role, qq)
        # 按 QQ 号判定本群唤醒人，并附带记录数量。
        try:
            import json as _j
            from pathlib import Path as _P
            _conf = _j.loads(_P(str(Path(HIGAN_ROOT) / "groups.json")).read_text(encoding="utf-8"))
            _gid = str(event.get_group_id() or "")
            _wl = [_ for _v in ((_conf.get("wake_persons") or {}).get(_gid) or [])
                   for _ in [str(_v)]]
            if qq in _wl:
                _cnt = 0
                try:
                    import importlib.util as _ilu
                    _sp = _ilu.spec_from_file_location(
                        __package__ + ".memory_wpcnt", str(_P(__file__).resolve().parent / "memory.py"))
                    _m = _ilu.module_from_spec(_sp)
                    _sp.loader.exec_module(_m)
                    _cnt = _m.WakePersonFacts(
                        str(Path(HIGAN_ROOT) / "memory" / "wake_persons.json")).load().count(qq)
                except Exception:  # noqa: BLE001
                    _cnt = 0
                return "【唤醒人 QQ %s · 我记了他 %d 条】%s" % (qq, _cnt, base)
        except Exception:  # noqa: BLE001
            pass
        return base

    def _rate_ok(self, group: str, woken: bool = False) -> bool:
        """按回复间隔和每分钟条数限流；唤醒消息使用独立的阈值。"""
        now = time.time()
        interval = MIN_WOKEN_INTERVAL if woken else MIN_REPLY_INTERVAL
        limit = MAX_WOKEN_PER_MIN if woken else MAX_REPLY_PER_MIN
        if now - self._last_reply_at.get(group, 0) < interval:
            logger.info("[higan] 群 %s：限流跳过（距上次 %ds < %ds，woken=%s）"
                        % (group, int(now - self._last_reply_at.get(group, 0)),
                           interval, woken))
            return False
        times = [t for t in self._reply_times.get(group, []) if now - t < 60]
        self._reply_times[group] = times
        if len(times) >= limit:
            logger.info("[higan] 群 %s：本分钟已达上限 %d 条，跳过（woken=%s）"
                        % (group, limit, woken))
            return False
        return True

    def _mark_replied(self, group: str) -> None:
        """记录一次回复时间。"""
        now = time.time()
        self._last_reply_at[group] = now
        self._reply_times.setdefault(group, []).append(now)

    def _tool_evidence(self):
        """Lazy persistent, scope-isolated tool observations, separate from persona/facts."""
        if getattr(self, "_tool_evidence_store", None) is None:
            self._tool_evidence_store = ToolContextArchive(Path(HIGAN_ROOT) / "tool_observations")
        return self._tool_evidence_store

    @staticmethod
    def _clock_context() -> dict:
        """Represent a single sampled clock as fields, not a conversational instruction."""
        now = datetime.now(timezone(timedelta(hours=8)))
        return {"sampled_at": now.isoformat(), "timezone": "Asia/Shanghai",
                "weekday_iso": now.isoweekday()}

    def _request_context(self, event, group: str, text: str, has_image: bool) -> str:
        """Render frozen prior history and the actual current input once, preserving source identity."""
        turn = (getattr(self, "_turn_context", {}) or {}).get(group, {})
        history = list(turn.get("history", ()))
        from .message_batch import message_record
        from .conversation_context import build_context
        records = turn["messages"] if "messages" in turn else [message_record(event, text)]
        from .local_runtime import is_local_event
        local_owner = is_local_event(event, self)
        records = [dict(record, sender=dict(record["sender"],
            role="owner" if (local_owner or str(record["sender"].get("id")) in OWNER_QQS) else "member"))
            for record in records]
        from .attachment_sources import collect_sources
        sources = turn.get("image_sources")
        if sources is None:
            sources = collect_sources(event)
        origins = []
        for item in sources:
            source = dict(item["source"])
            sender = dict(source.get("sender") or {})
            identity = str(sender.get("id") or "")
            sender["role"] = ("owner" if identity in OWNER_QQS else "member") if identity and identity != "0" else "unknown"
            source["sender"] = sender
            origins.append({"ref": item["ref"], "source": source})
        turn["image_sources"] = origins
        for record in records:
            if record.get("quote"):
                quote = dict(record["quote"])
                sender = dict(quote.get("sender") or {})
                identity = str(sender.get("id") or "")
                sender["role"] = ("owner" if identity in OWNER_QQS else "member") if identity and identity != "0" else "unknown"
                quote["sender"] = sender
                record["quote"] = quote
        observations = []
        observation_window = None
        try:
            # Reuse the archive's existing configurable prompt budget, not its
            # full retention window. Complete records remain queryable via the
            # existing tool_observations tool and are never deleted by selection.
            archive = self._tool_evidence()
            window = json.loads(archive.prompt(group))
            observations = window["observations"]
            observation_window = {"source": "tool_observation_archive", "retained": len(observations),
                                  "omitted": window["omitted"], "max_chars": archive.max_prompt_chars,
                                  "detail_tool": "tool_observations"}
        except Exception as exc:
            logger.warning("[higan] 工具观察读取失败：%s", type(exc).__name__)
        from .conversation_state import snapshot as conversation_snapshot
        try:
            scope_history = self._hist().load_records(str(group), limit=1)
        except Exception as exc:
            scope_history = []
            logger.warning("[higan] 会话活动时间读取失败：%s", type(exc).__name__)
        state = conversation_snapshot(str(group), history, records, turn.get("clock"),
            trigger=turn.get("trigger"), deliveries=turn.get("_deliveries"),
            scope_history=scope_history, now=time.time(), task=turn.get("task"))
        from .memory import Memory
        from .fact_context import build_memory_context
        try:
            persisted_memory = build_memory_context(
                Memory(str(Path(HIGAN_ROOT) / "memory" / "memory.json")).load(), str(group))
        except Exception as exc:
            # Missing/corrupt memory must be explicit, never a fabricated empty
            # successful store or an implicit rewrite of the persistent data.
            persisted_memory = {"source": "persisted_memory", "available": False,
                                "error": type(exc).__name__}
            logger.warning("[higan] memory_context_unavailable type=%s", type(exc).__name__)
        return build_context(scope=str(group), messages=records, history=history,
            clock=turn.get("clock", self._clock_context()), observations=observations,
            other_scopes=turn.get("other_scopes"), task=turn.get("task"),
            trigger=turn.get("trigger"), activity=turn.get("activity"),
            image_refs=turn.get("image_refs", self._image_refs(event)),
            attachment_origins=origins if origins else None,
            anchor_message_id=str(getattr(getattr(event, "message_obj", None), "message_id", "") or ""),
            history_context=turn.get("history_context"), conversation_state=state,
            deliveries=turn.get("_deliveries", []), observation_window=observation_window,
            memory=persisted_memory)

    def _record_visible_reply(self, group: str, text: str, record: dict | None = None) -> None:
        """Retain legacy text history plus optional actual send provenance, never inferred receipts."""
        if not text.strip():
            return
        line = "彼岸酱：" + text.strip()
        from .history_window import append_history
        self._history_origins = getattr(self, "_history_origins", {})
        append_history(self._recent, self._history_origins, group, line)
        if record is not None:
            from copy import deepcopy
            turn = getattr(self, "_turn_context", {}).get(group)
            if isinstance(turn, dict):
                # Freeze each actual send independently; an input batch is not an answered set.
                turn.setdefault("_deliveries", []).append(deepcopy(record))
        try:
            if record is None:
                self._hist().append(group, line)
            else:
                self._hist().append(group, line, record=record)
        except Exception as exc:
            logger.warning("[higan] 已发送回复落盘失败：%s", type(exc).__name__)
        self._last_reply_text = getattr(self, "_last_reply_text", {})
        self._last_reply_text[group] = text.strip()
        self._mark_replied(group)

    # --------------------------------------------------- 取图（含被引用的图）

    @staticmethod
    def _ref_of(comp) -> str:
        """从一个 Image 组件取出可用的图片引用（http URL 或本地路径）。"""
        for attr in ("url", "file", "path"):
            val = getattr(comp, attr, None)
            if val:
                return str(val)
        return ""

    def _quoted_text(self, event: AstrMessageEvent) -> str:
        """提取引用消息的文本，优先使用 Reply 属性，再从 chain 中拼接 Plain 段。"""
        for comp in event.get_messages():
            if not isinstance(comp, Reply):
                continue
            text = (getattr(comp, "message_str", "") or "").strip()
            if not text:
                parts = [getattr(sub, "text", "") for sub in (getattr(comp, "chain", None) or [])
                         if getattr(sub, "text", None)]
                text = "".join(parts).strip()
            if text:
                who = getattr(comp, "sender_nickname", "") or getattr(comp, "sender_id", "")
                return "%s：%s" % (who, text[:200]) if who else text[:200]
        return ""

    def _image_refs(self, event: AstrMessageEvent) -> list[str]:
        """Project visual refs from the single source-aware collector, keeping order."""
        from .attachment_sources import collect_sources
        return list(dict.fromkeys(record["ref"] for record in collect_sources(event)))

    def _remember_images(self, group: str, refs: list[str]) -> None:
        """记住群里最近出现过的图，供"看看刚才那张图"这类请求使用。"""
        if not refs:
            return
        cache = self._recent_images.setdefault(group, [])
        for ref in refs:
            if ref not in cache:
                cache.append(ref)
        del cache[:-MAX_REMEMBER_IMAGES]

    @staticmethod
    def _looks_like_asking_image(text: str) -> bool:
        """这句话是不是在问一张图（用于决定要不要带上"最近那张图"）。"""
        return any(k in (text or "") for k in
                   ("图", "图片", "照片", "表情", "这个", "这张", "看看", "看的"))

    @staticmethod
    def _data_urls(path: Path, max_side: int = REVIEW_IMAGE_MAX_SIDE,
                   max_frames: int = GIF_MAX_FRAMES) -> list[str]:
        """生成视觉输入的 data URL 列表，静图缩放，动图抽帧后横向拼接。

        动图最多取 max_frames 帧，以保留画面变化并控制上传体积。
        """
        import base64
        import io
        import mimetypes
        raw = path.read_bytes()
        try:
            from PIL import Image as PILImage
            img = PILImage.open(io.BytesIO(raw))
            frames: list = []
            total = int(getattr(img, "n_frames", 1) or 1)
            if total > 1:
                step = max(1, total // max_frames)
                for i in range(0, total, step):
                    img.seek(i)
                    frames.append(img.convert("RGB").copy())
                    if len(frames) >= max_frames:
                        break
            else:
                frames = [img.convert("RGB")]

            # 逐帧缩放到 max_side，再横向拼成一条（帧间留 4px 白缝，便于区分）
            scaled = []
            for fr in frames:
                if max(fr.size) > max_side:
                    fr.thumbnail((max_side, max_side))
                scaled.append(fr)
            if len(scaled) == 1:
                out = scaled[0]
            else:
                gap = 4
                width = sum(f.width for f in scaled) + gap * (len(scaled) - 1)
                height = max(f.height for f in scaled)
                out = PILImage.new("RGB", (width, height), (255, 255, 255))
                x = 0
                for fr in scaled:
                    out.paste(fr, (x, 0))
                    x += fr.width + gap
            buf = io.BytesIO()
            out.save(buf, format="JPEG", quality=80)
            return ["data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()]
        except Exception as exc:                      # 压缩失败就退回原图
            logger.warning("[higan] 图片处理失败，改用原图：%r", exc)
            mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
            return ["data:%s;base64,%s" % (mime, base64.b64encode(raw).decode())]

    @staticmethod
    def _data_url(path: Path, max_side: int = REVIEW_IMAGE_MAX_SIDE) -> str:
        """获取单张缩放后的 data URL；动图多帧处理复用 _data_urls。"""
        return HiganPlugin._data_urls(path, max_side=max_side, max_frames=1)[0]

    async def _pick_by_looking(self, keywords: list[str], context: str) -> dict | None:
        """将候选图及标签交给视觉模型选择，未选中时返回 None。"""
        if not self.manifest:
            return None
        pool = self._search_stickers(keywords)[:MAX_CANDIDATES]
        if not pool:
            logger.info("[higan] 关键词 %s 没搜到任何表情包" % keywords)
            return None
        refs: list[str] = []
        labels: list[str] = []
        for item in pool:
            path = STICKER_DIR / item["file"]
            if not path.exists():
                continue
            # 动图会返回"多帧拼成的一张长条图"，模型就能看懂过程
            for url in self._data_urls(path):
                refs.append(url)
                labels.append("图%d：%s" % (len(refs), (item.get("desc") or "（无标签）")[:24]))
        if not refs:
            return None
        provider = self.context.get_using_provider()
        if provider is None:
            return pool[0]
        prompt = ("【当前对话】\n%s\n\n【候选表情包】下面按顺序给你看了 %d 张图"
                  "（**动图是多帧拼成的长条图，从左到右就是它播放的顺序**）：\n%s\n\n"
                  "请挑一张最适合现在发的，**只回答序号**（例如 `2`）；"
                  "如果都不合适，只回答 `0`。\n"
                  "另外可选的第二行：\n"
                  "· 某张图的标签明显不准 → `标签N：更准的关键词`\n"
                  "· 某张图明显是废图/乱码/无意义（该丢掉）→ `删N`\n"
                  "（不需要就省略这些行）"
                  % (context or "（无上下文）", len(refs), "\n".join(labels)))
        try:
            resp = await asyncio.wait_for(
                provider.text_chat(
                    prompt=prompt,
                    system_prompt="你是表情包挑选助手，看得见每张图，严格按要求回答。",
                    image_urls=refs),
                timeout=REVIEW_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning("[higan] 挑图超时（>%ds），用第一张候选" % REVIEW_TIMEOUT)
            return pool[0]
        except Exception as exc:
            logger.warning("[higan] 挑图失败（用第一张候选）：%r", exc)
            return pool[0]
        text = (getattr(resp, "completion_text", "") or "").strip()
        pick = 0
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("标签"):
                self._apply_label_fix(line, pool)
                continue
            if line.startswith("删"):
                self._apply_drop(line, pool)          # 模型主动丢掉没用的图
                continue
            if line.isdigit():
                pick = int(line)
                break
        if pick == 0:
            logger.info("[higan] 模型看了 %d 张候选，认为都不合适（原答：%s）"
                        % (len(refs), text[:40].replace("\n", " ")))
            return None
        if 1 <= pick <= len(pool):
            chosen = pool[pick - 1]
            chosen["uses"] = int(chosen.get("uses", 0)) + 1
            chosen["last_used"] = time.time()
            self._save_manifest()
            logger.info("[higan] 模型看图选中第 %d 张：%s（%s）"
                        % (pick, chosen["file"], (chosen.get("desc") or "")[:20]))
            return chosen
        return pool[0]

    def _apply_drop(self, line: str, pool: list[dict]) -> None:
        """处理删图指令，只移出索引并保留原文件，避免不可恢复的删除。"""
        try:
            body = line[1:].strip()
            if "：" in body or ":" in body:
                body = re.split(r"[:：]", body, 1)[1].strip()
            keys = self._split_keywords(body) if body else []
            targets: list[dict] = []
            if keys:
                targets = [s for s in self.manifest
                           if any(k and k in (s.get("desc") or "") for k in keys)]
            else:
                num = "".join(ch for ch in body if ch.isdigit())
                if num and 1 <= int(num) <= len(pool):
                    targets = [pool[int(num) - 1]]
            removed = 0
            for item in targets:
                if item in self.manifest:
                    self.manifest.remove(item)
                    removed += 1
                    logger.info("[higan] 模型主动丢掉表情包：%s（%s）"
                                % (item["file"], (item.get("desc") or "")[:20]))
            if removed:
                self._save_manifest()
        except Exception as exc:
            logger.warning("[higan] 解析丢图指令失败（%s）：%r", line[:30], exc)

    def _apply_label_fix(self, line: str, pool: list[dict]) -> None:
        """处理模型给的标签修正行：`标签2：更准的关键词`。"""
        try:
            head, _, tail = line.partition("：") if "：" in line else line.partition(":")
            num = "".join(ch for ch in head if ch.isdigit())
            new = tail.strip()
            if not num or not new or new in ("-", "—", "无", "无需修改"):
                return
            idx = int(num)
            if not (1 <= idx <= len(pool)):
                return
            item = pool[idx - 1]
            old = item.get("desc") or ""
            item["desc"] = "、".join(self._split_keywords(new))[:60]
            self._save_manifest()
            logger.info("[higan] 模型改了关键词：%s 「%s」→「%s」"
                        % (item["file"], old, item["desc"]))
        except Exception as exc:
            logger.warning("[higan] 解析标签修正失败（%s）：%r", line[:30], exc)

    async def _pick_sticker_reviewed(self, keywords: list[str],
                                     context: str) -> dict | None:
        """兼容调用入口，复用 _pick_by_looking 的视觉挑图实现。"""
        return await self._pick_by_looking(keywords, context)

    def _register_sticker(self, path: Path, desc: str) -> None:
        """把一张图登记进表情包库（超限时淘汰最久未用的）。"""
        self.manifest.append({"file": path.name, "desc": desc[:60],
                              "ts": time.time(), "uses": 0})
        if len(self.manifest) > MAX_STICKERS:
            self.manifest.sort(key=lambda s: (s.get("uses", 0), s.get("ts", 0)))
            for old in self.manifest[:len(self.manifest) - MAX_STICKERS]:
                try:
                    (STICKER_DIR / old["file"]).unlink(missing_ok=True)
                except OSError:
                    pass
            self.manifest = self.manifest[len(self.manifest) - MAX_STICKERS:]
        self._save_manifest()

    # ------------------------------------------------------------- 消息处理

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent) -> None:
        """接收消息并按会话排队合批，保留来源与到达顺序，不抢占正在执行的工作。"""
        try:
            if self._stopping:
                return
            if hasattr(self, "_ready"):
                await self._ready.wait()
            # Releasing a failed old instance's ready gate only lets it stop;
            # it must not start work after a newer initialization marked it stopping.
            if self._stopping:
                return
            raw_request = getattr(getattr(event, "message_obj", None), "raw_message", None)
            if isinstance(raw_request, str):
                try:
                    raw_request = json.loads(raw_request)
                except (ValueError, TypeError):
                    raw_request = None
            if isinstance(raw_request, dict) and raw_request.get("post_type") == "request":
                from .qq_request_store import RequestStore
                try:
                    platform = str(getattr(event, "unified_msg_origin", "") or "").split(":", 1)[0]
                    captured = RequestStore(Path(HIGAN_ROOT) / "qq_requests" / "requests.sqlite").capture(
                        raw_request, platform=platform or None)
                    logger.info("[higan] native_request captured=%s", bool(captured))
                except Exception as exc:
                    logger.warning("[higan] native_request_capture_failed type=%s", type(exc).__name__)
                # A pending account request is source data, not an empty human
                # chat, a model prompt, or authorization to approve it.
                event.stop_event()
                return
            # WebChat owns a request-scoped response queue: its pipeline must
            # remain open until this same plugin worker finishes delivery.
            is_webchat = getattr(event, "get_platform_name", lambda: "")() == "webchat"
            # 私聊按独立会话处理。
            group_raw = str(event.get_group_id() or "")
            if group_raw:
                if TARGET_GROUPS and group_raw not in TARGET_GROUPS:
                    return
                group = group_raw
            else:
                # 私聊按 QQ 隔离历史、队列、执行状态和限流计数。
                sender = str(event.get_sender_id() or "") or "unknown"
                group = "private:" + sender
                if is_webchat:
                    from hashlib import sha256
                    # Dashboard logins share many conversations. Hash the authentic
                    # origin to stay below Recorder's 60-character filename limit.
                    group = "private:webchat:" + sha256(str(event.unified_msg_origin).encode("utf-8")).hexdigest()[:40]

            raw_event = getattr(getattr(event, "message_obj", None), "raw_message", None)
            if isinstance(raw_event, str):
                try:
                    raw_event = json.loads(raw_event)
                except (ValueError, TypeError):
                    raw_event = None
            source_notice = isinstance(raw_event, dict) and raw_event.get("post_type") == "notice"
            text = (event.get_message_str() or "").strip()
            # Plain rendering omits valid non-text components (faces, cards,
            # voice/files and adapter-unknown segments). Preserve their actual
            # source metadata instead of inventing an empty user utterance.
            from .nontext_context import describe_event
            nontext = describe_event(event)
            event._higan_nontext_context = nontext
            if source_notice and not nontext.get("recognized"):
                # Framework notice wrappers are not empty user chat. No guessed
                # time-window or file-ID deduplication is used to suppress them.
                event.stop_event()
                logger.info("[higan] source_notice ignored_attachment_free=True")
                return
            if nontext.get("recognized"):
                text = (text + "\n" + json.dumps({"source": "message_components",
                        "trust": "untrusted_data", "segments": nontext["segments"]},
                        ensure_ascii=False, allow_nan=False)).strip()
                logger.info("[higan] nontext_context segments=%d image_refs=%d",
                            len(nontext["segments"]), len(nontext.get("image_refs") or []))
            # 读取合并转发的原始消息记录。
            try:
                _fwd = await self._forward_to_text(event)
                if _fwd:
                    # This JSON is source data, not an invented message from the sender.
                    # A failed card read remains recognized content rather than an empty poke.
                    text = (text + "\n" + _fwd).strip()
            except Exception as _exc:  # noqa: BLE001
                logger.warning("[higan] 转发解析异常（忽略）：%r", str(_exc)[:120])
            images = [c for c in event.get_messages() if isinstance(c, Image)]

            # ① 群友发图 → 收集进表情包库（后台跑，不影响回复时序）+ 记住最近几张图
            if images:
                self._remember_images(group, self._image_refs(event))
                # 将收到的图片归档到机器人虚拟机，供工具回看。
                try:
                    await self._archive_images(event, group)
                except Exception as _exc:  # noqa: BLE001
                    logger.warning("[higan] 归档图片失败（忽略）：%r", _exc)
                asyncio.create_task(self._collect_images(event, group, images))

            # Chat commands are disabled; never hand slash input back to
            # the framework greeting/default-model pipeline. Tools remain callable.
            if text.startswith("/"):
                event.stop_event()
                return

            _woken_flag = self._is_woken(event)
            logger.info("[higan] 收到群 %s 消息：%s（%s）｜woken=%s"
                        % (group, text[:40] or "[图片]", self._who(event), _woken_flag))
            if "@" in (text or "") or _woken_flag:
                try:
                    _sid = str(event.get_self_id() or "")
                    _ats = []
                    for _seg in (getattr(getattr(event, "message_obj", None), "message", None) or []):
                        if str(getattr(_seg, "type", "") or "") == "at":
                            _ats.append(str(getattr(_seg, "qq", None) or "?"))
                    logger.info("[higan] @ 诊断：self_id=%s ｜ @目标=%s ｜ woken=%s"
                                % (_sid or "(空)", ",".join(_ats) or "(无)", _woken_flag))
                except Exception:  # noqa: BLE001
                    pass

            # 每条接收的群消息都记录发言人身份，保留完整上下文。


            quoted = self._quoted_text(event)
            # 保留消息号，供模型显式选择引用目标。
            _mid = getattr(getattr(event, "message_obj", None), "message_id", None)
            _tag = ("#%s" % _mid) if _mid else ""
            line = "%s%s：%s%s" % (self._who(event), _tag, ((text or "") if str(text or "").strip() else ("（这是 %s 发的图片/文件）" % self._who(event) if images else "（无正文消息）")),
                                   "（引用 %s）" % quoted if quoted else "")
            history = self._recent.setdefault(group, [])
            # 首次访问会话时从磁盘恢复历史。
            if not history:
                try:
                    _old = self._hist().load(group, 200)
                    if _old:
                        history.extend(_old)
                        logger.info("[higan] 已从磁盘恢复 %s 的历史 %d 行 ✓" % (group, len(_old)))
                except Exception as _exc:  # noqa: BLE001
                    logger.warning("[higan] 恢复历史失败（忽略）：%r", _exc)
            from .history_window import append_history, history_snapshot as stable_snapshot
            self._history_origins = getattr(self, "_history_origins", {})
            history_snapshot = stable_snapshot(history, self._history_origins.get(group, 0))
            append_history(self._recent, self._history_origins, group, line)
            # 同时写入带群标签的全局缓冲。
            try:
                self._all_recent.append("[群%s] %s" % (group, line))
                del self._all_recent[:-120]
            except Exception:  # noqa: BLE001
                pass
            try:
                from .message_batch import message_record
                self._hist().append(group, line, record=message_record(event, text))  # 原生来源落盘
            except Exception:  # noqa: BLE001
                pass

            if source_notice:
                # Upload metadata is an observation, not a second utterance. The
                # actual native file message remains its own normal opportunity.
                event.stop_event()
                logger.info("[higan] source_notice attachment_history_only=True")
                return
            # Only real admitted chat can establish an authentic framework destination.
            # State failures do not break foreground messages or fabricate an origin.
            try:
                self._autonomy_controller().observe(group, str(getattr(event, "unified_msg_origin", "") or ""))
            except Exception as exc:
                logger.warning("[higan] 自主会话来源未保存 type=%s", type(exc).__name__)
            # Compute wake eligibility now; reply probability applies once to the collected batch.
            _at_me_now = self._directly_mentioned(event)
            _woken_e = False
            try:
                # 按真实 At 目标与机器人 QQ 判断唤醒。
                _self_qq = ""
                try:
                    _self_qq = str(event.get_self_id() or "")
                except Exception:  # noqa: BLE001
                    _self_qq = ""
                _at_targets_now = []
                try:
                    _at_targets_now = [str(x) for x in (self._at_targets(event) or [])]
                except Exception:  # noqa: BLE001
                    _at_targets_now = []
                _at_me_now = bool(_self_qq) and _self_qq in _at_targets_now
                _wake_why = self._wake_hit(event, text, str(group))
                _woken_e = bool(self._is_woken(event)) or _at_me_now or bool(_wake_why)
                if _wake_why:
                    logger.info("[higan] 命中唤醒%s ⇒ 直接进入回复判定"
                                % ("人" if _wake_why == "person" else "词"))
                # 唤醒人可单独配置回复率、专属词与时间段。
                if _wake_why == "person":
                    try:
                        _sender = str(event.get_sender_id() or "")
                        _pc = self._person_conf(_sender)
                        _hit_word = any(w and w in (text or "") for w in _pc["words"])
                        if _hit_word:
                            logger.info("[higan] 命中他的专属唤醒词 ⇒ 必定进判定")
                            _woken_e = True
                        elif self._hours_ok(_pc["hours"]) and random.random() * 100 < _pc["pct"]:
                            logger.info("[higan] 唤醒人 %s 命中（pct=%s，时段内）⇒ 进判定"
                                        % (_sender, _pc["pct"]))
                            _woken_e = True
                        else:
                            logger.info("[higan] 唤醒人 %s 本次不触发（pct=%s，时段=%s）"
                                        % (_sender, _pc["pct"], _pc["hours"] or "不限"))
                            _woken_e = False
                    except Exception as _pexc:  # noqa: BLE001
                        logger.warning("[higan] 唤醒人配置读取失败：%r", str(_pexc)[:100])
                if _at_me_now:
                    logger.info("[higan] 本条 @ 了她（self=%s）⇒ 直接进入回复判定"
                                % _self_qq)
            except Exception as _pexc:  # noqa: BLE001
                logger.warning("[higan] 概率判定异常（忽略）：%r", str(_pexc)[:100])

            _woken_e = bool(_woken_e or _at_me_now)
            if not self._policy_allows(event, group):
                event.stop_event()
                logger.info("[higan] 群 %s 策略拦截非准入消息（仅留历史）", group)
                return
            # 插件统一处理唤醒与普通消息，并停止默认事件传播，避免重复回复。


            # 框架只有在事件存在结果且已停止时才跳过默认模型调用。


            # should_call_llm(False) 的消费点在插件之前，不能替代 stop_event。

            # 停止管线后 RespondStage 不执行，回复需通过 event.send 直接发送。

            event.stop_event()
            self._pending.setdefault(group, []).append(
                (event, text, bool(images or self._image_refs(event)), bool(_woken_e or _at_me_now), history_snapshot))
            if not hasattr(self, "_scope_workers"):
                self._scope_workers = {}
            if self._busy.get(group):
                if is_webchat:
                    await asyncio.shield(self._scope_workers[group])
                return
            # Mark ownership before scheduling: consecutive platform deliveries must
            # enqueue immediately without creating another worker for this scope.
            self._busy[group] = True
            try:
                if not hasattr(self, "_background_tasks"):
                    self._background_tasks = set()
                worker = self._start_background(self._drain_pending(group))
                self._scope_workers[group] = worker
            except Exception:
                # A scheduling failure must not strand the scope behind a busy flag.
                self._busy[group] = False
                raise
            if is_webchat:
                # The scheduler emits WebChat's terminal `end` on handler return.
                # Keep QQ ingress nonblocking, but await WebChat's owned delivery;
                # cancellation of a request must not cancel active tools/sends.
                await asyncio.shield(worker)
        except Exception as exc:
            logger.error("[higan] 处理消息出错：%r", exc)

    async def _drain_pending(self, group: str) -> None:
        """Run one owned scope worker without blocking sequential framework ingress.

        Snapshot only after obtaining the shared FIFO execution lock. Incoming
        messages can join while waiting, but never mutate an already active turn.
        The busy flag also keeps reload drain aware of model/tool/send ownership.
        """
        try:
            while self._pending.get(group):
                if not hasattr(self, "_execution_lock"):
                    self._execution_lock = asyncio.Lock()
                queued_at = time.monotonic()
                async with self._execution_lock:
                    lock_wait = time.monotonic() - queued_at
                    # No await separates the snapshot from detaching its source list:
                    # arrivals during execution therefore belong to the next turn.
                    entries = list(self._pending.get(group, []))
                    # Each WebChat request owns a distinct response queue. Never
                    # merge its reply into another request's batch anchor.
                    if entries and getattr(entries[0][0], "get_platform_name", lambda: "")() == "webchat":
                        self._pending[group] = entries[1:]
                        entries = entries[:1]
                    else:
                        self._pending[group] = []
                    entries = [entry for entry in entries if self._policy_allows(entry[0], group)]
                    if not entries:
                        continue
                    from .message_batch import collect_batch
                    batch = collect_batch(entries)
                    ev, txt = batch["event"], batch["text"]
                    from .attachment_sources import collect_sources
                    sources = [source for item in entries for source in collect_sources(item[0])]
                    refs = list(dict.fromkeys(source["ref"] for source in sources))
                    selected_refs = refs[-MAX_IMAGES_PER_CALL:]
                    sources = [source for source in sources if source["ref"] in selected_refs]
                    self._turn_context = getattr(self, "_turn_context", {})
                    self._turn_context[group] = {"history": batch["history"],
                        "clock": self._clock_context(), "messages": batch["records"],
                        "image_refs": selected_refs, "image_sources": sources}
                    logger.info("[higan] 群 %s 合批裁决：%d 条消息，一次模型机会", group, len(entries))
                    turn_started = time.monotonic()
                    logger.info("[higan] reply_phase phase=lock_wait seconds=%.3f batch_size=%d",
                                lock_wait, len(entries))
                    try:
                        await self._handle_one(ev, group, txt, batch["has_image"], batch["woken"])
                    except Exception as exc:
                        logger.error("[higan] 群 %s 合批处理失败：%s", group, type(exc).__name__)
                    finally:
                        logger.info("[higan] reply_phase phase=turn seconds=%.3f batch_size=%d",
                                    time.monotonic() - turn_started, len(entries))
                        self._turn_context.pop(group, None)
        except Exception as exc:
            logger.error("[higan] 群 %s 待处理消息出错：%s", group, type(exc).__name__)
        finally:
            # There is no await between observing an empty queue and releasing busy,
            # so the next admission either joins this worker or starts its successor.
            self._busy[group] = False
            getattr(self, "_scope_workers", {}).pop(group, None)

    @staticmethod
    def _is_woken(event: AstrMessageEvent) -> bool:
        """Recognize direct private input or a native self At, never prefix flags.

        Framework wake flags also cover slash commands and must not be treated
        as evidence that a user addressed this bot. The plugin owns its replies.
        """
        # 私聊消息视为对机器人的直接唤醒。

        try:
            if not str(event.get_group_id() or ""):
                return True
        except Exception:
            pass
        # 从 OneBot 原始消息段检查针对机器人的 At。
        self_id = ""
        for name in ("get_self_id", "self_id", "selfId"):
            raw = getattr(event, name, None)
            value = raw() if callable(raw) else raw
            if value:
                self_id = str(value)
                break
        if not self_id:
            try:
                self_id = str(event.message_obj.self_id or "")
            except Exception:
                self_id = ""
        from .message_batch import at_targets
        # Reuse canonical top-level segment parsing for dicts, enums and native
        # components. Quoted At segments and broad framework flags are not wake.
        if self_id and self_id in at_targets(event):
            return True
        if self_id is None or self_id == "":
            # 无法读取自身 QQ 时记录诊断，不推断为唤醒。
            try:
                logger.warning("[higan] 取不到 self_id，@ 判定降级为 False（消息=%s）"
                               % (str(getattr(event, "message_str", ""))[:60],))
            except Exception:  # noqa: BLE001
                pass
        return False

    async def _handle_one(self, event: AstrMessageEvent, group: str, text: str,
                          has_image: bool, woken: bool) -> None:
        """按会话策略和限流处理消息，再交模型裁决并发送；唤醒消息可无正文。"""
        if not self._policy_allows(event, group):
            return
        # 遵循分群策略：muted 不发送，mention_only 仅接收真实 At。
        try:
            _pol = self._group_policy(group)
            # 按配置间隔检查群消息；At 可绕过此检查。
            _mins = int((_pol.get("poll_minutes") or {}).get(str(group)) or 0)
            if _mins > 0 and not woken:
                _seen = getattr(self, "_poll_seen", None)
                if _seen is None:
                    self._poll_seen = {}
                    _seen = self._poll_seen
                _last = float(_seen.get(str(group)) or 0)
                if time.time() - _last < _mins * 60:
                    return
                # 这里只跳过处理，观察时间戳由定时器维护。
                logger.info("[higan] 群 %s 未到查看间隔，跳过（%d 分钟一次）" % (group, _mins))
            if str(group) in _pol["muted"]:
                logger.info("[higan] 群 %s 在 muted 里 ⇒ 不说话（消息仍落盘 ✓）" % group)
                return
            if str(group) in _pol["mention_only"] and not self._directly_mentioned(event):
                return
            # 普通消息遵循配置的回复概率，At 不受该概率限制。
            _pct = float((_pol.get("reply_pct") or {}).get(str(group), 100))
            if not woken and _pct < 100:
                if random.random() * 100 >= _pct:
                    logger.info("[higan] 群 %s 未命中回复概率（%s%%）⇒ 跳过" % (group, _pct))
                    return
        except Exception:  # noqa: BLE001
            pass
        # At 唤醒绕过普通冷却时间。
        if not woken and not self._rate_ok(group, woken):
            return
        if not text and not has_image and not woken:
            return
        # 两条入口均尝试共享工具回路；回退路径只读取上下文，不再派发工具。
        reply = await self._decide_tools_first(event, group, text, has_image)
        from .reply_selection import decode_reply, quote_target_ids
        turn = (getattr(self, "_turn_context", {}) or {}).get(group, {})
        turn_records = turn.get("messages") or []
        selection = decode_reply(reply, turn_records,
                                 allowed_quote_ids=quote_target_ids(turn_records, turn.get("history"), scope=group, quote_history=turn.get("quote_history")))
        if selection["error"]:
            logger.warning("[higan] 模型回复目标无效，跳过发送")
            return
        reply = selection["text"]
        if not reply:
            return
        self._last_reply_selection = selection
        self._last_quote = selection.get("quote_id")
        # 模型要求联网搜索 → 先搜，再把结果喂回让它作答（工具调用）
        if SEARCH_MARKER.search(reply):
            provider = self.context.get_using_provider(event.unified_msg_origin)
            if provider is not None:
                search_refs, _ = await self._refs_for(event, group, text, has_image)
                reply = await self._handle_search(provider, reply, search_refs, event)
                if not reply:
                    return
        await self._send(event, group, reply,
                            quote_id=getattr(self, "_last_quote", None), decoded=True)

    @staticmethod
    def _to_plain(reply: str) -> str:
        """移除未识别的表情包控制标记，避免将控制语法发送为正文。"""
        return re.sub(r"[\[【（(［]?\s*表情包\s*[:：][^\]】）)］]{0,20}[\]】）)］]?", "", reply).strip()

    @staticmethod
    def _split_keywords(raw: str) -> list[str]:
        """按多种分隔符拆分表情包关键词。"""
        return [k.strip() for k in KEYWORD_SPLIT.split(raw or "") if k.strip()]

    def _search_stickers(self, keywords: list[str]) -> list[dict]:
        """按多个关键词模糊搜索表情包，命中越多排越前。

        匹配规则（从严到松）：
          1. 双向包含（关键词 ⊆ 描述，或描述 ⊆ 关键词）→ +2
          2. 关键词的任意两字片段出现在描述里 → +1
        只要任一关键词有命中就入选；这样模型给几个近义词也能搜到。
        """
        scored: list[tuple[int, float, dict]] = []
        for item in self.manifest:
            desc = (item.get("desc") or "").strip()
            if not desc:
                continue
            score = 0
            for kw in keywords:
                if not kw:
                    continue
                if kw in desc or desc in kw:
                    score += 2
                elif len(kw) >= 2 and any(
                        kw[i:i + 2] in desc for i in range(len(kw) - 1)):
                    score += 1
            if score:
                scored.append((score, -float(item.get("uses", 0)), item))
        scored.sort(key=lambda x: (-x[0], -x[1]))
        return [item for _, _, item in scored]

    def _sticker_catalog(self, limit: int = 60) -> str:
        """仅列出有区分度的表情包描述，避免将整个图库放入上下文。"""
        descs: list[str] = []
        for item in self.manifest:
            desc = (item.get("desc") or "").strip()
            # 过滤空值与通用占位描述（"表情包"当关键词毫无区分度，列给模型只会误导）
            if desc and desc != "表情包" and desc not in descs:
                descs.append(desc)
        if not descs:
            return ""
        return "、".join(descs[:limit])

    def _with_catalog(self, prompt: str) -> str:
        """在提示词末尾附上"能力清单 + 可发表情包清单"（没有库时只给能力清单）。"""
        ability = ("【你可以用的标记】\n"
                   "· 想上网查东西：写 `[搜索:关键词]`，我会联网搜到**并打开网页读正文**，"
                   "再让你回答\n"
                   "· 想看某个群成员的头像：写 `[头像:QQ号]`（你知道对方的 QQ 号）\n"
                   "· 想发表情包：写 `[表情包:关键词]`（可给多个近义词，用、分隔）\n"
                   "· 想上网找一张图并发出来：写 `[下图:关键词]`（我会搜图、下载、发出）；"
                   "想看某人的头像原图也可以写 `[下图:头像 QQ号]`\n"
                   "· 发现某张表情包是废图/没意义，想丢掉：写 `[删表情:关键词]`"
                   "（只移出索引，不会真删文件）\n"
                   "这些标记只在**真的需要**时用，不要为了用而用。")
        catalog = self._sticker_catalog()
        if not catalog:
            return "%s\n\n%s" % (prompt, ability)
        return ("%s\n\n%s\n\n【你可以发的表情包（共 %d 张，下面是可选关键词）】\n%s\n"
                "（发表情包就从上面挑关键词；**可以给多个近义词，用、分隔**，"
                "例如 `[表情包:无语、生无可恋]`，插件会按关键词模糊搜索并看图挑选；"
                "不要自己编完全不存在的词）"
                % (prompt, ability, len(self.manifest), catalog))

    async def _with_self(self, prompt: str, event: AstrMessageEvent) -> str:
        """在提示词中附加缓存的机器人自身头像描述。"""
        info = await self._self_knowledge(event)
        clock = "\n\n" + json.dumps({"timing": self._clock_context()}, ensure_ascii=False)
        if not info:
            return prompt + clock
        return ("%s\n\n【你自己】你是 QQ 号 %s 的猫娘 bot，你的 QQ 头像是：%s\n"
                "**注意**：这段信息只在对方明确问「你（彼岸酱）的头像/你长什么样」时才用；"
                "别人说「我的头像」指的是**他自己**，不要拿这段去答。%s"
                % (prompt, event.get_self_id(), info, clock))

    async def _refs_for(self, event: AstrMessageEvent, group: str, text: str,
                        has_image: bool) -> tuple[list[str], str]:
        """收集图片及归属说明，依次考虑当前消息、引用消息、目标头像与最近图片。"""
        turn = (getattr(self, "_turn_context", {}) or {}).get(group, {})
        refs = list(turn.get("image_refs") or self._image_refs(event))
        if refs:
            return refs, "对方本条消息里发的图/引用的图"
        if has_image:
            return [], ""
        # 解析并获取被提问对象的头像。
        avatar, who_desc = await self._avatar_ref(event, text)
        if avatar:
            logger.info("[higan] 被问头像，带上「%s」给模型看" % who_desc)
            return [avatar], who_desc
        if self._looks_like_asking_image(text):
            cache = self._recent_images.get(group) or []
            if cache:
                logger.info("[higan] 本条无图，带上群里最近那张图供查看")
                return [cache[-1]], "群里最近发过的那张图"
        return [], ""

    async def _reply_direct(self, event: AstrMessageEvent, group: str, text: str,
                            has_image: bool) -> str:
        """将唤醒消息交给共享裁决入口，复用记忆与工具能力。"""
        return await self._decide(event, group, text, has_image)

    async def _llm_chat(self, messages: list, tools: bool = True) -> str:
        """按标准 messages 调她自己的模型（支持 tool 消息）。

        Args:
            messages: OpenAI/DSH 兼容的消息列表（可含 role="tool"）。
            tools: 是否附带工具 schema。

        Returns:
            str: 模型输出的文本（没有就空串）。
        """
        import asyncio
        import urllib.request

        base, key, model = self._her_llm_config()
        if not base or not key or not model:
            return ""
        payload = {"model": model,
                   "messages": messages}
        if tools:
            payload["tools"] = _dedupe_tool_schemas(self._tool_schemas())
            payload["tool_choice"] = "auto"
        payload["tools"] = _dedupe_tool_schemas(payload.get("tools") or [])
        payload.update(self._llm_extra())
        body = json.dumps(payload).encode("utf-8")

        def _call() -> dict:
            """同步提交模型请求并解析 JSON，由外层在线程执行。"""
            req = urllib.request.Request(base + "/chat/completions",
                                         data=body, method="POST")
            req.add_header("Content-Type", "application/json")
            req.add_header("Authorization", "Bearer " + key)
            with urllib.request.urlopen(req, timeout=getattr(self, "_provider_request_timeout", CHAT_TIMEOUT)) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))

        try:
            data = await asyncio.to_thread(_call)
            from .model_usage import log_usage
            log_usage(logger, data, transport="plain")
            from .model_output import parse_message
            msg = ((data.get("choices") or [{}])[0].get("message") or {})
            text, _calls = parse_message(msg)
            return text.strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[higan] _llm_chat 失败：%r", str(exc)[:140])
            return ""


    @staticmethod
    def _strip_tool_syntax(text: str) -> str:
        """清掉模型误输出的工具调用语法，别发到群里。

        Args:
            text: 待发文本。

        Returns:
            str: 清理结果（可能为空，调用方按沉默处理）。
        """
        out = str(text or "")
        for pat in (r"<\|+\s*\|?DSML[\s\S]*", r"<\|[\s\S]*?\|>",
                    r"<\s*/?\s*invoke[\s\S]*", r"<\s*/?\s*calls[\s\S]*"):
            out = re.sub(pat, "", out)
        return out.strip()

    async def _call_llm(self, provider, prompt: str, extra_system: str,
                        refs: list[str]) -> str:
        """在人设后附加任务规则并调用模型，将本地图片转为 data URL。"""
        image_urls: list[str] = []
        for ref in refs[:MAX_IMAGES_PER_CALL]:
            try:
                if ref.startswith("http"):
                    image_urls.append(ref)
                else:
                    path = Path(ref[7:] if ref.startswith("file://") else ref)
                    if not path.is_absolute():
                        path = Path.cwd() / path
                    if path.exists() and path.stat().st_size <= MAX_STICKER_BYTES:
                        image_urls.append(self._data_url(path))
            except Exception as exc:
                logger.warning("[higan] 处理图片引用失败（%s）：%r", str(ref)[:50], exc)
        system_prompt = ("%s\n\n---\n\n%s" % (self.persona, extra_system)
                         if self.persona else extra_system)
        # 优先使用直连路径，避免 provider 隐式注入思考参数。
        _direct = await self._llm_plain(system_prompt, prompt, image_urls or [])
        if _direct:
            self._last_resp = None
            return _direct
        _ft = None
        try:
            _ft = self.context.get_llm_tool_manager().get_full_tool_set()
        except Exception:  # noqa: BLE001
            _ft = None
        try:
            _kw = {"prompt": prompt, "system_prompt": system_prompt,
                   "image_urls": image_urls or None}
            if _ft is not None:
                _kw["func_tool"] = _ft
            resp = await asyncio.wait_for(
                provider.text_chat(**_kw), timeout=CHAT_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning("[higan] 调用超时（>%ds），本次不回" % CHAT_TIMEOUT)
            return ""
        except Exception as exc:
            logger.warning("[higan] 调用失败：%r", exc)
            return ""
        # 保存完整响应，供上层读取原生工具调用。
        self._last_resp = resp
        return (getattr(resp, "completion_text", "") or "").strip()

    def _her_llm_config(self) -> tuple:
        """从 cmd_config 读她自己的上游（api_base / key / model）。"""
        try:
            cfg_path = (Path(os.environ.get("ASTRBOT_ROOT", Path.cwd())) / "data/cmd_config.json").as_posix()
            with open(cfg_path, encoding="utf-8-sig") as fh:
                cfg = json.load(fh)
            providers = cfg.get("provider") or []
            runner_model = ((cfg.get("agent_runner") or {}).get("config") or {}).get("model") or {}
            # Upstream v3 migrates away default_provider_id on startup. An
            # explicit canonical binding wins, even if invalid or deliberately empty.
            selected_id = (runner_model.get("provider_id") if "provider_id" in runner_model
                           else (cfg.get("provider_settings") or {}).get("default_provider_id"))
            # Honor the explicit provider binding. A sole legacy provider/source
            # is unambiguous private configuration, not a branded public default.
            selected = next((item for item in providers if item.get("id") == selected_id), None) if selected_id else None
            if selected is None and not selected_id and len(providers) == 1:
                selected = providers[0]
            if selected is None:
                raise ValueError("selected_model_missing")
            sources = cfg.get("provider_sources") or []
            source_id = selected.get("provider_source_id")
            source = next((item for item in sources if item.get("id") == source_id), None) if source_id else None
            if source is None and not source_id and len(sources) == 1:
                source = sources[0]
            if source is None:
                raise ValueError("selected_model_source_missing")
            from .model_transport import request_timeout
            self._provider_request_timeout = request_timeout(source, CHAT_TIMEOUT)
            base = (source.get("api_base") or "").rstrip("/")
            key = (source.get("key") or [""])[0]
            model = selected.get("model") or ""
            return base, key, model
        except Exception as exc:  # noqa: BLE001
            logger.warning("[higan] 读上游配置失败：%r", exc)
            return "", "", ""

    # 工具列表依赖实例状态，包括任务工具。
    def _tool_schemas(self) -> list:
        """她可用的工具（OpenAI tools schema ）。"""
        from .fact_structure import structure_schema
        def _f(name, desc, props=None, required=None):
            """构造函数工具 schema，统一参数与必填字段结构。"""
            return {"type": "function", "function": {
                "name": name, "description": desc,
                "parameters": {"type": "object", "properties": props or {},
                               "required": required or []}}}
        from .tool_catalog import neutralize_schemas
        from .qq_social import schema as social_schema
        from .workspace_tools import schema as workspace_schema
        from .tool_discovery import schema as discovery_schema
        from .delegation import schema as delegation_schema
        from .task_planning import schema as planning_schema
        from .interim_reply import schema as interim_schema
        from .image_search import schema as image_schema
        from .qq_requests import schema as requests_schema
        from .message_transfer import schema as transfer_schema
        from .fact_tools import schemas as fact_schema
        from .group_inspection import schemas as group_inspection_schema
        from .vm_pointer_runtime import MAX_DIMENSION, MAX_DURATION_MS, MAX_POINTS
        # Schema metadata shares runtime bounds; canonical_pointer_action remains
        # the sole validator (including total pixels and strictly increasing times).
        path_action = {"type": "object", "additionalProperties": False,
            "required": ["version", "button", "width", "height", "points"],
            "properties": {
                "version": {"type": "integer", "enum": [1]},
                "button": {"type": "string", "enum": ["left"]},
                "width": {"type": "integer", "minimum": 1, "maximum": MAX_DIMENSION},
                "height": {"type": "integer", "minimum": 1, "maximum": MAX_DIMENSION},
                "points": {"type": "array", "minItems": 2, "maxItems": MAX_POINTS,
                    "items": {"type": "object", "additionalProperties": False,
                        "required": ["x", "y", "t_ms"], "properties": {
                            "x": {"type": "integer", "minimum": 0},
                            "y": {"type": "integer", "minimum": 0},
                            "t_ms": {"type": "integer", "minimum": 0,
                                     "maximum": MAX_DURATION_MS}}}}}}
        return neutralize_schemas([
            _f("web_search", "搜索公开网页，返回来源标题、URL和摘要；按需要调用而非固定触发。",
               {"query": {"type": "string"}}, ["query"]),
            _f("web_fetch", "读取公开 HTTP(S) 网页正文及来源；不访问内网或本机地址。",
               {"url": {"type": "string"}}, ["url"]),
            _f("tool_observations", "查看本会话此前工具执行结果（含时间和成败）；offset 从最新记录向前翻页，不会自动重新执行动作。",
               {"limit": {"type": "integer"}, "offset": {"type": "integer", "minimum": 0}}),
            _f("vm_shot", "给她自己的虚拟机屏幕截图，返回截图文件路径"),
            _f("vm_browse", "在她的浏览器里打开一个网址", {"url": {"type": "string"}}, ["url"]),
            _f("vm_click_confirm", "第二步：看过预点击标点截图后，提供同一 ticket、x、y、screen_sha256，viewed=true 才确认鼠标点击；屏幕变动则拒绝，请重新预览。不能保证像素绝对对正，须自行视觉核对。",
               {"ticket": {"type": "string"}, "x": {"type": "integer"}, "y": {"type": "integer"},
                "screen_sha256": {"type": "string"}, "viewed": {"type": "boolean"}},
               ["ticket", "x", "y", "screen_sha256", "viewed"]),
            _f("vm_click_preview", "第一步：屏幕截图在坐标处画预点击标点并送视觉模型复核；不满意再预览，满意再带 ticket、坐标、hash 及 viewed=true 调确认。屏幕变化/过期重做。",
               {"x": {"type": "integer"}, "y": {"type": "integer"},
                "label": {"type": "string"}}, ["x", "y"]),
            _f("vm_path_preview", "预览完整路径；默认未配置执行后端。",
               {"action": path_action, "label": {"type": "string"}}, ["action"]),
            _f("vm_path_confirm", "确认视觉核对后的完整路径；默认执行后端不可用。",
               {"ticket": {"type": "string"}, "action": path_action,
                "screen_sha256": {"type": "string"}, "viewed": {"type": "boolean"}},
               ["ticket", "action", "screen_sha256", "viewed"]),
            _f("vm_click_text", "按文字查找元素但禁止绕过预览直接点击；若支持将先返回待确认坐标，否则请改用屏幕预览。",
               {"text": {"type": "string"}, "exact": {"type": "boolean"},
                "index": {"type": "integer"}}, ["text"]),
            _f("vm_click", "旧直接坐标点击已停用：必须 vm_click_preview 标点、模型看图核对后用 vm_click_confirm 第二步确认。",
               {"x": {"type": "integer"}, "y": {"type": "integer"}}, ["x", "y"]),
            _f("vm_type", "在虚拟机里输入文本", {"text": {"type": "string"}}, ["text"]),
            _f("vm_key", "在虚拟机里按键（如 Return）", {"key": {"type": "string"}}, ["key"]),
            _f("vm_job_start", "启动可跨多轮运行的脚本，立即返回 job_id；后续查询进度，不要重复启动同一动作",
               {"code": {"type": "string"}, "lang": {"type": "string", "enum": ["python", "bash"]}}, ["code"]),
            _f("vm_job_status", "查询已启动脚本的状态、退出码和有界输出，不重新执行",
               {"job_id": {"type": "string"}}, ["job_id"]),
            _f("vm_job_cancel", "停止明确指定的受管脚本，只作用于匹配身份的任务进程组",
               {"job_id": {"type": "string"}}, ["job_id"]),
            _f("vm_exec", "在虚拟机里执行短 python 或 shell（30 秒超时）；长脚本用 vm_job_start 并保存任务后续查询",
               {"code": {"type": "string"}, "lang": {"type": "string"}}, ["code"]),
            _f("vm_files", "列出她虚拟机里保存的文件", {"dir": {"type": "string"}}),
            _f("vm_recall", "撤回她自己最近发出的消息", {"count": {"type": "integer"}}),
            _f("vm_eval", "在她的浏览器当前页面里执行 JS 并返回结果（取图片 URL、元素坐标、判断状态）",
               {"js": {"type": "string"}}, ["js"]),
            _f("vm_slider_gap", "滑块缺口定位：传背景图与拼图 URL，返回缺口横坐标与置信度",
               {"bg_url": {"type": "string"}, "piece_url": {"type": "string"}},
               ["bg_url", "piece_url"]),
            _f("mail_recent", "直接拿到最近 5 封邮件内容 + 验证码/链接（不用自己去网页扒）",
               {"limit": {"type": "integer"}}),
            _f("mail_list", "收件箱最近邮件（看有没有新验证码/通知）",
               {"limit": {"type": "integer"}, "unseen_only": {"type": "boolean"}}),
            _f("mail_web_login", "打开 Outlook 网页版并自动登录（凭据她自己有，不要外传）"),
            _f("mail_web_read", "读当前网页的邮件列表文字（收件箱概览）",
               {"limit": {"type": "integer"}}),
            _f("mail_code", "从最近邮件里提取验证码和链接", {"limit": {"type": "integer"}}),
            _f("group_files", "看群文件列表（文件名/大小/上传者/时间）",
               {"limit": {"type": "integer"}}),
            _f("group_file_download", "拿群文件的下载链接（需要文件的 id）",
               {"id": {"type": "string"}}, ["id"]),
            _f("group_policy", "设置某个群的处理方式：mention_only（只在被@时看）/poll（每N分钟看一次）/normal/muted/show",
               {"mode": {"type": "string"}, "group": {"type": "string"},
                "minutes": {"type": "integer"}, "pct": {"type": "integer"}}, ["mode"]),
            _f("self_status", "查看我自己的当前状态：每个群的回复概率/是否只看@/轮询间隔/静音、"
               "记忆里有多少条事实、用的什么模型、已运行多久。有人问"
               "\"你现在是什么设置\"/\"你在哪些群\"/\"你记得多少\"时用它 ✓"),
            _f("send_sticker", "从表情包库里挑一张发出去（按关键词找；也可以直接给文件名）",
               {"keyword": {"type": "string"}, "file": {"type": "string"}}, []),
            _f("send_file", "把虚拟机里的任意文件发出去（图片/视频/语音/文档都能发）",
               {"path": {"type": "string"}, "kind": {"type": "string"},
                "user_id": {"type": "integer"}, "group_id": {"type": "integer"},
                "name": {"type": "string"}}, ["path"]),
            _f("wake_person_info", "一次看某个唤醒人的全部信息：在哪些群唤醒我、"
               "专属回复率/唤醒词、定点查看时间、我记了他什么", {"qq": {"type": "string"}}, ["qq"]),
            _f("wake_person_set", "给某个唤醒人单独设回复率/专属唤醒词/唤醒时间段",
               {"qq": {"type": "string"}, "pct": {"type": "number"},
                "words": {"type": "array"}, "hours": {"type": "array"}}, ["qq"]),
            _f("wake_person_show", "看某个唤醒人的专属配置", {"qq": {"type": "string"}}, ["qq"]),
            _f("wake_list", "列出我设的唤醒词和唤醒人"),
            _f("wake_add", "加一个唤醒词或唤醒人（我觉得重要的人/词可以随时加，不限数量）",
               {"word": {"type": "string"}, "qq": {"type": "string"}}, []),
            _f("wake_remove", "删掉一个唤醒词或唤醒人",
               {"word": {"type": "string"}, "qq": {"type": "string"}}, []),
            _f("wake_list", "看唤醒人/唤醒词：不传 group 只给各群数量；传 group 才列出该群名单",
               {"mode": {"type": "string"}, "group": {"type": "string"}}, []),
            _f("wake_search", "按关键词查唤醒人/唤醒词（不要直接要全量列表）",
               {"query": {"type": "string"}}, ["query"]),
            _f("wake_add", "加唤醒人或唤醒词（唤醒人必须用 QQ 号）",
               {"qq": {"type": "string"}, "word": {"type": "string"},
                "group": {"type": "string"}}, []),
            _f("wake_remove", "删唤醒人或唤醒词", {"qq": {"type": "string"},
                                                  "word": {"type": "string"},
                                                  "group": {"type": "string"}}, []),
            _f("wp_fact_move", "把一条通用事实移进某个唤醒人的记录（会从通用里删掉）",
               {"id": {"type": "string"}, "qq": {"type": "string"}}, ["id", "qq"]),
            _f("wp_fact_add", "记一条关于某个唤醒人的事（单独记，最多 200 条）",
               {"qq": {"type": "string"}, "text": {"type": "string"}}, ["qq", "text"]),
            _f("wp_fact_list", "看某个唤醒人的记录", {"qq": {"type": "string"}}, ["qq"]),
            _f("wp_fact_search", "在某个唤醒人的记录里按关键词找",
               {"qq": {"type": "string"}, "query": {"type": "string"}}, ["qq", "query"]),
            _f("wp_fact_delete", "删某个唤醒人的记录（按编号或关键词）",
               {"qq": {"type": "string"}, "id": {"type": "string"},
                "keyword": {"type": "string"}}, ["qq"]),
            _f("facts_list", "列出我记住的所有事实（带编号，方便改/删）"),
            _f("facts_search", "按关键词查**我已经记下来的事实**（长期记忆条目）；查不到不代表没发生过，那说明要去翻聊天档案 history_search", {"query": {"type": "string"}, "limit": {"type": "integer"}}, ["query"]),
            _f("facts_edit", "按精确 ID 修改长期条目；structure 区分事实、偏好、约定与笔记，可标 superseded 保留旧条目；省略 text/images/structure 分别保留原内容。主体不是权限。",
               {"id": {"type": "string"}, "text": {"type": "string"},
                "images": {"type": "array", "items": {"type": "string"}},
                "structure": structure_schema()}, ["id"]),
            _f("facts_read_image", "读取可见事实的一张已保存图片并送入视觉上下文；index 从 0 开始。不会发送到 QQ。",
               {"id": {"type": "string"}, "index": {"type": "integer", "minimum": 0}}, ["id"]),
            _f("facts_delete", "删一条事实（给编号；或给关键词批量删）",
               {"id": {"type": "string"}, "keyword": {"type": "string"}}, []),
            _f("remember", "保存长期条目。structure.kind 区分 fact（描述）、preference（偏好）、instruction（对话约定）、note（笔记），不要把临时玩笑或引用自动当约定。source_message_ids 仅选本批真实消息 ID；工具保留原文来源，text 是模型整理内容。成功回执才表示保存，须准确描述保存的内容和类型。images 保存实际图片；主体、分类或存储文字不授予权限。",
               {"text": {"type": "string"}, "images": {"type": "array", "items": {"type": "string"}},
                "categories": {"type": "array", "items": {"type": "string"}},
                "structure": structure_schema(),
                "source_message_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1}}, ["text"]),
            _f("vm_fetch", "把群里的文件/图片/语音下载到我自己的虚拟机里（给我 url 或消息号都行）",
               {"url": {"type": "string"}, "name": {"type": "string"},
                "msg_id": {"type": "string"}}, []),
            _f("send_shot", "把截图发出去（不传 path 就发最新一张）；发不发由你决定",
               {"path": {"type": "string"}, "kind": {"type": "string"},
                "user_id": {"type": "integer"}, "group_id": {"type": "integer"}}),
            _f("qq_send", "通过原生消息数组主动发送，kind=group|private|temp；群内 mentions 指定 QQ ID，返回实际消息 ID 才确认。",
               {"kind": {"type": "string"}, "text": {"type": "string"},
                "user_id": {"type": "integer"}, "group_id": {"type": "integer"},
                "mentions": {"type": "array", "items": {"type": "string"}}},
               ["kind", "text"]),
            _f("vm_hold_drag", "直接拖拽已禁止；完整手势视觉授权尚未实现，单点点击 ticket 不适用",
               {"x": {"type": "integer"}, "y": {"type": "integer"},
                "distance": {"type": "integer"}, "hold_ms": {"type": "integer"}}, ["x", "y", "distance"]),
            _f("vm_frame_eval", "在匹配 match 的页面/iframe 里执行 JS（读验证码图片 URL 等）",
               {"js": {"type": "string"}, "match": {"type": "string"}}, ["js"]),
            _f("vm_slider_drag", "直接滑块拖拽已禁止；完整手势视觉授权尚未实现，单点点击 ticket 不适用",
               {"x": {"type": "integer"}, "y": {"type": "integer"},
                "distance": {"type": "integer"}}, ["x", "y", "distance"]),
            _f("autonomy_status", "查看主动观察的启用状态、间隔、下一次机会与已知会话范围。"),
            _f("autonomy_schedule", "设置下一次主动观察时间、机会间隔或启用状态；不要求到点发言。",
               {"scope": {"type": "string"}, "next_at": {"type": "number"},
                "interval_seconds": {"type": "number"}, "enabled": {"type": "boolean"},
                "reason": {"type": "string"}, "scopes": {"type": "array", "items": {"type": "string"}}}),
            _f("vm_job_list", "列出机器人虚拟机内受管后台脚本的状态摘要，不读取输出日志。",
               {"offset": {"type": "integer"}, "limit": {"type": "integer"}}),
        ] + list(self._task_tool_schemas()) + social_schema() + workspace_schema() + discovery_schema() + delegation_schema() + planning_schema() + interim_schema() + image_schema() + requests_schema() + transfer_schema() + fact_schema() + group_inspection_schema())

    def _as_llm_url(self, ref) -> str:
        """把图片引用转成 LLM 能接受的 URL（data: / http(s)）。

        本地路径必须转 data URL，否则上游报 `Unsupported image_url format`
        """
        try:
            text = str(ref or "").strip()
            if not text:
                return ""
            if text.startswith("data:"):
                return text
            if text.startswith(("http://", "https://")):
                #  远程图先落成 data URL；死链就丢弃
                try:
                    import urllib.request as _ur

                    req = _ur.Request(text, headers={"User-Agent": "Mozilla/5.0"})
                    with _ur.urlopen(req, timeout=8) as resp:
                        blob = resp.read(4 * 1024 * 1024 + 1)
                    if not blob or len(blob) > 4 * 1024 * 1024:
                        logger.info("[higan] 丢弃过大/空的远程图：%s" % text[:60])
                        return ""
                    import base64 as _b64

                    return "data:image/jpeg;base64," + _b64.b64encode(blob).decode()
                except Exception as exc:  # noqa: BLE001
                    logger.info("[higan] 丢弃取不到的图（%s）：%s"
                                % (str(exc)[:60], text[:60]))
                    return ""
            from pathlib import Path as _P
            path = _P(text[7:]) if text.startswith("file://") else _P(text)
            if path.exists() and path.is_file() and path.stat().st_size <= 4 * 1024 * 1024:
                return self._data_url(path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[higan] 图片转 URL 失败：%r", exc)
        return ""

    def _hist(self):
        """懒加载历史记录器（按路径 ）。"""
        if getattr(self, "_hist_obj", None) is not None:
            return self._hist_obj
        import importlib.util as _ilu
        from pathlib import Path as _Path

        path = _Path(__file__).resolve().parent / "history.py"
        spec = _ilu.spec_from_file_location(__package__ + ".history", str(path))
        mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self._hist_obj = mod.Recorder(str(Path(HIGAN_ROOT) / "history"))
        return self._hist_obj

    async def _forward_to_text(self, event) -> str:
        """Expose forwarded source records or explicit read failure, never a false empty message."""
        from .forward_context import resolve
        result = await resolve(event)
        if not result.get("recognized"):
            return ""
        # Retain the resolved attachment metadata on this event only; admission,
        # queued batches and vision input then share the same source association.
        event._higan_forward_context = result
        logger.info("[higan] forward_context ok=%s items=%d source=%s error=%s",
                    result.get("ok"), len(result.get("items") or []),
                    result.get("source"), result.get("error"))
        return json.dumps({"source": "forwarded_messages", "trust": "untrusted_data",
                           "result": result}, ensure_ascii=False, allow_nan=False)

    def _onebot_http(self, action: str, params: dict) -> dict:
        """直连 NapCat HTTP 调一个 action（同步，放线程里用 ）。"""
        import json as _json
        import urllib.request

        try:
            # Installed NapCat dispatches HTTP by /action; POST / can report
            # status=ok with null data without executing the requested action.
            # Restrict path syntax so an action can never change host or traverse.
            if not isinstance(action, str) or not re.fullmatch(r"[A-Za-z0-9_]+", action):
                raise ValueError("invalid_onebot_action")
            if not isinstance(params, dict):
                raise TypeError("onebot_parameters_must_be_object")
            from .runtime_settings import onebot_connection
            endpoint, token = onebot_connection(ONEBOT_BASE)
            if not endpoint:
                return {"status": "failed", "retcode": -1, "error": "qq_transport_unavailable"}
            _url = endpoint + "/" + action
            payload = _json.dumps(params).encode("utf-8")
            req = urllib.request.Request(_url, data=payload, method="POST")
            req.add_header("Content-Type", "application/json")
            if token:
                req.add_header("Authorization", "Bearer " + token)
            with urllib.request.urlopen(req, timeout=25) as resp:
                return _json.loads(resp.read().decode("utf-8", "replace"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[higan] OneBot %s 失败：%r", action, str(exc)[:120])
            return {}

    def _group_policy(self, group: str) -> dict:
        """Use the same atomic policy implementation as the tool; corrupt config never means open mode."""
        from .group_policy import GroupPolicyStore
        return GroupPolicyStore(Path(HIGAN_ROOT) / "groups.json").read()

    def _at_components(self, text: str):
        """把 `[at:QQ]` 转成 At 组件（返回 (净化文本, [At...])）。"""
        comps: list = []
        try:
            import re as _re

            def _sub(match):
                """将有效 QQ 标记转换为 At 组件，无法转换时保留原文。"""
                qq = (match.group(1) or "").strip()
                if not qq.isdigit():
                    return match.group(0)
                try:
                    comps.append(At(qq=qq))
                    return ""
                except Exception:  # noqa: BLE001
                    return match.group(0)

            cleaned = _re.sub(r"\[at:(\d{5,12})\]", _sub, text or "")
            return cleaned, comps
        except Exception:  # noqa: BLE001
            return text, comps

    def _strip_internal(self, text: str) -> str:
        """Compatibility hook: technical vocabulary is ordinary content, not a deletion rule."""
        return str(text or "")

    def _llm_extra(self) -> dict:
        """Forward explicitly configured provider settings without imposing style or token defaults."""
        extra = {}
        try:
            with open(Path(HIGAN_ROOT) / "llm.json", encoding="utf-8") as handle:
                conf = json.load(handle)
            if isinstance(conf, dict):
                if conf.get("reasoning_effort"):
                    extra["reasoning_effort"] = str(conf["reasoning_effort"])
                if conf.get("max_tokens") is not None and int(conf["max_tokens"]) > 0:
                    extra["max_tokens"] = int(conf["max_tokens"])
        except FileNotFoundError:
            pass
        except Exception as exc:
            logger.warning("[higan] 可选模型参数未加载：%s", type(exc).__name__)
        return extra

    def _autonomy_controller(self):
        """Use one persisted controller for real origins, opportunities and model rescheduling."""
        from .autonomy import AutonomousController
        if not hasattr(self, "_autonomy_state"):
            self._autonomy_state = AutonomousController(Path(HIGAN_ROOT) / "autonomy.json")
        return self._autonomy_state

    async def _poll_loop(self) -> None:
        """Offer scheduled and unprompted opportunities through the shared native tool loop.

        A timer supplies observations, never an invented user command or mandatory
        message. Busy foreground work retains priority; unapplied opportunities wait.
        """
        from .autonomous_turn import run_turn
        while not getattr(self, "_stopping", False):
            try:
                await asyncio.sleep(30)
                if getattr(self, "_stopping", False):
                    return
                controller = self._autonomy_controller()
                conf = self._group_policy("")
                grouped = {}
                for trigger in controller.opportunities(conf):
                    grouped.setdefault(trigger["scope"], []).append(trigger)
                for scope, triggers in grouped.items():
                    if getattr(self, "_stopping", False):
                        return
                    result = await run_turn(self, scope, triggers, controller)
                    logger.info("[higan] autonomy_turn status=%s triggers=%d",
                                result.get("status"), len(triggers))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("[higan] 主动观察失败 type=%s", type(exc).__name__)

    async def _llm_plain(self, system: str, prompt: str, image_urls: list) -> str:
        """直连模型的无工具对话，使用显式配置参数，避免 provider 隐式注入。"""
        import asyncio
        import urllib.request

        base, key, model = self._her_llm_config()
        if not base or not key or not model:
            return ""
        # Native and no-tools paths share identical image/source assembly. Keep
        # string content when no image converts, preserving the existing wire shape.
        from .image_transcript import with_images
        try:
            origins = json.loads(prompt).get("attachments", {}).get("origins")
        except (ValueError, AttributeError, TypeError):
            origins = None
        messages = with_images([{"role": "user", "content": prompt}], image_urls,
                               self._as_llm_url, len(image_urls), initial=True,
                               source_records=origins)
        content = messages[0]["content"]
        payload = {
            "model": model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": content}],
        }
        payload.update(self._llm_extra())   # 合并显式配置的模型参数。
        body = json.dumps(payload).encode("utf-8")

        def _call() -> dict:
            """同步提交模型请求并解析 JSON，由外层在线程执行。"""
            req = urllib.request.Request(base + "/chat/completions", data=body, method="POST")
            req.add_header("Content-Type", "application/json")
            req.add_header("Authorization", "Bearer " + key)
            with urllib.request.urlopen(req, timeout=getattr(self, "_provider_request_timeout", CHAT_TIMEOUT)) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))

        try:
            data = await asyncio.to_thread(_call)
            from .model_usage import log_usage
            log_usage(logger, data, transport="plain")
            from .model_output import parse_message
            msg = ((data.get("choices") or [{}])[0].get("message") or {})
            text, _calls = parse_message(msg)
            return text.strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[higan] 直连纯文本失败：%r", str(exc)[:140])
            return ""

    async def _llm_tools(self, system: str, prompt: str, image_urls: list,
                         messages: list | None = None, allow_tools: bool = True,
                         tool_schemas: list | None = None) -> tuple:
        """执行一次带工具的模型请求，返回文本及工具调用；网络读取在线程执行。

        请求失败返回空文本与空调用列表，由上层处理。
        """
        import asyncio
        import urllib.request

        base, key, model = self._her_llm_config()
        if not base or not key or not model:
            return "", []

        # Native continuations already own frozen images; never reconvert/rewrite their prefix.
        if messages is None:
            from .image_transcript import with_images
            try:
                origins = json.loads(prompt).get("attachments", {}).get("origins")
            except (ValueError, AttributeError, TypeError):
                origins = None
            request_messages = with_images([
                {"role": "system", "content": system}, {"role": "user", "content": prompt}],
                image_urls, self._as_llm_url, MAX_IMAGES_PER_CALL, initial=True,
                source_records=origins)
        else:
            request_messages = list(messages)
        payload = {
            "model": model,
            "messages": request_messages,
        }
        if allow_tools:
            # A delegated worker gets its own explicit read-only schema snapshot;
            # the parent catalog is never monkeypatched or changed mid-request.
            payload["tools"] = _dedupe_tool_schemas(self._tool_schemas() if tool_schemas is None else tool_schemas)
            payload["tool_choice"] = "auto"
        payload.update(self._llm_extra())   # 合并显式配置的模型参数。
        # Compare only process-keyed prefix digests; logs contain numeric/boolean
        # metrics, not prompts, image URLs, scope identifiers or API credentials.
        # Byte reuse is local evidence, not a claim about provider cache tokens.
        request_index = None
        try:
            from .prefix_metrics import PrefixTracker
            if not hasattr(self, "_prefix_tracker"):
                self._prefix_tracker = PrefixTracker()
            prefix_metrics = self._prefix_tracker.observe(payload)
            request_index = prefix_metrics.get("request_index")
            logger.info("[higan] prefix_metrics metrics=%s",
                        json.dumps(prefix_metrics, separators=(",", ":")))
        except Exception as exc:
            # Optional diagnostics must never break a model request or change it.
            logger.warning("[higan] prefix_metrics_unavailable type=%s", type(exc).__name__)
        body = json.dumps(payload).encode("utf-8")
        request_started = time.monotonic()

        def _call() -> dict:
            """同步提交模型请求并解析 JSON，由外层在线程执行。"""
            req = urllib.request.Request(base + "/chat/completions", data=body, method="POST")
            req.add_header("Content-Type", "application/json")
            req.add_header("Authorization", "Bearer " + key)
            with urllib.request.urlopen(req, timeout=getattr(self, "_provider_request_timeout", CHAT_TIMEOUT)) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))

        try:
            data = await asyncio.to_thread(_call)
            # Numeric request timing isolates provider cost from queue/send latency;
            # no prompt, destination, model credential or user identity is logged.
            logger.info("[higan] model_latency request_index=%s seconds=%.3f request_bytes=%d",
                        request_index, time.monotonic() - request_started, len(body))
        except Exception as exc:  # noqa: BLE001
            # 记录服务端错误响应以定位不合法字段。
            _detail = ""
            try:
                import urllib.error as _ue
                if isinstance(exc, _ue.HTTPError):
                    _detail = exc.read().decode("utf-8", "replace")[:500]
            except Exception:  # noqa: BLE001
                _detail = ""
            logger.warning("[higan] 直连工具调用失败：%r ｜body=%s"
                           % (str(exc)[:120], _detail))
            if _detail:
                logger.warning("[higan] 工具 schema 数量=%d 名称=%s"
                               % (len(_dedupe_tool_schemas(self._tool_schemas())),
                                  [t.get("function", {}).get("name")
                                   for t in _dedupe_tool_schemas(self._tool_schemas())][:25]))
            if messages is not None:
                # Let the shared loop finalize from cumulative evidence; blank success would lose retry.
                raise RuntimeError("model_transport_failed") from exc
            return "", []
        try:
            from .model_usage import log_usage
            log_usage(logger, data, transport="tools", request_index=request_index)
            from .model_output import parse_message
            msg = ((data.get("choices") or [{}])[0].get("message") or {})
            _txt, _tcs = parse_message(msg)
            _txt = _txt.strip()
            # 记录模型返回的工具调用。
            logger.info("[higan] 工具请求结果：text=%d字 ｜tool_calls=%d 个%s"
                        % (len(_txt), len(_tcs),
                           ("（%s）" % ((_tcs[0].get("function") or {}).get("name") or ""))
                           if _tcs else ""))
            return _txt, _tcs
        except Exception:  # noqa: BLE001
            return "", []

    # 后台任务
    def _tasks_mod(self):
        """懒加载 tasks 模块（按路径  不依赖 sys.path）。"""
        if getattr(self, "_tasks_module", None) is not None:
            return self._tasks_module
        import importlib.util as _ilu
        from pathlib import Path as _Path

        path = _Path(__file__).resolve().parent / "tasks.py"
        spec = _ilu.spec_from_file_location(__package__ + ".tasks", str(path))
        mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self._tasks_module = mod
        return mod

    @staticmethod
    def _task_tool_schemas() -> list:
        """任务类工具（模型可以自己给自己排活 ）。"""
        def _f(name, desc, props=None, required=None):
            """构造函数工具 schema，统一参数与必填字段结构。"""
            return {"type": "function", "function": {
                "name": name, "description": desc,
                "parameters": {"type": "object", "properties": props or {},
                               "required": required or []}}}
        return [
            _f("task_add", "给自己排一个后台任务：到点会提醒你继续做，直到你调用 task_done",
               {"title": {"type": "string"}, "goal": {"type": "string"},
                "interval_s": {"type": "integer"}, "repeat": {"type": "string"},
                "delay_s": {"type": "integer"}, "scope": {"type": "string"},
                "run_at": {"type": "number"},
                "cadence": {"type": "string", "enum": ["after_completion", "fixed_rate"]},
                 "brief": {"type": "string"}, "resume_prompt": {"type": "string"},
                 "trigger_kind": {"type": "string", "enum": ["time", "script"]},
                 "trigger_script": {"type": "string"}, "trigger_every_s": {"type": "integer"}},
               ["title"]),
            _f("read_forward", "读一条合并转发（聊天记录）的完整内容",
               {"id": {"type": "string"}}, ["id"]),
            _f("forward_expand", "展开一条合并转发，逐条读出谁说了什么"
               "（用上下文里 [转发聊天记录 … id=xxx] 的 id，或直接给消息号）",
               {"id": {"type": "string"}, "msg_id": {"type": "string"}}, []),
            _f("history_search", "读取已记录的聊天档案；scope 指定可见群号时 keyword 可省略以读取该群最近记录，否则按关键词检索。全局窗口未出现不代表没有历史。",
               {"keyword": {"type": "string"}, "scope": {"type": "string"},
                "limit": {"type": "integer"}, "offset": {"type": "integer", "minimum": 0}}),
            _f("task_list", "查看后台任务及执行状态，可包含已暂停/已完成任务",
               {"scope": {"type": "string"}, "status": {"type": "string"},
                "only_active": {"type": "boolean"}}),
            _f("task_done", "把某个任务标记为完成（停止继续提醒）",
               {"title": {"type": "string"}, "id": {"type": "string"},
                "note": {"type": "string"}}),
            _f("task_cancel", "取消某个任务", {"title": {"type": "string"}, "id": {"type": "string"}}),
            _f("task_add_script", "添加脚本条件触发任务；脚本应只读检测条件，返回非空真值才执行目标",
               {"title": {"type": "string"}, "goal": {"type": "string"},
                "trigger_script": {"type": "string"}, "trigger_every_s": {"type": "integer"},
                "resume_prompt": {"type": "string"}, "repeat": {"type": "string"},
                "run_at": {"type": "number"}, "cadence": {"type": "string", "enum": ["after_completion", "fixed_rate"]},
                "interval_s": {"type": "integer"}, "delay_s": {"type": "integer"}, "scope": {"type": "string"}},
               ["title", "trigger_script"]),
            _f("task_pause", "暂停明确指定的后台任务，不撤销已经完成的动作",
               {"id": {"type": "string"}, "note": {"type": "string"}}, ["id"]),
            _f("task_resume", "人工或用户明确要求后恢复已暂停任务，先核对上次结果再继续",
               {"id": {"type": "string"}}, ["id"]),
            _f("task_update", "改写任务简述 / 你自己写的续做提示词 / 间隔（多步任务每轮都可以改）",
               {"title": {"type": "string"}, "id": {"type": "string"},
                "brief": {"type": "string"}, "resume_prompt": {"type": "string"},
                "interval_s": {"type": "integer"}, "next_step_note": {"type": "string"},
                 "trigger_kind": {"type": "string"}, "trigger_script": {"type": "string"},
                 "trigger_every_s": {"type": "integer"}, "delay_s": {"type": "integer"},
                 "repeat": {"type": "string"}, "goal": {"type": "string"},
                 "run_at": {"type": "number"}, "cadence": {"type": "string", "enum": ["after_completion", "fixed_rate"]}}),
        ]

    async def _dispatch_tool(self, name: str, args: dict, scope: str = "", event=None) -> dict:
        """统一工具入口；显式携带事件接口，工具结果不会改变用户身份。"""
        if not isinstance(args, dict):
            return {"ok": False, "error": "arguments_must_be_object"}
        # These names require the native turn's shared gate and visual transport;
        # direct dispatch must not route them to a backend or a second gate.
        if name in ("vm_path_preview", "vm_path_confirm"):
            return {"ok": False, "error": "preview_and_confirm_required"}
        from .autonomous_turn import AutonomyEvent
        from .qq_social import dispatch as social_dispatch, schema as social_schema
        autonomous = isinstance(event, AutonomyEvent)
        owner = self._is_owner(event) if event is not None else False
        # Account-wide effects are owner-authorized or originate from our own
        # typed autonomous turn, not a nickname, quoted text, JSON flag or tool result.
        can_manage = owner or (autonomous and bool(event.unified_msg_origin)
                               and self._policy_allows(event, scope))
        if autonomous:
            # Internal observation is not permission to rewrite the administrator's
            # saved boundaries, impersonate wake people, or target unknown users.
            if name == "group_policy" and str(args.get("mode") or "show") != "show":
                return {"ok": False, "error": "autonomy_cannot_change_group_permissions"}
            if name in ("wake_person_set", "wake_add", "wake_remove"):
                return {"ok": False, "error": "autonomy_cannot_change_wake_permissions"}
            if name in ("qq_send", "send_file", "send_shot"):
                kind = str(args.get("kind") or ("private" if scope.startswith("private:") else "group"))
                target = args.get("user_id") if kind == "private" else args.get("group_id")
                destination = ("private:" if kind == "private" else "") + str(target or scope.split(":", 1)[-1])
                if kind not in ("group", "private") or not self._autonomy_controller().origin(destination):
                    return {"ok": False, "error": "autonomy_destination_not_observed"}
                if not self._policy_allows(event, destination):
                    return {"ok": False, "error": "destination_policy_denied"}
        from .message_transfer import dispatch as transfer_dispatch, schema as transfer_schema
        if name in {item["function"]["name"] for item in transfer_schema()}:
            from .message_transfer_store import TransferStore
            async def transfer_action(action, parameters):
                """Share the existing authenticated native action bridge without retries."""
                return await asyncio.to_thread(self._onebot_http, action, parameters)
            def transfer_read_source(group_id):
                """Public-group memory is shared; native reads must prove each source group."""
                return event is not None and str(group_id).isascii() and str(group_id).isdigit() and int(group_id) > 0
            def transfer_send_target(group_id):
                """Apply persisted destination policy and autonomy's observed-origin boundary."""
                destination = str(group_id)
                if event is None or not self._policy_allows(event, destination):
                    return False
                return not autonomous or bool(self._autonomy_controller().origin(destination))
            return await transfer_dispatch(
                name, args, transfer_action, scope=str(scope),
                store=TransferStore(Path(HIGAN_ROOT) / "message_transfer" / "clipboard.sqlite"),
                audit_path=Path(HIGAN_ROOT) / "message_transfer" / "operations.sqlite",
                can_read_source=transfer_read_source, can_send_target=transfer_send_target)
        from .qq_requests import dispatch as request_dispatch, schema as request_schema
        if name in {item["function"]["name"] for item in request_schema()}:
            from .qq_request_store import RequestStore
            async def request_action(action, parameters):
                """Invoke only the installed native action through the existing bridge."""
                return await asyncio.to_thread(self._onebot_http, action, parameters)
            return await request_dispatch(
                name, args, request_action, can_manage=owner,
                store=RequestStore(Path(HIGAN_ROOT) / "qq_requests" / "requests.sqlite"),
                audit_path=Path(HIGAN_ROOT) / "qq_requests" / "operations.sqlite",
                expected_self_id=str(event.get_self_id() or "") if event is not None else None)
        if name == "reply_now":
            if event is None:
                return {"ok": False, "delivered": False, "error": "current_event_required"}
            from .interim_reply import deliver
            from .reply_selection import quote_target_ids
            turn = getattr(self, "_turn_context", {}).get(scope, {})
            delivery_count = len(turn.get("_deliveries") or [])
            async def send_interim(body, quote_id):
                """Share the normal policy/history/media boundary without reparsing validated text."""
                return await self._send(event, scope, body, quote_id, decoded=True)
            result = await deliver(args, turn.get("messages") or [], send_interim,
                                   allowed_quote_ids=quote_target_ids(turn.get("messages"), turn.get("history"), scope=scope, quote_history=turn.get("quote_history")))
            if result.get("delivered") is True:
                turn.setdefault("_interim_replies", []).append(result["text"])
                deliveries = turn.get("_deliveries") or []
                # Only this awaited send's native evidence may enrich the tool result.
                if len(deliveries) > delivery_count:
                    delivery = deliveries[-1]["delivery"]
                    if delivery["status"] == "native_confirmed":
                        from copy import deepcopy
                        result["delivery"] = deepcopy(delivery)
                    elif delivery.get("transport") == "local" and delivery.get("recorded") is True:
                        # Preserve successful local publication without claiming QQ receipt.
                        result.update(delivery)
                        result["delivery"] = dict(delivery)
            return result
        from .workspace_tools import dispatch as workspace_dispatch, schema as workspace_schema
        from .tool_discovery import dispatch as discovery_dispatch, schema as discovery_schema
        from .delegation import Manager, schema as delegation_schema
        if name in {item["function"]["name"] for item in workspace_schema()}:
            async def execute_workspace(code):
                """Execute only on the bot's own existing VM service, not the DSH/production host."""
                return await self._vm_tool("vm_exec", {"code": code, "lang": "python"})
            return await workspace_dispatch(name, args, execute_workspace)
        if name in {item["function"]["name"] for item in discovery_schema()}:
            return await discovery_dispatch(name, args, self._tool_schemas(),
                                            skills_root=Path(HIGAN_ROOT) / "skills", can_write=can_manage)
        if name in {item["function"]["name"] for item in delegation_schema()}:
            if not hasattr(self, "_delegation_manager"):
                self._delegation_manager = Manager(Path(HIGAN_ROOT) / "delegation")
            manager = self._delegation_manager
            try:
                if name == "agent_start":
                    from .delegated_runner import run_worker
                    ticket = {}
                    async def run_delegated(task, worker_scope, inbox):
                        """Run privately, then expose a typed result opportunity, never a forced send."""
                        try:
                            return await run_worker(self, task, worker_scope, inbox)
                        finally:
                            # Manager.start records/returns the ID before its new
                            # coroutine can run. Persisting the final receipt has
                            # no intervening await after this notification.
                            if ticket.get("id"):
                                try:
                                    self._autonomy_controller().notify(worker_scope, ticket["id"])
                                except Exception as exc:
                                    logger.warning("[higan] 委派结果机会记录失败：%s", type(exc).__name__)
                    receipt = await manager.start(args.get("task"), args.get("label"),
                                                  run_delegated, scope=scope)
                    ticket["id"] = receipt["id"]
                    return {"ok": True, **receipt}
                if name == "agent_list":
                    return {"ok": True, **manager.list(scope=scope)}
                if name == "agent_status":
                    result = manager.status(args.get("id"), scope=scope)
                elif name == "agent_message":
                    result = manager.message(args.get("id"), args.get("message"), scope=scope)
                else:
                    result = manager.interrupt(args.get("id"), scope=scope)
                return {"ok": not bool(result.get("error")), **result}
            except (ValueError, TypeError) as exc:
                return {"ok": False, "error": type(exc).__name__}
        if name in {item["function"]["name"] for item in social_schema()}:
            async def native_action(action, parameters):
                """Use the existing native event bridge; never expose QQ session credentials."""
                if event is None and name not in ("qq_account_info", "qq_social_status"):
                    raise RuntimeError("native_event_required")
                # AstrBot strips the OneBot envelope. The existing local native
                # HTTP bridge retains business status/retcode for write receipts;
                # HTTP 200 alone must not claim that a social action was accepted.
                return await asyncio.to_thread(self._onebot_http, action, parameters)
            return await social_dispatch(name, args, native_action, can_write=can_manage,
                                         audit_path=Path(HIGAN_ROOT) / "social.sqlite3")
        if name in ("autonomy_status", "autonomy_schedule"):
            controller = self._autonomy_controller()
            if name == "autonomy_schedule":
                if not can_manage:
                    return {"ok": False, "error": "autonomy_management_not_authorized"}
                try:
                    return {"ok": True, "settings": controller.schedule(**args)}
                except (ValueError, TypeError) as exc:
                    return {"ok": False, "error": type(exc).__name__}
            state = controller.snapshot()
            # Origin strings are transport metadata, not model-facing identity or credentials.
            return {"ok": True, "enabled": state["enabled"], "interval_seconds": state["interval_seconds"],
                    "scopes": state["scopes"], "known_scopes": list(state["origins"]),
                    "next_checks": state["next_checks"], "last_run": state["last_run"]}
        if name == "tool_observations":
            limit = max(1, min(20, int(args.get("limit") or 8)))
            offset = args.get("offset", 0)
            if type(offset) is not int or offset < 0:
                return {"ok": False, "error": "invalid_observation_offset"}
            archive_records = self._tool_evidence().recent(scope)
            end = max(0, len(archive_records) - offset)
            page = archive_records[max(0, end - limit):end]
            result = {"ok": True, "scope": scope, "observations": page}
            if "offset" in args:
                # Explicit paging exposes all retained records without attaching
                # the entire archive to every unrelated model request.
                result.update(offset=offset, total=len(archive_records),
                              has_more=end > len(page))
            return result
        if name in ("image_search", "page_images"):
            from .image_search import search, extract_page
            try:
                if name == "image_search":
                    return await asyncio.to_thread(search, args.get("query"), args.get("limit", 6))
                return await asyncio.to_thread(extract_page, args.get("url"), args.get("limit", 12))
            except (ValueError, TypeError) as exc:
                return {"ok": False, "error": "invalid_arguments", "error_type": type(exc).__name__}
            except Exception as exc:
                return {"ok": False, "error": "image_discovery_failed", "error_type": type(exc).__name__}
        if name == "web_search":
            query = str(args.get("query") or "").strip()
            if not query:
                return {"ok": False, "error": "empty_query"}
            try:
                results = await asyncio.wait_for(asyncio.to_thread(self._web_search, query),
                                                SEARCH_TIMEOUT * SEARCH_QUERY_MAX + 5)
                return {"ok": True, "query": query, "results": results,
                        "status": "results_available" if results else "no_usable_evidence"}
            except Exception as exc:
                return {"ok": False, "query": query, "error": type(exc).__name__}
        if name == "web_fetch":
            url = str(args.get("url") or "").strip()
            try:
                body = await asyncio.wait_for(asyncio.to_thread(self._fetch_page, url), SEARCH_TIMEOUT + 3)
                return {"ok": bool(body), "url": url, "body": body,
                        "truncated_at_chars": PAGE_TEXT_LIMIT if len(body) >= PAGE_TEXT_LIMIT else None}
            except Exception as exc:
                return {"ok": False, "url": url, "error": type(exc).__name__}
        try:
            tasks = self._tasks_mod()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[higan] tasks 模块不可用：%r", exc)
            tasks = None
        from .task_planning import dispatch as planning_dispatch, schema as planning_schema
        if name in {item["function"]["name"] for item in planning_schema()}:
            if tasks is None:
                return {"ok": False, "error": "tasks_unavailable"}
            result = planning_dispatch(name, args, tasks, scope)
            return {"ok": not bool(result.get("error")), **result}
        if name in ("read_forward", "forward_expand"):
            # All entry points share the native event API and the same typed source parser.
            from .forward_context import resolve
            return await resolve(event, forward_id=str(args.get("id") or "").strip() or None,
                                 message_id=str(args.get("msg_id") or "").strip().lstrip("#") or None)

        if name == "qq_group_inspect":
            from .group_inspection import inspect as inspect_group
            async def read_group(action, parameters):
                """Reuse the authenticated, read-only native bridge without changing the event origin."""
                return await asyncio.to_thread(self._onebot_http, action, parameters)
            return await inspect_group(args, read_group)
        if name == "history_search":
            try:
                from .cross_scope_memory import CrossScopeMemory, visible_scope
                requested = str(args.get("scope") or "")
                # Mute controls sending, not inclusion in the shared group memory.
                muted = ()
                if requested and not visible_scope(requested, str(scope), muted=muted):
                    return {"ok": False, "error": "history_scope_not_visible"}
                limit = args.get("limit", 10)
                if type(limit) is not int or not 1 <= limit <= 500:
                    return {"ok": False, "error": "invalid_history_limit"}
                keyword = args.get("keyword", "")
                if not isinstance(keyword, str):
                    return {"ok": False, "error": "invalid_history_keyword"}
                if not keyword.strip() and not requested:
                    return {"ok": False, "error": "history_keyword_or_scope_required"}
                offset = args.get("offset", 0)
                if type(offset) is not int or offset < 0:
                    return {"ok": False, "error": "invalid_history_offset"}
                hits = CrossScopeMemory(self._hist()).search(
                    keyword, str(scope), scope=requested, limit=limit + 1, muted=muted, offset=offset)
            except Exception as exc:
                return {"ok": False, "error": type(exc).__name__}
            more = len(hits) > limit
            hits = hits[:limit]
            return {"ok": True, "count": len(hits), "hits": hits,
                    "coverage": {"scope": requested or None, "returned": len(hits),
                                 "limit": limit, "offset": offset, "more_available": more,
                                 "next_offset": offset + len(hits) if more else None,
                                 "source": "recorded_history", "live_membership_verified": False,
                                 "ts_meaning": "archive_ingestion_time",
                                 "matching_records_are_not_identical_forward_counts": True}}
        if name == "self_status":
            # 返回可观察的自身状态，不包含密钥。
            _cfg_path = os.path.join(HIGAN_ROOT, "groups.json")
            _conf = {}
            try:
                with open(_cfg_path, encoding="utf-8") as _h:
                    _conf = json.load(_h)
            except Exception:                          # noqa: BLE001
                _conf = {}
            _pct = {str(k): v for k, v in (_conf.get("reply_pct") or {}).items()}
            _poll = {str(k): v for k, v in (_conf.get("poll_minutes") or {}).items()}
            _mention = [str(x) for x in (_conf.get("mention_only") or [])]
            _muted = [str(x) for x in (_conf.get("muted") or [])]

            _mem_path = os.path.join(HIGAN_ROOT, "memory", "memory.json")
            _facts, _scopes = 0, {}
            try:
                with open(_mem_path, encoding="utf-8") as _h:
                    _mem = json.load(_h)
                _facts = len((_mem.get("global") or {}).get("facts") or [])
                for _gid, _item in (_mem.get("scopes") or {}).items():
                    _scopes[str(_gid)] = {
                        "turns": _item.get("turns"),
                        "summary_len": len(str(_item.get("summary") or "")),
                    }
            except Exception:                          # noqa: BLE001
                pass

            _llm = {}
            try:
                with open(os.path.join(HIGAN_ROOT, "llm.json"), encoding="utf-8") as _h:
                    _raw = json.load(_h)
                _base = str(_raw.get("api_base") or _raw.get("base_url") or "")
                _llm = {"model": _raw.get("model") or _raw.get("name"),
                        "api_host": (_base.split("//")[-1].split("/")[0] if _base else "")}
            except Exception:                          # noqa: BLE001
                _llm = {"model": "未知"}

            _up = 0
            try:
                with open("/proc/uptime", encoding="utf-8") as _h:
                    _up = int(float(_h.read().split()[0]))
            except Exception:                          # noqa: BLE001
                pass

            return {
                "ok": True,
                "self": {"qq": str(event.get_self_id() or "") if event else "", "name": "Qhunliv-bot/彼岸酱"},
                "groups": {
                    "reply_pct": _pct,
                    "mention_only": _mention,
                    "poll_minutes": _poll,
                    "muted": _muted,
                    "note": "未列出的群 = 正常参与（默认 100%）",
                },
                "memory": {"facts": _facts, "scopes": _scopes,
                           "file_bytes": (os.path.getsize(_mem_path)
                                          if os.path.exists(_mem_path) else 0)},
                "model": _llm,
                "runtime": {"host_uptime_s": _up},
            }

        if name == "group_policy":
            from .group_policy import GroupPolicyStore
            try:
                return GroupPolicyStore(Path(HIGAN_ROOT) / "groups.json").update(
                    str(args.get("group") or scope or ""), str(args.get("mode") or "show"),
                    pct=args.get("pct"), minutes=args.get("minutes"))
            except Exception as exc:
                logger.warning("[higan] 群策略未完成持久化：%s", type(exc).__name__)
                return {"ok": False, "error": "group_policy_not_saved", "error_type": type(exc).__name__}
        if name == "send_sticker":
            # 从表情包索引选择图片，通过插件通道发送。
            try:
                # 从表情包 manifest 里挑
                _kw = str(args.get("keyword") or "").strip()
                _want = str(args.get("file") or "").strip()
                _items = list(self.manifest or [])
                if not _items:
                    return {"ok": False, "error": "表情包库是空的"}
                # 她自己指定了文件 ⇒ 直接发那张
                if _want:
                    for _item in _items:
                        if _want in str(_item.get("file") or ""):
                            _p = STICKER_DIR / str(_item.get("file"))
                            if _p.exists():
                                return {"ok": True, "deliver": "plugin",
                                        "path": str(_p), "send_as": "image"}
                    return {"ok": False, "error": "没有这个文件：%s" % _want}
                # 关键词匹配：描述 + 文件名
                _hits = []
                if _kw:
                    _kwl = _kw.lower()
                    for _item in _items:
                        _blob = (str(_item.get("desc") or "") + " "
                                 + str(_item.get("file") or "")).lower()
                        if _kwl in _blob:
                            _hits.append(_item)
                if len(_hits) == 1:
                    _p = STICKER_DIR / str(_hits[0].get("file"))
                    if _p.exists():
                        return {"ok": True, "deliver": "plugin",
                                "path": str(_p), "send_as": "image"}
                if _hits:
                    # 多张命中 ⇒ 把候选给她，她自己挑
                    return {"ok": True, "candidates": [
                        {"file": str(_i.get("file") or ""),
                         "desc": str(_i.get("desc") or "")[:40]} for _i in _hits[:8]],
                        "note": "挑一张就把它的 file 传给 send_sticker 的 file 参数"}
                # 没命中 ⇒ 给她最近用过的几张当参考，别擅自发
                return {"ok": False, "error": "没有匹配「%s」的表情包" % _kw,
                        "samples": [
                            {"file": str(_i.get("file") or ""),
                             "desc": str(_i.get("desc") or "")[:40]}
                            for _i in _items[:8]]}
            except Exception as _exc:  # noqa: BLE001
                return {"ok": False, "error": repr(_exc)[:200]}
        if name in ("__wake_v1_disabled__",):
            #  她自己的唤醒词/唤醒人（命中就当作被叫到，绕过回复率）
            try:
                _p = str(Path(HIGAN_ROOT) / "groups.json")
                with open(_p, encoding="utf-8") as _h:
                    _c = json.load(_h)
                _w = [str(x) for x in (_c.get("wake_words") or []) if str(x).strip()]
                _q = [str(x) for x in (_c.get("wake_persons") or []) if str(x).strip()]
                if name == "wake_list":
                    return {"ok": True, "wake_words": _w, "wake_persons": _q}
                _word = str(args.get("word") or "").strip()
                _qq = str(args.get("qq") or "").strip()
                if not _word and not _qq:
                    return {"ok": False, "error": "word 或 qq 至少给一个"}
                if name == "wake_add":
                    if _word and _word not in _w:
                        _w.append(_word)
                    if _qq and _qq not in _q:
                        _q.append(_qq)
                else:
                    if _word in _w:
                        _w.remove(_word)
                    if _qq in _q:
                        _q.remove(_qq)
                _c["wake_words"], _c["wake_persons"] = _w, _q
                with open(_p, "w", encoding="utf-8") as _h:
                    json.dump(_c, _h, ensure_ascii=False, indent=2)
                return {"ok": True, "wake_words": _w, "wake_persons": _q,
                        "count": len(_w) + len(_q)}
            except Exception as _exc:  # noqa: BLE001
                logger.warning("[higan] wake 工具失败：%r", str(_exc)[:120])
                return {"ok": False, "error": str(_exc)[:120]}

        if name == "wake_person_info":
            # 查看唤醒人的配置、事实和定点时间。
            try:
                with open(str(Path(HIGAN_ROOT) / "groups.json"), encoding="utf-8") as _h:
                    _c = json.load(_h)
                _qq = str(args.get("qq") or "").strip()
                if not _qq:
                    return {"ok": False, "error": "要给 qq"}
                _groups = [str(_g) for _g, _lst in (_c.get("wake_persons") or {}).items()
                           if _qq in [str(x) for x in (_lst or [])]]
                _conf = (_c.get("wake_person_conf") or {}).get(_qq) or {}
                _at = _c.get("wake_at") or {}
                _facts = []
                try:
                    import importlib.util as _ilu6
                    from pathlib import Path as _P6
                    _sp6 = _ilu6.spec_from_file_location(
                        __package__ + ".memory_wpi", str(_P6(BASE_DIR, "memory.py")))
                    _wpm = _ilu6.module_from_spec(_sp6)
                    _sp6.loader.exec_module(_wpm)
                    _st6 = _wpm.WakePersonFacts(
                        str(Path(HIGAN_ROOT) / "memory" / "wake_persons.json")).load()
                    _facts = _st6.list(_qq)
                except Exception:  # noqa: BLE001
                    _facts = []
                return {"ok": True, "qq": _qq,
                        "is_wake_person_in": _groups,
                        "conf": {"pct": _conf.get("pct", 100),
                                 "words": _conf.get("words") or []},
                        "wake_at": {g: (_at.get(g) or []) for g in _groups} or _at,
                        "facts_count": len(_facts),
                        "facts": _facts[:50]}
            except Exception as _exc:  # noqa: BLE001
                return {"ok": False, "error": str(_exc)[:150]}

        if name in ("wake_person_set", "wake_person_show"):
            #  单唤醒人配置：pct / words / hours
            try:
                _p = str(Path(HIGAN_ROOT) / "groups.json")
                with open(_p, encoding="utf-8") as _h:
                    _c = json.load(_h)
                _tbl = _c.setdefault("wake_person_conf", {})
                _qq = str(args.get("qq") or "").strip()
                if not _qq:
                    return {"ok": False, "error": "要给 qq"}
                if name == "wake_person_show":
                    return {"ok": True, "qq": _qq, "conf": _tbl.get(_qq) or {}}
                _item = _tbl.setdefault(_qq, {})
                if args.get("pct") is not None:
                    _item["pct"] = max(0.0, min(100.0, float(args["pct"])))
                if isinstance(args.get("words"), list):
                    _item["words"] = [str(x) for x in args["words"]][:20]
                if isinstance(args.get("hours"), list):
                    _item["hours"] = [int(x) for x in args["hours"]][:2]
                with open(_p, "w", encoding="utf-8") as _h:
                    json.dump(_c, _h, ensure_ascii=False, indent=2)
                return {"ok": True, "qq": _qq, "conf": _item}
            except Exception as _exc:  # noqa: BLE001
                return {"ok": False, "error": str(_exc)[:150]}

        if name in ("wake_list", "wake_add", "wake_remove", "wake_search"):
            # 按群管理唤醒人和唤醒词，支持删除与搜索。
            try:
                _p = str(Path(HIGAN_ROOT) / "groups.json")
                with open(_p, encoding="utf-8") as _h:
                    _c = json.load(_h)
                _wp = _c.get("wake_persons") or {}
                _ww = _c.get("wake_words") or {}
                if not isinstance(_wp, dict):
                    _wp = {}
                if not isinstance(_ww, dict):
                    _ww = {}
                _g = str(args.get("group") or "").strip() or str(scope or "")
                if name == "wake_list":
                    _mode = str(args.get("mode") or "summary")
                    if _mode == "group" and _g:
                        return {"ok": True, "group": _g,
                                "persons": [str(x) for x in (_wp.get(_g) or [])],
                                "words": [str(x) for x in (_ww.get(_g) or [])]}
                    return {"ok": True, "mode": "summary",
                            "groups": {k: {"persons": len(v or []),
                                           "words": len(_ww.get(k) or [])}
                                       for k, v in _wp.items()},
                            "hint": "要看某个群的名单就传 group（例如本群）"}
                if name == "wake_search":
                    _q = str(args.get("query") or "").strip()
                    _hit_p, _hit_w = {}, {}
                    for _gid, _lst in _wp.items():
                        _f = [x for x in (_lst or []) if _q and _q in str(x)]
                        if _f:
                            _hit_p[_gid] = _f
                    for _gid, _lst in _ww.items():
                        _f = [x for x in (_lst or []) if _q and _q in str(x)]
                        if _f:
                            _hit_w[_gid] = _f
                    return {"ok": True, "query": _q,
                            "persons": _hit_p, "words": _hit_w}
                _qq = str(args.get("qq") or "").strip()
                _word = str(args.get("word") or "").strip()
                if name == "wake_add":
                    if _qq:
                        if not _qq.isdigit():
                            return {"ok": False,
                                    "error": "唤醒人必须用 QQ 号（纯数字），名字不可信"}
                        _lst = _wp.setdefault(_g, [])
                        if _qq not in _lst:
                            _lst.append(_qq)
                    elif _word:
                        _lst = _ww.setdefault(_g, [])
                        if _word not in _lst:
                            _lst.append(_word)
                    else:
                        return {"ok": False, "error": "要给 qq（唤醒人）或 word（唤醒词）"}
                else:  # wake_remove
                    if _qq:
                        _lst = _wp.get(_g) or []
                        if _qq in _lst:
                            _lst.remove(_qq)
                        _wp[_g] = _lst
                    if _word:
                        _lst = _ww.get(_g) or []
                        if _word in _lst:
                            _lst.remove(_word)
                        _ww[_g] = _lst
                _c["wake_persons"], _c["wake_words"] = _wp, _ww
                with open(_p, "w", encoding="utf-8") as _h:
                    json.dump(_c, _h, ensure_ascii=False, indent=2)
                return {"ok": True, "group": _g,
                        "persons": [str(x) for x in (_wp.get(_g) or [])],
                        "words": [str(x) for x in (_ww.get(_g) or [])]}
            except Exception as _exc:  # noqa: BLE001
                logger.warning("[higan] wake 工具失败：%r", str(_exc)[:120])
                return {"ok": False, "error": str(_exc)[:120]}

        if name == "wp_fact_move":
            #  通用事实 → 某个唤醒人的记录（原子：先写成功再从通用里删）
            try:
                import importlib.util as _ilu5
                from pathlib import Path as _P5
                _sp5 = _ilu5.spec_from_file_location(__package__ + ".memory_wpm", str(_P5(BASE_DIR, "memory.py")))
                _wpmod5 = _ilu5.module_from_spec(_sp5)
                _sp5.loader.exec_module(_wpmod5)
                _qq5 = str(args.get("qq") or "").strip()
                _fid5 = str(args.get("id") or "").strip()
                if not _qq5 or not _fid5:
                    return {"ok": False, "error": "要同时给 id 和 qq"}
                _gm = _wpmod5.Memory(str(Path(HIGAN_ROOT) / "memory" / "memory.json")).load()
                _hit = [f for f in _gm.list_facts() if str(f.get("id")) == _fid5]
                if not _hit:
                    return {"ok": False, "error": "通用事实里没有 %s" % _fid5}
                if _hit[0].get("images"):
                    # The destination is text-only: never report a successful move
                    # that deletes the only durable copy of a fact's attachments.
                    return {"ok": False, "error": "destination_does_not_support_images"}
                _text = str(_hit[0].get("text") or "")
                _st5 = _wpmod5.WakePersonFacts(
                    str(Path(HIGAN_ROOT) / "memory" / "wake_persons.json")).load()
                _new_id = _st5.add(_qq5, _text)
                if not _new_id:
                    return {"ok": False, "error": "写入唤醒人记录失败"}
                _gm.delete_fact(_fid5)
                logger.info("[higan] 通用事实 %s 已移入唤醒人 %s（%s）" % (_fid5, _qq5, _new_id))
                return {"ok": True, "moved": _fid5, "to_person": _qq5,
                        "new_id": _new_id, "count": _st5.count(_qq5)}
            except Exception as _exc:  # noqa: BLE001
                return {"ok": False, "error": str(_exc)[:150]}

        if name in ("wp_fact_add", "wp_fact_list", "wp_fact_search", "wp_fact_delete"):
            #  关于某个唤醒人的记录（单独存，最多 200 条，不计入 facts）
            try:
                import importlib.util as _ilu3
                from pathlib import Path as _P3
                _sp3 = _ilu3.spec_from_file_location(
                    __package__ + ".memory_wp", str(_P3(BASE_DIR, "memory.py")))
                _wpmod = _ilu3.module_from_spec(_sp3)
                _sp3.loader.exec_module(_wpmod)
                _st = _wpmod.WakePersonFacts(str(Path(HIGAN_ROOT) / "memory" / "wake_persons.json")).load()
                _qq = str(args.get("qq") or "").strip()
                if name == "wp_fact_add":
                    _fid = _st.add(_qq, str(args.get("text") or ""))
                    return {"ok": bool(_fid), "id": _fid, "count": _st.count(_qq)}
                if name == "wp_fact_list":
                    return {"ok": True, "qq": _qq, "facts": _st.list(_qq)}
                if name == "wp_fact_search":
                    return {"ok": True, "qq": _qq,
                            "hits": _st.search(_qq, str(args.get("query") or ""))}
                _n = _st.delete(_qq, str(args.get("id") or ""),
                                str(args.get("keyword") or ""))
                return {"ok": True, "deleted": _n}
            except Exception as _exc:  # noqa: BLE001
                logger.warning("[higan] 唤醒人记录失败：%r", str(_exc)[:120])
                return {"ok": False, "error": str(_exc)[:120]}

        from .fact_tools import dispatch as fact_dispatch, schemas as fact_schema
        if name == "facts_delete" or name in {item["function"]["name"] for item in fact_schema()}:
            from .memory import Memory
            return fact_dispatch(name, args,
                memory=Memory(str(Path(HIGAN_ROOT) / "memory" / "memory.json")).load(),
                current_scope=str(scope),
                can_write=event is not None and self._policy_allows(event, str(scope)), muted=())
        if name in ("facts_list", "facts_search", "facts_edit", "facts_read_image"):
            try:
                from .memory import Memory
                _mm = Memory(str(Path(HIGAN_ROOT) / "memory" / "memory.json")).load()
                _muted = ()  # All group facts share one memory; send policies are unchanged.
                if name == "facts_list":
                    return {"ok": True, "facts": _mm.list_facts(current_scope=str(scope), muted=_muted)}
                if name == "facts_search":
                    _q = str(args.get("query") or "")
                    _lim = int(args.get("limit") or 500)
                    _hits = _mm.list_facts(limit=None, current_scope=str(scope), muted=_muted)
                    _kw = [w for w in _q.split() if w]
                    _hit = [f for f in _hits if not _kw or all(w in f["text"] for w in _kw)][:_lim]
                    return {"ok": True, "query": _q, "facts": _hit}
                if name == "facts_read_image":
                    _image = _mm.get_fact_image(str(args.get("id") or ""), args.get("index", 0),
                                               current_scope=str(scope), muted=_muted)
                    if _image is None:
                        return {"ok": False, "error": "fact_image_not_found"}
                    return {"ok": True, "id": args.get("id"), "image": _image}
                if name == "facts_edit":
                    if event is None or not self._policy_allows(event, str(scope)):
                        return {"ok": False, "error": "fact_write_not_authorized"}
                    # Hidden IDs must not authorize edits or trigger image fetching.
                    if str(args.get("id") or "") not in {row["id"] for row in _mm.list_facts(
                            None, current_scope=str(scope), muted=_muted)}:
                        return {"ok": False, "id": args.get("id")}
                    from .fact_image_tools import snapshot_images
                    _images = await snapshot_images(args.get("images"), self._as_llm_url)
                    _current = next(row for row in _mm.list_facts(
                        None, current_scope=str(scope), muted=_muted)
                        if str(row["id"]) == str(args.get("id") or ""))
                    _text = args["text"] if "text" in args else _current["text"]
                    if not isinstance(_text, str):
                        return {"ok": False, "error": "fact_text_must_be_string"}
                    _ok = _mm.edit_fact(str(args.get("id") or ""), _text,
                                       images=_images, structure=args.get("structure"),
                                       current_scope=str(scope), muted=_muted)
                    _updated = next((row for row in _mm.list_facts(
                        None, current_scope=str(scope), muted=_muted)
                        if str(row["id"]) == str(args.get("id") or "")), None)
                    return {"ok": bool(_ok), "persisted": bool(_ok), "id": args.get("id"),
                            "fact": _updated, "operation": "edit"}
            except Exception as _exc:  # noqa: BLE001
                logger.warning("[higan] 事实管理失败：%r", str(_exc)[:120])
                return {"ok": False, "error": str(_exc)[:120]}

        if name == "remember":
            if event is None or not self._policy_allows(event, str(scope)):
                return {"ok": False, "error": "fact_write_not_authorized"}
            if "text" in args and not isinstance(args["text"], str):
                return {"ok": False, "error": "fact_text_must_be_string"}
            # 通过 memory.add_fact 记录事实并复用去重逻辑。
            _txt = str(args.get("text") or "").strip()
            if not _txt and not args.get("images"):
                return {"ok": False, "error": "text 和 images 不能同时为空"}
            try:
                from .memory import Memory
                from .fact_image_tools import snapshot_images
                from .fact_provenance import memory_source
                from .message_batch import message_record
                from .fact_structure import normalize_structure
                turn = (getattr(self, "_turn_context", {}) or {}).get(str(scope), {})
                _records = turn.get("messages") or [message_record(event, event.get_message_str() or "")]
                _source = memory_source(str(scope), _records, args.get("source_message_ids"))
                if "structure" in args:
                    normalize_structure(args["structure"])
                _images = await snapshot_images(args.get("images"), self._as_llm_url)
                _m = Memory(str(Path(HIGAN_ROOT) / "memory" / "memory.json")).load()
                _before_ids = {row["id"] for row in _m.list_facts(None, current_scope=str(scope))}
                _fact_id = _m.add_fact(_txt, str(scope), source=_source, categories=args.get("categories"),
                                       images=_images, structure=args.get("structure"))
                _n = len((_m.data.get("global") or {}).get("facts") or [])
                _created = _fact_id not in _before_ids
                _position = (next((index for index, item in enumerate(
                    (_m.data.get("global") or {}).get("facts") or [], start=1)
                    if str(item.get("id")) == str(_fact_id)), None))
                logger.info("[higan] remember_persisted kind=%s created=%s facts_total=%d",
                            (args.get("structure") or {}).get("kind", "unspecified"),
                            _created, _n)
                _stored = next(row for row in _m.list_facts(None, current_scope=str(scope))
                               if str(row["id"]) == str(_fact_id))
                return {"ok": True, "persisted": True, "created": _created,
                        "append_position": _position, "appended_at_end": _created and _position == _n,
                        "id": _fact_id, "saved": _stored["text"], "facts_total": _n,
                        "source_scope": _stored["source_scope"], "images": _stored["images"],
                        "structure": _stored["structure"], "fact": _stored,
                        "operation": "remember"}
            except Exception as _exc:  # noqa: BLE001
                logger.warning("[higan] remember 失败：%r", str(_exc)[:120])
                return {"ok": False, "error": str(_exc)[:120]}
        if name == "vm_fetch":
            # 将指定文件下载到机器人虚拟机。
            _msg_id = str(args.get("msg_id") or "").strip()
            _url = str(args.get("url") or "").strip()
            _name = str(args.get("name") or "").strip()
            if not _url and _msg_id:
                try:
                    _u = await self._file_url_of_message(_msg_id)
                except Exception as _exc:  # noqa: BLE001
                    logger.warning("[higan] vm_fetch 取消息文件失败：%r", str(_exc)[:120])
                    _u = ""
                _url = _u or ""
            if not _url:
                return {"ok": False, "error": "没有可下载的地址（url 与 msg_id 都没给，或那条消息里没有文件）"}
            _res = await self._vm_tool("vm_fetch", {"url": _url, "name": _name})
            logger.info("[higan] vm_fetch %s → %s" % (_url[:60], str(_res)[:120]))
            return _res
        if name in ("vm_job_start", "vm_job_status", "vm_job_cancel", "vm_job_list"):
            from .vm_jobs import build_start, build_status, build_cancel, build_list
            try:
                if name == "vm_job_start":
                    bootstrap = build_start(str(args.get("code") or ""), str(args.get("lang") or "python"))
                elif name == "vm_job_status":
                    bootstrap = build_status(str(args.get("job_id") or ""))
                elif name == "vm_job_list":
                    bootstrap = build_list(args.get("offset", 0), args.get("limit", 20))
                else:
                    bootstrap = build_cancel(str(args.get("job_id") or ""))
            except (ValueError, TypeError):
                return {"ok": False, "error": "invalid_job_arguments"}
            response = await self._vm_tool("vm_exec", {"code": bootstrap, "lang": "python"})
            if not response.get("ok"):
                return {"ok": False, "error": "job_transport_failed", "detail": response}
            try:
                return json.loads(str(response.get("output") or ""))
            except ValueError:
                return {"ok": False, "error": "job_response_invalid"}
        if name == "send_file":
            delivery = dict(args)
            if not delivery.get("kind"):
                delivery["kind"] = "private" if scope.startswith("private:") else "group"
            if delivery["kind"] not in ("group", "private"):
                return {"ok": False, "error": "invalid_delivery_kind"}
            key = "user_id" if delivery["kind"] == "private" else "group_id"
            if not delivery.get(key):
                same_kind = scope.startswith("private:") == (delivery["kind"] == "private")
                destination = scope.split(":", 1)[-1]
                if not same_kind or not destination.isdigit() or int(destination) <= 0:
                    return {"ok": False, "error": "explicit_destination_required"}
                delivery[key] = destination
            try:
                if int(delivery[key]) <= 0:
                    raise ValueError("destination")
            except (TypeError, ValueError):
                return {"ok": False, "error": "invalid_delivery_destination"}
            destination = ("private:" if delivery["kind"] == "private" else "") + str(delivery[key])
            if not self._policy_allows(event, destination):
                return {"ok": False, "error": "destination_policy_denied"}
            from .qq_native_actions import media_kind, upload, prepare_container_file
            local = Path(str(delivery.get("path") or ""))
            if not local.is_absolute():
                local = Path(HIGAN_ROOT) / local
            if not local.is_file():
                return {"ok": False, "delivered": False, "error": "local_file_required"}
            if local.stat().st_size > 200 * 1024 * 1024:
                return {"ok": False, "delivered": False, "error": "file_too_large"}
            send_as = media_kind(str(local))
            if send_as != "file":
                # The framework event sends to its authenticated current origin;
                # do not silently route a different requested scope back here.
                if destination != str(scope):
                    return {"ok": False, "delivered": False, "error": "media_destination_requires_origin"}
                return {"ok": True, "deliver": "plugin", "path": str(local),
                        "send_as": send_as, "name": str(delivery.get("name") or local.name)}
            async def native_upload(action, parameters):
                """Submit the actual upload action, never the VM welcome endpoint."""
                return await asyncio.to_thread(self._onebot_http, action, parameters)
            return await upload(delivery["kind"], str(local),
                                user_id=int(delivery.get("user_id") or 0),
                                group_id=int(delivery.get("group_id") or 0),
                                name=str(delivery.get("name") or ""), call_action=native_upload,
                                prepare_file=prepare_container_file)
        if name == "vm_recall":
            if scope.startswith("private:") or not scope.isdigit():
                return {"ok": False, "error": "group_scope_required"}
            if not self._policy_allows(event, scope):
                return {"ok": False, "error": "destination_policy_denied"}
            from .qq_native_actions import recall
            async def native_recall(action, parameters):
                """Use the same checked native bridge for readback and explicit own-message recall."""
                return await asyncio.to_thread(self._onebot_http, action, parameters)
            return await recall(scope, count=args.get("count", 1), call_action=native_recall)
        if name == "send_shot":
            # Bind an omitted target to this authenticated conversation only;
            # never send a screenshot to a developer's historical group.
            delivery = dict(args)
            kind = str(delivery.get("kind") or ("private" if scope.startswith("private:") else "group"))
            if kind not in ("group", "private"):
                return {"ok": False, "error": "invalid_delivery_kind"}
            key = "user_id" if kind == "private" else "group_id"
            if not delivery.get(key) and scope.startswith("private:") == (kind == "private"):
                delivery[key] = scope.split(":", 1)[-1]
            target = delivery.get(key)
            if isinstance(target, bool) or not str(target or "").isascii() or not str(target or "").isdigit() or int(target) <= 0:
                return {"ok": False, "error": "explicit_destination_required"}
            destination = ("private:" if kind == "private" else "") + str(target)
            if not self._policy_allows(event, destination):
                return {"ok": False, "error": "destination_policy_denied"}
            delivery["kind"] = kind
            return await self._vm_tool("vm_send_shot", delivery)
        if name == "qq_send":
            # Resolve omitted destinations from the real current scope, never a
            # hardcoded group. Cross-scope targets still undergo persisted policy.
            kind = str(args.get("kind") or ("private" if scope.startswith("private:") else "group"))
            key = "user_id" if kind == "private" else "group_id"
            target = args.get(key)
            if not target and scope.startswith("private:") == (kind == "private"):
                target = scope.split(":", 1)[-1]
            try:
                if kind not in ("group", "private", "temp") or isinstance(target, bool) or int(target or 0) <= 0:
                    raise ValueError("destination")
            except (TypeError, ValueError):
                return {"ok": False, "error": "explicit_destination_required"}
            # Policy, provenance and publication must use the same numeric target
            # as the native transport, not a different key such as "042" or "42.0".
            destination = ("private:" if kind == "private" else "") + str(int(target))
            if not self._policy_allows(event, destination):
                return {"ok": False, "error": "destination_policy_denied"}
            options = {"mentions": args["mentions"]} if "mentions" in args else {}
            body = str(args.get("text") or "")
            result = await self._qq_send(kind, body,
                                         int(target) if kind == "private" else int(args.get("user_id") or 0),
                                         int(target) if kind != "private" else 0, **options)
            from .task_delivery import _message_id
            message_id = _message_id(result.get("message_id")) if isinstance(result, dict) else None
            if (message_id is not None and result.get("ok") is True
                    and result.get("delivered") is True):
                # Native sends bypass _send, but are the same public action.
                # Preserve the actual destination/receipt before continuing,
                # including mention-only sends without a plain text body.
                mentions = [{"id": str(value), "display_name": ""}
                            for value in (args.get("mentions") or [])]
                # A temporary session routes privately through its source group;
                # the group grants routing access but did not receive the message.
                delivery_scope = "private:" + str(int(args.get("user_id"))) if kind == "temp" else destination
                record = {"kind": "assistant_delivery", "id": message_id,
                          "body": body, "mentions": mentions, "quote": None,
                          "source": {"kind": "tool", "tool": "qq_send"},
                          "delivery": {"status": "native_confirmed", "message_ids": [message_id],
                                       "destination_scope": delivery_scope, "reply_to": [],
                                       "sent_at": datetime.now(timezone.utc).isoformat()}}
                visible = " ".join("[at:%s]" % item["id"] for item in mentions)
                self._record_visible_reply(delivery_scope, (visible + " " + body).strip(), record=record)
            return result
        if name in ("task_add", "task_add_script") and tasks is not None:
            if name == "task_add_script" and not str(args.get("trigger_script") or "").strip():
                return {"ok": False, "error": "trigger_script_required"}
            destination = str(args.get("scope") or scope or "")
            origin = str(getattr(event, "unified_msg_origin", "") or "")
            # Scope and authentic framework origin must describe the SAME transport.
            # A task cannot use another scope while keeping this event's destination.
            if not origin:
                return {"ok": False, "error": "task_origin_missing", "scheduler_accepted": False}
            if not destination or destination != str(scope):
                return {"ok": False, "error": "task_destination_mismatch", "scheduler_accepted": False}
            task = tasks.add(str(args.get("title") or ""), str(args.get("goal") or ""),
                             str(args.get("scope") or scope or ""),
                             int(args.get("interval_s") or 120),
                             str(args.get("repeat") or "every"),
                             int(args.get("delay_s") or 0),
                             str(args.get("brief") or ""),
                             str(args.get("resume_prompt") or ""),
                              "script" if name == "task_add_script" else str(args.get("trigger_kind") or "time"),
                             str(args.get("trigger_script") or ""),
                              int(args.get("trigger_every_s") or 60),
                              origin=origin, run_at=args.get("run_at"),
                              cadence=str(args.get("cadence") or "after_completion"))
            logger.info("[higan] 新任务 %s「%s」每 %ds 一次（scope=%s）"
                        % (task["id"], task["title"], task["interval_s"], task["scope"]))
            return {"ok": True, "task": task, "scheduler_accepted": task.get("status") == "active",
                    "delivery": "not_attempted", "scheduled_at": task.get("next_run_at"),
                    "cadence": task.get("cadence", "after_completion")}
        if name == "task_list" and tasks is not None:
            return {"ok": True, "tasks": tasks.listing(str(args.get("scope") or scope or ""),
                only_active=bool(args.get("only_active", False)), status=str(args.get("status") or ""))}
        if name == "task_update" and tasks is not None:
            hit = tasks.update(str(args.get("id") or ""), str(args.get("title") or ""),
                               str(args.get("brief") or ""), str(args.get("resume_prompt") or ""),
                               int(args.get("interval_s") or 0),
                               str(args.get("next_step_note") or ""),
                               str(args.get("trigger_kind") or ""),
                               str(args.get("trigger_script") or ""),
                                int(args.get("trigger_every_s") or 0),
                                repeat=str(args.get("repeat") or ""), delay_s=args.get("delay_s"),
                                goal=str(args.get("goal") or ""), run_at=args.get("run_at"),
                                cadence=str(args.get("cadence") or ""))
            logger.info("[higan] 任务改写：%s" % [h.get("title") for h in hit])
            return {"ok": bool(hit), "updated": [h.get("title") for h in hit]}
        if name in ("task_done", "task_cancel", "task_pause", "task_resume") and tasks is not None:
            hit = tasks.set_status(str(args.get("id") or ""), str(args.get("title") or ""),
                                   {"task_done": "done", "task_cancel": "cancelled",
                                    "task_pause": "paused", "task_resume": "active"}[name],
                                   str(args.get("note") or ""))
            logger.info("[higan] 任务 %s 结束：%s" % (name, [h.get("title") for h in hit]))
            return {"ok": bool(hit), "closed": [h.get("title") for h in hit]}
        return await self._vm_tool(name, args)

    def _send_onebot_sync(self, scope: str, text: str) -> bool:
        """Use the shared native payload and require a message-ID receipt, never root health."""
        from .qq_delivery import build, receipt
        try:
            private = scope.startswith("private:")
            action, parameters = build("private" if private else "group", text,
                                       user_id=int(scope.split(":", 1)[1]) if private else 0,
                                       group_id=0 if private else int(scope))
            return bool(receipt(self._onebot_http(action, parameters), action=action).get("delivered"))
        except Exception as exc:
            logger.warning("[higan] 后台原生发送未确认：%s", type(exc).__name__)
            return False

    async def _run_task_once(self, task: dict) -> str:
        """Reuse the foreground native transcript/media path for every durable continuation."""
        from .task_event import TaskEvent
        from .task_delivery import task_active
        from .reply_selection import decode_reply, quote_target_ids
        # Per-attempt quote metadata is not persisted task configuration or a
        # delivery receipt. Never inherit a previous attempt's chosen target.
        task.pop("_final_quote_id", None)
        if not task_active(task, self._tasks_mod().listing(only_active=False)):
            return ""
        scope = str(task.get("scope") or "")
        event = TaskEvent(self, task)
        text = str(task.get("resume_prompt") or task.get("goal") or task.get("title") or "")
        turns = getattr(self, "_turn_context", None)
        if turns is None:
            turns = self._turn_context = {}
        previous = turns.get(scope)
        had_previous = scope in turns
        # A scheduled continuation has no current human messages or attachments.
        # Keep shared historical memory, but never inherit a pending human turn.
        turns[scope] = {"messages": [], "history": [], "quote_history": [],
                        "image_refs": [], "image_sources": [], "clock": self._clock_context()}
        try:
            turns[scope]["quote_history"] = self._hist().load_record_snapshot(
                scope, limit=200)["records"]
        except Exception:
            turns[scope]["quote_history"] = []
        try:
            raw = await self._decide_tools_first(event, scope, text, False, task_context=task)
            if not task_active(task, self._tasks_mod().listing(only_active=False)):
                return ""
            selection = decode_reply(raw, [], allow_unattributed=True, allowed_quote_ids=quote_target_ids(
                [], turns[scope].get("history"), scope=scope,
                quote_history=turns[scope].get("quote_history")))
            if selection["error"]:
                raise RuntimeError("task_reply_protocol_invalid")
            if selection.get("quote_id") is not None:
                task["_final_quote_id"] = selection["quote_id"]
            return selection["text"]
        finally:
            if had_previous:
                turns[scope] = previous
            else:
                turns.pop(scope, None)

    async def _task_loop(self) -> None:
        """Claim one durable task, serialize desktop access, and acknowledge only its own outcome."""
        while True:
            try:
                await asyncio.sleep(25)
                if getattr(self, "_stopping", False):
                    return
                tasks = self._tasks_mod()
                for candidate in await asyncio.to_thread(tasks.due):
                    if not candidate.get("origin"):
                        tasks.set_status(str(candidate["id"]), status="paused", note="task_origin_missing")
                        continue
                    if not hasattr(self, "_execution_lock"):
                        self._execution_lock = asyncio.Lock()
                    # Join the same FIFO lock without preempting in-flight work.
                    # Repeated polling/skip under pending chat could otherwise
                    # starve an explicitly scheduled task indefinitely.
                    async with self._execution_lock:
                        task = await asyncio.to_thread(tasks.claim, str(candidate["id"]))
                        if task is None:
                            continue
                        self._task_executing = True
                        try:
                            text = await self._run_task_once(task)
                            from .task_delivery import task_active
                            # Completion/revocation is authoritative even when a
                            # tool-free model response arrived after that change.
                            if not task_active(task, tasks.listing(only_active=False)):
                                continue
                            scope = str(task.get("scope") or "")
                            if text and scope:
                                from .task_event import TaskEvent
                                final_event = TaskEvent(self, task)
                                quote = task.pop("_final_quote_id", None)
                                if quote is not None:
                                    delivered = await self._send(final_event, scope, text, quote_id=quote, decoded=True)
                                else:
                                    delivered = await self._send(final_event, scope, text, decoded=True)
                                from .task_delivery import fresh, observe
                                outcome = task.setdefault("_delivery_outcome", fresh())
                                receipt = getattr(final_event, "last_delivery_receipt", None)
                                from .local_runtime import local_route
                                channel = "local_record" if local_route(task.get("scope"), task.get("origin")) else "qq_send"
                                observe(outcome, channel, receipt or {"ok": False, "error": "delivery_not_confirmed"})
                                if not delivered:
                                    raise RuntimeError("task_reply_delivery_failed")
                            from .task_delivery import completion_failure
                            failure = completion_failure(task.get("_delivery_outcome") or {})
                            if failure:
                                raise RuntimeError(failure)
                            evidence = {"outcome": task["_delivery_outcome"]} if "_delivery_outcome" in task else {}
                            tasks.mark(task["id"], text[:200], success=True, claim_id=task["claim_id"], **evidence)
                        except asyncio.CancelledError:
                            from .task_delivery import task_active
                            # Revocation also owns failure metadata; an old runner
                            # must not rewrite completion or another claim's state.
                            if task_active(task, tasks.listing(only_active=False)):
                                tasks.mark(task["id"], "interrupted_outcome_requires_review", success=False,
                                           claim_id=task["claim_id"])
                            raise
                        except Exception as exc:
                            from .task_delivery import task_active
                            if task_active(task, tasks.listing(only_active=False)):
                                evidence = {"outcome": task["_delivery_outcome"]} if "_delivery_outcome" in task else {}
                                tasks.mark(task["id"], type(exc).__name__, success=False, claim_id=task["claim_id"], **evidence)
                                logger.warning("[higan] 后台任务暂停待检查：%s", task["id"])
                            else:
                                logger.info("[higan] 任务已失效，跳过旧执行结果：%s", task["id"])
                        finally:
                            self._task_executing = False
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning("[higan] 任务循环异常（继续）：%s", type(exc).__name__)

    def _ensure_task_loop(self) -> None:
        """Start only explicit persisted tasks; do not enable unsolicited free activity."""
        task = getattr(self, "_task_loop_task", None)
        if task is None or task.done():
            self._task_loop_task = self._start_background(self._task_loop())
            logger.info("[higan] 后台任务循环已启动（25s 一轮）")

    async def _qq_send(self, kind: str, text: str, user_id: int = 0,
                       group_id: int = 0, mentions: list | None = None) -> dict:
        """Publish native message segments and require the actual platform message ID."""
        from .qq_delivery import send
        async def native_action(action, parameters):
            """Share the inspected native bridge; do not route through the VM health endpoint."""
            return await asyncio.to_thread(self._onebot_http, action, parameters)
        try:
            return await send(kind, text, user_id=user_id, group_id=group_id,
                              mentions=mentions, call_action=native_action)
        except (ValueError, TypeError) as exc:
            return {"ok": False, "delivered": False, "error": "invalid_send_arguments",
                    "error_type": type(exc).__name__}

    # 自主机会兼容入口
    async def _free_tick(self) -> None:
        """兼容入口；自主机会由统一轮询控制器提供。"""
        return None

    # 虚拟机工具映射



    async def _vm_tool(self, name: str, args: dict, *, verified_click: bool = False) -> dict:
        """调用机器人虚拟机的工具 API，保持显式工具映射、超时和错误处理。"""
        # Pointer gestures must not bypass visual verification. A click ticket
        # binds only one point, not a drag's path/duration: deny direct drags even
        # with verified_click=True; path confirmation uses the turn-local gate and
        # an explicitly installed trusted adapter, never these legacy HTTP routes.
        # Raw exec/browser evaluation remain trusted capabilities, not sandboxed
        # by this client-side guard; see the VM checkpoint boundary document.
        if name in ("vm_click", "vm_click_text", "vm_click_preview", "vm_click_confirm",
                    "vm_hold_drag", "vm_slider_drag", "vm_path_preview", "vm_path_confirm"):
            if name != "vm_click" or verified_click is not True:
                return {"ok": False, "error": "preview_and_confirm_required"}
        import asyncio
        import urllib.request

        from .browser_eval import build_eval_wrapper, decode_eval_response
        args = dict(args)
        if name in ("vm_recall", "vm_send_shot"):
            # Raw bridge callers have no authenticated scope to infer. Require an
            # explicit destination before any transport or eager payload coercion.
            kind = str(args.get("kind") or "group")
            key = "group_id" if name == "vm_recall" or kind == "group" else "user_id"
            target = args.get(key)
            if (kind not in ("group", "private") or isinstance(target, bool)
                    or not str(target or "").isascii() or not str(target or "").isdigit()
                    or int(target) <= 0):
                return {"ok": False, "error": "explicit_destination_required"}
        if name in ("vm_eval", "vm_frame_eval"):
            args["js"] = build_eval_wrapper(str(args.get("js") or ""))
        mapping = {
            "vm_shot": ("GET", "/vm/shot", None),
            "send_file": ("POST", "/vm/qq/send_file", {"path": str(args.get("path") or ""),
                "kind": str(args.get("kind") or "group"), "group_id": int(args.get("group_id") or 0),
                "user_id": int(args.get("user_id") or 0), "name": str(args.get("name") or "")}),
            "vm_fetch": ("POST", "/vm/download", {"url": str(args.get("url") or ""),
                                                  "name": str(args.get("name") or "")}),
            "vm_files": ("GET", "/vm/files?dir=%s" % (args.get("dir") or "files"), None),
            "vm_click": ("POST", "/vm/click", {"x": int(args.get("x") or 0),
                                               "y": int(args.get("y") or 0)}),
            "vm_type": ("POST", "/vm/type", {"text": str(args.get("text") or "")}),
            "vm_key": ("POST", "/vm/key", {"key": str(args.get("key") or "Return")}),
            "vm_browse": ("POST", "/vm/browse", {"url": str(args.get("url") or "")}),
            "vm_exec": ("POST", "/vm/exec", {"code": str(args.get("code") or ""),
                                             "lang": str(args.get("lang") or "python")}),
            "vm_recall": ("POST", "/vm/qq/recall", {"group_id": str(args.get("group_id") or ""),
                                                    "count": int(args.get("count") or 1)}),
            "tab_action": ("POST", "/vm/tab", {"action": str(args.get("action") or "close"),
                                               "n": int(args.get("n") or 0)}),
            "vm_click_text": ("POST", "/vm/click_text", {"text": str(args.get("text") or ""),
                              "exact": bool(args.get("exact") or False),
                              "index": int(args.get("index") or 0)}),
            "vm_click_confirm": ("POST", "/vm/click_confirm", {}),
            "vm_click_preview": ("POST", "/vm/click_preview", {"x": int(args.get("x") or 0),
                                 "y": int(args.get("y") or 0),
                                 "label": str(args.get("label") or "")}),
            "vm_send_shot": ("POST", "/vm/qq/send_shot", {"path": str(args.get("path") or ""),
                              "kind": str(args.get("kind") or "group"),
                              "user_id": int(args.get("user_id") or 0),
                              "group_id": int(args.get("group_id") or 0)}),
            "mail_recent": ("POST", "/vm/mail/recent", {"limit": int(args.get("limit") or 5)}),
            "mail_web_login": ("POST", "/vm/mail/web_login", {}),
            "mail_web_read": ("POST", "/vm/mail/web_read", {"limit": int(args.get("limit") or 2500)}),
            "vm_frame_eval": ("POST", "/vm/frame_eval", {"js": str(args.get("js") or ""),
                              "match": str(args.get("match") or "")}),
            "mail_list": ("POST", "/vm/mail/list", {"limit": int(args.get("limit") or 10),
                                    "unseen_only": bool(args.get("unseen_only") or False)}),
            "mail_code": ("POST", "/vm/mail/code", {"limit": int(args.get("limit") or 5)}),
            "vm_eval": ("POST", "/vm/eval", {"js": str(args.get("js") or "")}),
            "vm_slider_gap": ("POST", "/vm/slider/gap", {"bg_url": str(args.get("bg_url") or ""),
                                                         "piece_url": str(args.get("piece_url") or "")}),
        }
        if name not in mapping:
            return {"ok": False, "error": "unknown tool %s" % name}

        def _call() -> dict:
            """执行一次虚拟机请求，保留结构化错误，不自动重试可能有副作用的操作。"""
            method, path, payload = mapping[name]
            try:
                from .runtime_settings import configured_vm_bridge
                url = configured_vm_bridge() + path
                if method == "GET":
                    with urllib.request.urlopen(url, timeout=35) as resp:
                        return json.loads(resp.read().decode("utf-8", "replace"))
                body = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(url, data=body, method="POST")
                req.add_header("Content-Type", "application/json")
                with urllib.request.urlopen(req, timeout=35) as resp:
                    return json.loads(resp.read().decode("utf-8", "replace"))
            except Exception as exc:  # noqa: BLE001
                # Preserve structured API errors (e.g. JS failures) without logging sensitive payloads.
                from urllib.error import HTTPError
                if isinstance(exc, HTTPError):
                    try:
                        failure = json.loads(exc.read(16000).decode("utf-8", "replace"))
                        if isinstance(failure, dict):
                            failure["ok"] = False
                            return failure
                    except Exception:
                        pass
                return {"ok": False, "error": type(exc).__name__}

        try:
            result = await asyncio.to_thread(_call)
            return decode_eval_response(result) if name in ("vm_eval", "vm_frame_eval") else result
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": type(exc).__name__}

    async def _decide_tools_first(self, event: AstrMessageEvent, group: str,
                                  text: str, has_image: bool, task_context: dict | None = None) -> str:
        """Run one native model/tool transcript with neutral source data and optional limits.

        Empty output and SILENT end the turn without another decision call. Screenshots
        become observations; only explicit output markers or send tools deliver them.
        """
        try:
            import importlib.util as _ilu
            from pathlib import Path as _Path

            _f = _Path(__file__).resolve().parent / "agent_loop.py"
            _spec = _ilu.spec_from_file_location(__package__ + ".agent_loop", str(_f))
            _loop = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_loop)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[higan] agent_loop 不可用，回退普通裁决：%r", exc)
            return await self._decide(event, group, text, has_image)

        try:
            refs, _desc = await self._refs_for(event, group, text, has_image)
        except Exception:  # noqa: BLE001
            refs = []
        from .vm_click_gate import ClickGate
        click_gate = ClickGate(Path(HIGAN_ROOT) / "images")
        initial_refs = list(refs or [])       # Initial user attachments freeze before any tool runs.
        live_refs = list(initial_refs)        # Screenshots append here until the next model request.
        submitted_ref_count = len(initial_refs)  # A later screenshot may overwrite the same path.
        images_initialized = False
        shots: list = []                      # 要发出去的图

        from .conversation_context import system_prompt
        system = system_prompt(getattr(self, "persona", "") or "")
        # Context stays data-only. The original source grouping is retained, not rephrased as instructions.
        turn = (getattr(self, "_turn_context", {}) or {}).setdefault(group, {})
        self._turn_context = getattr(self, "_turn_context", {})
        self._turn_context[group] = turn
        turn["other_scopes"] = []
        from .local_runtime import is_local_event, local_route
        from .task_event import TaskEvent
        local_context = local_route(group, getattr(event, "unified_msg_origin", None)) and (
            is_local_event(event, self) or (isinstance(event, TaskEvent) and event.plugin is self))
        if local_context or (str(group).isascii() and str(group).isdigit() and int(group) > 0):
            from .cross_scope_memory import CrossScopeMemory, DEFAULT_SHARED_CACHE_BLOCK
            current_records = turn.get("messages") or []
            excluded = [(str(group), str(record["id"])) for record in current_records
                        if isinstance(record, dict) and record.get("id") is not None]
            try:
                snapshot_started = time.monotonic()
                shared = await asyncio.to_thread(
                    CrossScopeMemory(self._hist()).unified_snapshot,
                    exclude_messages=excluded, cache_block_size=DEFAULT_SHARED_CACHE_BLOCK)
                logger.info("[higan] reply_phase phase=history_snapshot seconds=%.3f rows=%d",
                            time.monotonic() - snapshot_started, len(shared["records"]))
                # Public QQ history stays read-only; local history never joins its pool.
                turn["history"] = list(shared["records"])
                if local_context:
                    local_history = turn.get("local_history")
                    if local_history is None:
                        local_history = self._hist().load_records(str(group), limit=200)
                    turn["history"].extend(dict(row, origin_scope=str(group)) for row in local_history)
                    # Preserve native timestamps; never recast local input as a QQ group.
                    turn["history"].sort(key=lambda row: row.get("ts", 0) if isinstance(row.get("ts"), (int, float)) else 0)
                turn["history_context"] = {key: shared[key] for key in ("mode", "source", "coverage")}
            except Exception as exc:
                # Do not silently fall back to separate current/other group silos.
                turn["history"] = list(turn.get("local_history") or []) if local_context else []
                turn["history_context"] = {"mode": "one_shared_timeline", "available": False,
                                           "error": type(exc).__name__}
                logger.warning("[higan] 统一群记忆读取失败：%s", type(exc).__name__)
        else:
            # Preserve the frozen private display history. Only the additional
            # same-scope native metadata authorizes quotes; it is never a group pool.
            turn.pop("history_context", None)
            try:
                turn["quote_history"] = self._hist().load_record_snapshot(
                    str(group), limit=200, exclude_message_ids=[record.get('id') for record
                    in (turn.get('messages') or []) if isinstance(record, dict)])["records"]
            except Exception:
                # Missing original metadata cannot authorize a guessed native quote.
                turn["quote_history"] = []
        turn["task"] = task_context
        # Delivery receipts belong only to this native turn, never a later chat.
        turn["_interim_replies"] = []
        turn["_deliveries"] = []
        from .task_delivery import fresh
        turn["_delivery_outcome"] = fresh()
        if task_context is not None:
            # Share only this local attempt's evidence; never rewrite persisted
            # completion based on a model's prose or inferred intention.
            task_context["_delivery_outcome"] = turn["_delivery_outcome"]
        if turn.get("trigger") is not None:
            # Supply neutral unfinished-work/capability/error state instead of
            # inventing a human wake message or requiring a tool every turn.
            try:
                from .activity_context import snapshot
                turn["activity"] = snapshot(self, group, self._tool_schemas(),
                                            skills_root=Path(HIGAN_ROOT) / "skills")
            except Exception as exc:
                turn["activity"] = {"source": "activity_state", "available": False,
                                    "error": type(exc).__name__}
        # Full remains the unchanged deployment default. Light is an explicit,
        # per-turn schema-presentation choice, never a permissions/feature switch.
        from .runtime_settings import configured_tool_profile
        profile_options = configured_tool_profile()
        tool_profile = None
        if profile_options["mode"] == "light":
            from .tool_profile import ToolProfile
            tool_profile = ToolProfile(self._tool_schemas(), **profile_options)
            system += ("\nTool schema exposure is light for this turn. "
                       "Use tool_search to discover the complete catalog and "
                       "tool_describe to expose exact schemas on the next request. "
                       "All existing permissions and execution requirements still apply.")
        context_started = time.monotonic()
        prompt = self._request_context(event, group, text, has_image)
        logger.info("[higan] reply_phase phase=context seconds=%.3f chars=%d",
                    time.monotonic() - context_started, len(prompt))

        async def _llm(_sys, _prompt):
            """Retain legacy callback compatibility for the shared loop module."""
            return await self._llm_tools(_sys, _prompt, live_refs)

        async def _llm_messages(messages, allow_tools):
            """Persist frozen initial images and append tool image data after tool replies."""
            nonlocal images_initialized, submitted_ref_count
            from .task_delivery import task_active
            # Do not interrupt native tools already running; only suppress the
            # next model request after completion, pause, deletion or reclaim.
            if task_context is not None and not task_active(
                    task_context, self._tasks_mod().listing(only_active=False)):
                return "", []
            from .image_transcript import with_images
            pending_marker_submitted = None
            observed_url = {}
            marker_path = click_gate.pending_marker_path

            def convert_observed(ref):
                """Record only this checkpoint's successfully converted pixel URL."""
                url = self._as_llm_url(ref)
                if marker_path and str(ref) == marker_path and url:
                    observed_url["url"] = url
                return url

            images_started = time.monotonic()
            if not images_initialized:
                # Enrich the loop's original user exactly once, before its first request.
                converted = await asyncio.to_thread(
                    with_images, list(messages), list(initial_refs), convert_observed,
                    MAX_IMAGES_PER_CALL, initial=True,
                    source_records=turn.get("image_sources", []))
                messages[:] = converted
                images_initialized = True
            else:
                # The loop has appended every tool result; new images cannot break tool pairing.
                # Count tool observations, not filenames: a new screenshot may reuse
                # the same path with different bytes. Old data URLs stay immutable.
                new_refs = live_refs[submitted_ref_count:]
                converted = await asyncio.to_thread(
                    with_images, list(messages), list(new_refs), convert_observed,
                    MAX_IMAGES_PER_CALL)
                messages[:] = converted
                submitted_ref_count = len(live_refs)
            if observed_url:
                # Check the actual image URL in the outgoing request, not just a
                # path listed in metadata or a preview tool JSON response.
                for message in converted:
                    content = message.get("content")
                    if isinstance(content, list) and any(
                            isinstance(part, dict) and part.get("type") == "image_url"
                            and (part.get("image_url") or {}).get("url") == observed_url["url"]
                            for part in content):
                        pending_marker_submitted = marker_path
                        break
            logger.info("[higan] reply_phase phase=image_prepare seconds=%.3f",
                        time.monotonic() - images_started)
            if tool_profile is None:
                result = await self._llm_tools(system, prompt, [], messages, allow_tools)
            else:
                result = await self._llm_tools(system, prompt, [], messages, allow_tools,
                                               tool_schemas=tool_profile.schemas())
            if pending_marker_submitted:
                # A successful provider response proves the marked image was in
                # that model request; mere preview output never establishes this.
                await click_gate.mark_observed(pending_marker_submitted)
            # A completed same-scope send makes an unmarked no-tools completion
            # an internal draft, not a fresh finalization or a second send. Only
            # explicit delivery=new can request another public response.
            from .final_publication import has_public_delivery
            from .final_response import FINAL_RESPONSE_CONTRACT, ensure_final_response, publishable
            from .reply_selection import is_silence
            if (not result[1] and has_public_delivery(turn, group)
                    and not is_silence(result[0])
                    and not publishable(result[0], turn.get("messages") or [],
                                        allow_unattributed=not bool(turn.get("messages")))):
                return "", []
            # A no-tools completion is not automatically a public reply. Only an
            # explicit final envelope may cross this one shared publication edge.

            async def _repair_final(_private_draft):
                """Ask once from original evidence; never restate a private draft."""
                repair_messages = list(messages) + [
                    {"role": "system", "content": FINAL_RESPONSE_CONTRACT},
                ]
                return await self._llm_tools(system, prompt, [], repair_messages, False)

            return await ensure_final_response(
                result[0], result[1], turn.get("messages") or [], _repair_final,
                allow_unattributed=not bool(turn.get("messages")))

        async def _execute_tool(name, args):
            # 群文件通过 AstrBot 的 OneBot 客户端读取。
            """执行共享工具入口，并将媒体与观察结果接入本轮模型上下文。"""
            if name in ("group_files", "group_file_download"):
                try:
                    _api = event.bot.api
                    if name == "group_files":
                        _r = await _api.call_action("get_group_root_files",
                                                    group_id=int(group),
                                                    file_count=int(args.get("limit") or 20))
                        _files = ((_r or {}).get("files") or [])[: int(args.get("limit") or 20)]
                        _folders = (_r or {}).get("folders") or []
                        _out = []
                        for f in _files:
                            _out.append({"name": f.get("file_name"),
                                         "size": f.get("file_size"),
                                         "uploader": f.get("uploader"),
                                         "time": f.get("upload_time"),
                                         "id": f.get("file_id")})
                        return {"ok": True, "files": _out,
                                "folders": [{"name": d.get("folder_name"),
                                             "count": d.get("total_file_count"),
                                             "id": d.get("folder_id")} for d in _folders]}
                    _r = await _api.call_action("get_group_file_url",
                                                group_id=int(group),
                                                file_id=str(args.get("id") or ""))
                    return {"ok": True, "url": (_r or {}).get("url")}
                except Exception as exc:  # noqa: BLE001
                    return {"ok": False, "error": repr(exc)[:200]}
            if name in ("vm_click_preview", "vm_click_confirm", "vm_path_preview", "vm_path_confirm"):
                if not isinstance(args, dict):
                    return {"ok": False, "error": "invalid_click_arguments"}
                async def screenshot_for_checkpoint():
                    """Capture the real current screen, not an earlier tool path."""
                    return await self._vm_tool("vm_shot", {})

                if name in ("vm_click_preview", "vm_path_preview"):
                    if name == "vm_path_preview":
                        res = await click_gate.preview_path(args.get("action"),
                            args.get("label", ""), screenshot_for_checkpoint)
                    else:
                        res = await click_gate.preview(args.get("x"), args.get("y"),
                            args.get("label", ""), screenshot_for_checkpoint)
                    if res.get("ok") is True and res.get("path"):
                        live_refs.append(str(res["path"]))
                elif name == "vm_path_confirm":
                    # None is deliberate: the gate consumes the ticket, validates
                    # observed intent, then returns backend_unavailable before any
                    # input. Only trusted host code can install this async adapter.
                    executor = getattr(self, "_vm_pointer_executor", None)
                    # Explicit trusted-host protocol; legacy action-only callbacks
                    # retain their signature but cannot claim worker-queue freshness.
                    res = await click_gate.confirm_path(args.get("ticket"),
                        screenshot_for_checkpoint, executor,
                        action=args.get("action"), screen_sha256=args.get("screen_sha256"),
                        viewed=args.get("viewed"),
                        authorized_path_callable=getattr(executor, "execute_authorized", None))
                else:
                    async def click_after_checkpoint(x, y):
                        """Call the native mouse route only after pixel verification."""
                        return await self._vm_tool("vm_click", {"x": x, "y": y},
                                                   verified_click=True)

                    res = await click_gate.confirm(args.get("ticket"),
                        screenshot_for_checkpoint, click_after_checkpoint,
                        x=args.get("x"), y=args.get("y"),
                        screen_sha256=args.get("screen_sha256"), viewed=args.get("viewed"))
            else:
                res = await self._dispatch_tool(name, args, group, event=event)
            from .task_delivery import observe
            turn["_delivery_outcome"]["last_tools"].append(name)
            if name in ("read_forward", "forward_expand") and isinstance(res, dict):
                # A later explicit forward read must have the same visual input
                # pathway as a directly received forward, after its native tool
                # result. URLs alone in JSON are not evidence of visual access.
                for item in res.get("items") or []:
                    for media in item.get("media") or []:
                        if media.get("type") == "image":
                            ref = str(media.get("url") or media.get("file") or "")
                            if ref:
                                live_refs.append(ref)
            if name == "facts_read_image" and isinstance(res, dict) and res.get("ok"):
                # Feed saved pixels after the tool pair, without bloating the tool
                # archive or JSON transcript with base64. Reading is not sending.
                image = dict(res.get("image") or {})
                ref = image.pop("ref", None)
                if ref:
                    live_refs.append(ref)
                res = dict(res, image=image)
            if name == "workspace_read_image" and isinstance(res, dict) and res.get("ok"):
                # An explicit file-image observation uses the existing append-only
                # visual transport, not a URL-only claim of pixel understanding.
                image = res.get("image") or {}
                if image.get("ref"):
                    live_refs.append(str(image["ref"]))
            # VM 请求插件发送媒体时，通过插件媒体通道执行。
            try:
                if isinstance(res, dict) and res.get("deliver") == "plugin":
                    _p = str(res.get("path") or "")
                    if _p:
                        _delivered = await self._deliver_media(event, group, _p, str(res.get("send_as") or "image"))
                        # 记录媒体发送状态，避免组装回复时重复附加。
                        res = dict(res)
                        res["delivered"] = bool(_delivered)
                        if _delivered:
                            self._delivered_media = _p
                        logger.info("[higan] 插件已发送文件：%s" % _p)
            except Exception as _exc:  # noqa: BLE001
                logger.warning("[higan] 插件发送失败：%r", str(_exc)[:120])
            try:
                if name == "vm_shot" and isinstance(res, dict) and res.get("path"):
                    path = str(res["path"])
                    shots.append(path)
                    live_refs.append(path)      # 将图片加入模型的后续视觉输入。
                    logger.info("[higan] 截图已喂回模型：%s" % path)
            except Exception:  # noqa: BLE001
                pass
            # Media delivery completes after dispatch; inspect the final receipt,
            # not the intermediate request to deliver it through the plugin.
            observe(turn["_delivery_outcome"], name, res)
            if tool_profile is not None:
                # Promote only names from the immutable local catalog after a
                # successful describe; tool output cannot inject arbitrary schemas.
                tool_profile.observe(name, args, res)
            return res

        def _observe(observation):
            """Persist every attempted tool result before final answer, including group-file tools."""
            try:
                self._tool_evidence().append(
                    group, text, observation["tool_name"], observation["arguments"],
                    observation["result"], status=observation.get("status"),
                    observation_id=observation.get("id"))
            except Exception as exc:
                logger.warning("[higan] 工具观察落盘失败：%s", type(exc).__name__)

        async def _tool(name, args):
            """Only explicit state changes revoke work; failed sends cannot be called complete."""
            from .task_delivery import completion_failure
            if name == "task_done" and task_context is not None:
                failure = completion_failure(turn["_delivery_outcome"])
                if failure:
                    return {"ok": False, "error": failure,
                            "reason": "An attempted delivery has not been confirmed."}
            if task_context is not None:
                from .task_delivery import current_task, task_active
                current = self._tasks_mod().listing(only_active=False)
                if not task_active(task_context, current):
                    row = current_task(task_context, current)
                    return {"ok": False, "error": "task_not_active",
                            "status": row.get("status") if row else "unavailable"}
            return await _execute_tool(name, args)

        def completion_check(name, arguments, result):
            """Honor confirmed replies or this exact claim's persisted task completion."""
            if (name == "task_done" and task_context is not None
                    and isinstance(result, dict) and result.get("ok") is True):
                from .task_delivery import current_task
                row = current_task(task_context, self._tasks_mod().listing(only_active=False))
                if row is not None and row.get("status") == "done":
                    return True
            return (name == "reply_now" and isinstance(arguments, dict)
                    and arguments.get("finish") is True and isinstance(result, dict)
                    and result.get("ok") is True
                    and (result.get("delivered") is True or (local_context
                         and result.get("transport") == "local"
                         and result.get("status") == "local_recorded" and result.get("recorded") is True))
                    and result.get("finish") is True)

        try:
            _out, _used = await asyncio.wait_for(
                _loop.run(system, prompt, _llm, _tool, max_rounds=TOOL_ROUNDS,
                          llm_messages=_llm_messages, observation_callback=_observe,
                          observation_question=text, completion_check=completion_check),
                timeout=TOOL_TURN_TIMEOUT)
        except _loop.ModelCompletionUnavailable as exc:
            # The same transcript has already made its one no-tools finalization
            # attempt. Never disguise this as chat or start an independent decision.
            logger.warning("[higan] model_completion_unavailable reason=%s tool_attempts=%d",
                           exc.reason, exc.tool_attempt_count)
            if task_context is not None:
                raise RuntimeError("task_finalization_failed") from exc
            return ""
        except Exception as exc:  # noqa: BLE001
            logger.warning("[higan] 工具回路失败：%s", type(exc).__name__)
            if task_context is not None:
                raise RuntimeError("task_tool_loop_incomplete") from exc
            return await self._decide(event, group, text, has_image)
        if task_context is not None and _out == _loop.SAFE_FINAL:
            raise RuntimeError("task_finalization_failed")

        if _used:
            logger.info("[higan] 本轮用过工具：%s ｜截图 %d 张" % (_used, len(shots)))
        # Native loop already produced a result-aware answer. Never ask a second independent
        # model to rephrase it without the transcript (that could duplicate actions or lose evidence).
        # 模型可选择静默。
        # Successful tool attempts remain evidence for tasks even when the public
        # final is silent or an already-delivered acknowledgement.
        if task_context is not None:
            task_context["last_tools"] = list(_used)
        from .reply_selection import is_silence
        if is_silence(_out):
            logger.info("[higan] 她选择沉默（SILENT）✓")
            return ""
        _out = (_out or "").strip()
        if not _out:
            return ""
        # 媒体已发送时不补造文字说明。

        # Replace only the public JSON text field; a Windows screenshot path
        # contains backslashes and must be re-encoded before the shared decoder.
        if shots and "[发图]" in _out:
            payload = json.loads(_out)
            public_text = payload["text"]
            if "[发图]" in public_text:
                payload["text"] = public_text.replace("[发图]", "[发图:%s]" % shots[-1], 1)
                _out = json.dumps(payload, ensure_ascii=False)
                logger.info("[higan] 她决定发图：%s" % shots[-1])
        elif shots and "[发图" not in _out:
            logger.info("[higan] 本轮截图 %d 张，她选择不发（内部保留 ✓）" % len(shots))
        if not _out:
            return ""
        # An actual same-scope public send requires an explicit new final publication,
        # regardless of whether text or media tools produced it; repeated tools stay independent.
        from .final_publication import has_public_delivery
        if has_public_delivery(turn, group):
            from .final_publication import select as select_publication
            from .reply_selection import quote_target_ids
            records = turn.get("messages") or []
            publication = select_publication(_out, records, explicit=True,
                                             allow_unattributed=not records,
                                             allowed_quote_ids=quote_target_ids(records, turn.get("history"), scope=group, quote_history=turn.get("quote_history")))
            turn["_final_publication"] = publication
            # A public action and a subsequent completion are separate channels.
            # No text comparison, forced loop completion, or inferred intent is used.
            if not publication["publish"]:
                return ""
        logger.info("[higan] 工具优先通路产出回复（%d 字）" % len(_out))
        return _out

    async def _decide(self, event: AstrMessageEvent, group: str, text: str,
                      has_image: bool) -> str:
        """Read-only fallback: preserve context/evidence without dispatching a second tool loop."""
        refs, _ = await self._refs_for(event, group, text, has_image)
        prompt = self._request_context(event, group, text, has_image)
        from .conversation_context import system_prompt
        out = await self._llm_plain(system_prompt(self.persona), prompt, refs)
        from .reply_selection import is_silence
        if is_silence(out):
            return ""
        from .final_response import FINAL_RESPONSE_CONTRACT, ensure_final_response, FinalResponseUnavailable
        records = ((getattr(self, "_turn_context", {}) or {}).get(group, {}).get("messages") or [])

        async def _repair_plain(_private_draft):
            """Use the same read-only context; never echo the private draft."""
            messages = [{"role": "system", "content": system_prompt(self.persona)},
                        {"role": "user", "content": prompt},
                        {"role": "system", "content": FINAL_RESPONSE_CONTRACT}]
            return await self._llm_tools(system_prompt(self.persona), prompt, [], messages, False)

        try:
            result, _calls = await ensure_final_response(
                out, [], records, _repair_plain, allow_unattributed=not bool(records))
        except FinalResponseUnavailable:
            # Failure is silence, never the raw private draft or a second fallback.
            return ""
        return (result or "").strip()

    def _parse_decision(self, raw: str, require_action: bool = True):
        """从模型输出里取 JSON（三级容错 ）。

        ① 严格解析 ② 串内裸引号换成中文引号再解析 ③ 正则抽 action/segments
        任何一级成功就返回；全失败才返回 None（调用方再决定是否静默）
        """
        if not raw:
            return None
        match = re.search(r"\{.*\}", raw, re.S)
        cand = match.group(0) if match else raw

        # ① 严格
        try:
            data = json.loads(cand)
            if isinstance(data, dict) and (not require_action or "action" in data):
                return data
        except Exception:  # noqa: BLE001
            pass

        # 将字符串内部的裸双引号替换为中文引号，再尝试解析。
        try:
            fixed = re.sub(r'(?<=[^\s:,\[{])"(?=[^\s:,\]}])', "”", cand)
            data = json.loads(fixed)
            if isinstance(data, dict) and (not require_action or "action" in data):
                logger.info("[higan] 裁决修复成功（裸引号 → 中文引号）✓")
                return data
        except Exception:  # noqa: BLE001
            pass

        # 解析失败时按正则提取 action 与 segments。
        try:
            act = re.search(r'"action"\s*:\s*"(reply|silent)"', cand)
            if not act:
                return None
            segs: list = []
            m2 = re.search(r'"segments"\s*:\s*\[(.*?)\]', cand, re.S)
            if m2:
                segs = re.findall(r'"((?:[^"\\]|\\.)*)"', m2.group(1))
            if not segs:
                # 没有 segments 时提取引号内的中文文本。
                for s in re.findall(r'"((?:[^"\\]|\\.){2,})"', cand):
                    if re.search(r"[\u4e00-\u9fff]", s) and s not in (
                            "reply", "silent", "reason", "tone", "segments"):
                        segs.append(s)
            logger.info("[higan] 裁决正则修复：action=%s segments=%d 段 ✓"
                        % (act.group(1), len(segs)))
            return {"action": act.group(1),
                    "reason": "（正则修复）",
                    "segments": segs[:4]}
        except Exception:  # noqa: BLE001
            return None

    async def _deliver_media(self, event, scope: str, path: str, kind: str = "image"):
        """Deliver the requested actual media type, retaining confirmed-send semantics."""
        if not self._policy_allows(event, scope):
            return None
        if kind == "image":
            return await self._send(event, scope, "[发图:%s]" % path)
        from astrbot.api.message_components import Video, Record
        component = Video if kind == "video" else Record if kind == "record" else None
        if component is None or not Path(path).is_file():
            return None
        chain = MessageChain()
        chain.chain.append(component.fromFileSystem(path))
        try:
            await event.send(chain)
        except Exception as exc:
            logger.warning("[higan] 媒体发送失败：%s", type(exc).__name__)
            return None
        visible = "[视频]" if kind == "video" else "[语音]"
        self._record_visible_reply(scope, visible)
        return visible

    async def _send(self, event: AstrMessageEvent, group: str, reply: str,
                    quote_id=None, *, decoded: bool = False) -> str | None:
        """按共享协议发送文字、可选引用和媒体，并记录确认发送的结果。

        on_message 停止默认事件管线，因此这里使用 event.send 直接发送。
        引用由模型输出的有效消息号决定，不默认引用触发消息。
        """
        from .reply_selection import decode_reply, is_silence, quote_target_ids
        if not decoded:
            # Every raw send entry shares one protocol decoder. An empty current
            # batch may produce unattributed autonomous text, never fabricated IDs.
            # Already-decoded text is not parsed again: literal JSON examples stay literal.
            turn = (getattr(self, "_turn_context", {}) or {}).get(group, {})
            records = turn.get("messages") or []
            authorized_quotes = (quote_target_ids(
                records, turn.get("history"), scope=group,
                quote_history=turn.get("quote_history"))
                if (records or turn.get("history") or turn.get("quote_history")) else None)
            selection = decode_reply(reply, records, allow_unattributed=not records,
                                     allowed_quote_ids=authorized_quotes)
            if selection["error"]:
                logger.warning("[higan] 发送出口拒绝无效回复协议")
                return None
            reply = selection["text"]
            quote_id = selection.get("quote_id") or quote_id
        if is_silence(reply) or not self._policy_allows(event, group):
            return None
        # Accepted later chat is queued; it cannot cancel an active operation or delivery.
        #  发送前统一清洗：工具调用语法/DSML 一律不发到群里
        try:
            _clean_reply = self._strip_tool_syntax(reply)
            if not _clean_reply.strip():
                logger.info("[higan] 回复只剩工具语法，已拦下不发送")
                return
            reply = _clean_reply
            # Cleaning can expose a control token previously wrapped by tool syntax.
            if is_silence(reply):
                return None
            # 兜底：还有 <||DSML|| 残留就整行丢掉
            if "DSML" in reply:
                reply = "\n".join(
                    ln for ln in reply.splitlines() if "DSML" not in ln).strip()
                if not reply:
                    logger.info("[higan] 清洗后为空，不发送")
                    return
        except Exception as _sexc:  # noqa: BLE001
            logger.warning("[higan] 发送前清洗失败：%r", str(_sexc)[:100])

        # 识别撤回标记，在发送前撤回机器人上一条消息。
        if "[撤回]" in reply:
            reply = reply.replace("[撤回]", "")
            try:
                await self._recall_last(event, group, count=1)
            except Exception as _exc:  # noqa: BLE001
                logger.warning("[higan] 撤回异常（忽略）：%r", _exc)
        # 解析发图标记，将工具生成的图片交给媒体通道。
        _img_path = ""
        _img_hit = re.search(r"\[发图:([^\]]+)\]", reply or "")
        if _img_hit:
            _img_path = _img_hit.group(1).strip()
            reply = (reply or "").replace(_img_hit.group(0), "")
        m = STICKER_MARKER.search(reply)
        text = self._to_plain(STICKER_MARKER.sub("", reply))
        chain = MessageChain()
        has_image_output = False
        #  回复里的 `[at:QQ]` ⇒ 真正的 @
        try:
            text, _ats = self._at_components(text)
            # QQ renders an At component as ``@nickname``. Some model replies
            # immediately repeat the same nickname as plain text, producing
            # ``@nameName``. Remove only an exact, authoritative member-name
            # prefix after the component; never infer or broadly strip prose.
            _api = getattr(getattr(event, "bot", None), "api", None)
            _call_action = getattr(_api, "call_action", None)
            if _ats and callable(_call_action) and str(group).isascii() and str(group).isdigit():
                for _at in _ats:
                    _qq = str(getattr(_at, "qq", "") or "")
                    if not _qq.isascii() or not _qq.isdigit() or int(_qq) <= 0:
                        continue
                    try:
                        _member = await _call_action(
                            "get_group_member_info", group_id=int(group),
                            user_id=int(_qq), no_cache=False)
                        if not isinstance(_member, dict):
                            continue
                        _name = str(_member.get("card") or _member.get("nickname") or "").strip()
                        if _name and text.startswith(_name):
                            text = text[len(_name):]
                            break
                    except Exception as _exc:  # noqa: BLE001
                        logger.debug("[higan] @ 昵称去重查询失败：%s", type(_exc).__name__)
            for _a in _ats:
                chain.chain.append(_a)
        except Exception:  # noqa: BLE001
            pass
        # 仅在模型提供有效 quote 时添加引用。
        msg_id = quote_id or None
        # One shared lexical boundary consumes leading transport controls only.
        # Invalid task/hash IDs are never displayed or guessed into QQ reply IDs.
        from .reply_selection import extract_legacy_quote
        text, legacy_quote = extract_legacy_quote(text or "")
        msg_id = msg_id or legacy_quote
        if msg_id:
            try:
                chain.chain.append(Reply(id=msg_id))
            except Exception as exc:
                logger.warning("[higan] 构造引用失败：%r", exc)
        #  `[头像:me]` → 换成自己的 QQ（走既有头像通道 ）
        try:
            if "[头像:me]" in (text or ""):
                _selfq = str(event.get_self_id() or "")
                if _selfq:
                    text = text.replace("[头像:me]", "[头像:%s]" % _selfq)
        except Exception:  # noqa: BLE001
            pass
        # 解析头像标记并发送对应成员头像。
        av = AVATAR_MARKER.search(text)
        if av:
            text = self._to_plain(AVATAR_MARKER.sub("", text))
            try:
                avatar_file = await self._member_avatar(av.group(1))
                if avatar_file:
                    chain.chain.append(Image.fromFileSystem(str(avatar_file)))
                    has_image_output = True
                    logger.info("[higan] 发出成员 %s 的头像" % av.group(1))
            except Exception as exc:
                logger.warning("[higan] 取成员头像失败：%r", exc)
        # `[删表情:关键词]` → 模型主动丢掉没用的图（只移出索引，不删文件）
        drop = DELETE_STICKER_MARKER.search(text)
        if drop:
            text = self._to_plain(DELETE_STICKER_MARKER.sub("", text))
            keys = self._split_keywords(drop.group(1))
            victims = [s for s in self.manifest
                       if any(k and k in (s.get("desc") or "") for k in keys)]
            for item in victims:
                self.manifest.remove(item)
                logger.info("[higan] 按模型要求丢掉表情包：%s（%s）"
                            % (item["file"], (item.get("desc") or "")[:20]))
            if victims:
                self._save_manifest()
        # `[下图:关键词或直链]` → 下载一张图并发出去（搜图/成员头像都支持）
        dl = DOWNLOAD_IMG_MARKER.search(text)
        if dl:
            text = self._to_plain(DOWNLOAD_IMG_MARKER.sub("", text))
            arg = dl.group(1).strip()
            # 支持 `[下图:头像 123456]` 这种写法：走成员头像
            qq = re.search(r"头像\D*(\d{5,12})", arg)
            if qq:
                path = await self._member_avatar(qq.group(1))
            else:
                path = await self._download_image(arg)
            if path:
                chain.chain.append(Image.fromFileSystem(str(path)))
                has_image_output = True
                logger.info("[higan] 已下载并发出图片：%s（%s）" % (path.name, arg[:30]))
            else:
                logger.info("[higan] 下载图片失败/无结果：%s" % arg[:30])
        if _img_path:
            try:
                if os.path.exists(_img_path):
                    chain.chain.append(Image.fromFileSystem(_img_path))
                    has_image_output = True
                    logger.info("[higan] 发送工具生成的图片：%s" % _img_path)
                else:
                    logger.warning("[higan] 要发的图不存在：%s" % _img_path)
            except Exception as _exc:  # noqa: BLE001
                logger.warning("[higan] 挂图失败：%r", _exc)
        text = SEARCH_MARKER.sub("", text).strip()
        if text:
            chain.message(text)
        if m:
            context = "\n".join(self._recent.get(group, [])[-6:]) or text
            keywords = self._split_keywords(m.group(1))       # 支持多个关键词
            sticker = await self._pick_sticker_reviewed(keywords, context)
            if sticker:
                path = STICKER_DIR / sticker["file"]
                if path.exists():
                    chain.chain.append(Image.fromFileSystem(str(path)))
                    has_image_output = True
                    logger.info("[higan] 发表情包 %s（关键词=%s，描述=%s）"
                                % (sticker["file"], keywords, sticker.get("desc")))
                else:
                    logger.warning("[higan] 表情包文件缺失：%s" % path)
        if (text.strip() and is_silence(text)) or (not text and not has_image_output) or not self._policy_allows(event, group):
            return None
        try:
            # 保存已发送消息 ID，供显式撤回使用。
            _sent = await event.send(chain)
        except Exception as exc:
            logger.error("[higan] 发送失败：%s", type(exc).__name__)
            return None
        visible = text + (" [图片]" if has_image_output else "")
        from .local_runtime import is_local_event, local_route
        from .task_event import TaskEvent
        if local_route(group, getattr(event, "unified_msg_origin", None)) and (
                is_local_event(event, self) or (isinstance(event, TaskEvent) and event.plugin is self)):
            # LocalEvent/TaskEvent already persisted exactly once through the runtime.
            if not (isinstance(_sent, dict) and _sent.get("transport") == "local"
                    and _sent.get("status") == "local_recorded" and _sent.get("recorded") is True):
                return None
            delivery = dict(_sent, ok=True, delivered=False, destination_scope=str(group))
            turn = getattr(self, "_turn_context", {}).get(group, {})
            turn.setdefault("_deliveries", []).append({"kind": "assistant_delivery", "body": visible,
                                                       "delivery": delivery})
            from .history_window import append_history
            self._history_origins = getattr(self, "_history_origins", {})
            append_history(self._recent, self._history_origins, group, "彼岸酱：" + visible.strip())
            return visible.strip()
        from .qq_delivery import _mention
        from .autonomous_turn import AutonomyEvent
        from .task_event import TaskEvent
        _ids, native_ids = [], []
        try:
            # Extract once from the actual send return, before committing its source record.
            for _item in (_sent or []):
                _mid = getattr(_item, "message_id", None)
                if _mid is None and isinstance(_item, dict):
                    _mid = _item.get("message_id")
                if _mid:
                    _ids.append(_mid)
                try:
                    native_ids.append(_mention(_mid))
                except ValueError:
                    # Legacy adapter identifiers remain recall-compatible, not native proof.
                    pass
            if _ids:
                self._last_sent.setdefault(group, []).extend(_ids)
                del self._last_sent[group][:-5]
                logger.info("[higan] 已发送，message_id=%s" % ",".join(map(str, _ids)))
        except Exception as exc:
            logger.warning("[higan] 发送成功但回执记录失败：%s", type(exc).__name__)
        turn = getattr(self, "_turn_context", {}).get(group, {})
        # Quote and mention provenance comes only from components actually sent.
        quote_ids = [str(part.id) for part in chain.chain
                     if isinstance(part, Reply) and getattr(part, "id", None)]
        mentions = [{"id": str(part.qq), "display_name": ""} for part in chain.chain
                    if isinstance(part, At) and getattr(part, "qq", None)]
        sender_id, sender_name = "", ""
        try:
            getter = getattr(event, "get_self_id", None)
            sender_id = str(getter() or "") if callable(getter) else ""
            getter = getattr(event, "get_self_name", None)
            sender_name = str(getter() or "") if callable(getter) else ""
        except Exception as exc:
            logger.warning("[higan] 已发送回复身份读取失败：%s", type(exc).__name__)
        source = {"kind": "autonomous" if isinstance(event, AutonomyEvent)
                  else "task" if isinstance(event, TaskEvent) else "foreground"}
        delivery = {"status": "native_confirmed" if native_ids else "observed_send_return",
                    "message_ids": native_ids, "destination_scope": str(group),
                    "source": dict(source), "reply_to": quote_ids,
                    "input_message_ids": [item["id"] for item in turn.get("messages", [])
                                          if isinstance(item, dict) and "id" in item],
                    "sent_at": datetime.now(timezone.utc).isoformat()}
        source_record = {"kind": "assistant_delivery",
                         "id": native_ids[0] if len(native_ids) == 1 else "",
                         "sender": {"id": sender_id, "display_name": sender_name},
                         "body": visible.strip(), "mentions": mentions,
                         "quote": {"id": quote_ids[0]} if quote_ids else None,
                         "source": source, "delivery": delivery}
        if len(native_ids) > 1:
            source_record["message_ids"] = list(native_ids)
        self._record_visible_reply(group, visible, record=source_record)
        try:
            try:  #  记住她最近说过的话，防重复
                _mine = getattr(self, "_my_recent", None)
                if _mine is None:
                    _mine = self._my_recent = {}
                _lst = _mine.setdefault(group, [])
                _lst.append(self._to_plain(STICKER_MARKER.sub("", reply))[:200])
                del _lst[:-3]
            except Exception:  # noqa: BLE001
                pass
        except Exception as exc:
            logger.warning("[higan] 发送成功但回执记录失败：%s", type(exc).__name__)
        return visible.strip()

    async def _recall_last(self, event: AstrMessageEvent, group: str,
                           count: int = 1) -> list:
        """通过 AstrBot 的 bot 连接撤回机器人最近发出的消息。"""
        ids = list(getattr(self, "_last_sent", {}).get(group) or [])[-count:]
        done = []
        bot = getattr(event, "bot", None)
        for mid in reversed(ids):
            ok = False
            for name in ("call_action", "api"):
                fn = getattr(bot, name, None)
                if not callable(fn):
                    continue
                try:
                    await fn("delete_msg", message_id=mid)
                    ok = True
                    break
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[higan] 撤回 %s 失败（%s）：%r" % (mid, name, exc))
            done.append({"message_id": mid, "ok": ok})
            if ok:
                self._last_sent[group] = [x for x in (self._last_sent.get(group) or [])
                                          if x != mid]
        logger.info("[higan] 撤回结果：%s" % done)
        return done

    async def _archive_images(self, event: AstrMessageEvent, group: str) -> list:
        """将收到的图片归档到机器人虚拟机的 images/inbox，供工具回看。"""
        import shutil as _shutil
        from pathlib import Path as _Path
        out_dir = _Path(HIGAN_ROOT) / "images" / "inbox"
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[higan] 建归档目录失败：%r", exc)
            return []
        saved = []
        for idx, ref in enumerate(self._image_refs(event) or []):
            try:
                raw = str(ref)
                src = _Path(raw[7:] if raw.startswith("file://") else raw)
                if not src.exists() or not src.is_file():
                    continue
                name = "%s-%d-%s" % (group.replace(":", "_"), idx, src.name)
                dst = out_dir / name
                _shutil.copy2(src, dst)
                saved.append(str(dst))
            except Exception as exc:  # noqa: BLE001
                logger.warning("[higan] 归档单张失败：%r", exc)
        if saved:
            logger.info("[higan] 已归档 %d 张图到她的虚拟机：%s" % (len(saved), out_dir))
        return saved

    # ----------------------------------------------------- 上网搜索（工具）

    @staticmethod
    def _search_queries(query: str) -> list[str]:
        """规范化用户查询并去重，不凭空添加领域专用词。"""
        raw = re.sub(r"\s+", " ", (query or "").strip())
        if not raw:
            return []
        candidates = [raw]
        # 引号、全角标点和中英文空格差异常导致同一查询重复请求；这里只做无语义改写。
        compact = re.sub(r"[“”‘’]", '"', raw)
        compact = re.sub(r"[，、；]+", " ", compact)
        compact = re.sub(r"\s+", " ", compact).strip()
        if compact and compact not in candidates:
            candidates.append(compact)
        return candidates[:SEARCH_QUERY_MAX]


    @classmethod
    def _web_search(cls, query: str, limit: int = SEARCH_TOP_N) -> list[dict]:
        """Read structured RSS before HTML fallback; preserve query and engine ranking, not keyword policy."""
        import html
        import urllib.parse
        import urllib.request
        import xml.etree.ElementTree as ET

        candidates = cls._search_queries(query)
        last_error = None
        for candidate in candidates:
            parameters = urllib.parse.urlencode({"q": candidate, "count": limit})
            # RSS avoids brittle assumptions about JavaScript and HTML anchor attribute order.
            for base, is_feed in (("https://www.bing.com/search?format=rss&", True),
                                  ("https://cn.bing.com/search?", False)):
                try:
                    req = urllib.request.Request(base + parameters, headers={
                        "User-Agent": "Mozilla/5.0", "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})
                    with urllib.request.urlopen(req, timeout=SEARCH_TIMEOUT) as response:
                        page = response.read(800000).decode("utf-8", "replace")
                    parsed = []
                    if is_feed:
                        root = ET.fromstring(page)
                        for item in root.findall(".//item"):
                            parsed.append({"title": item.findtext("title", "")[:200],
                                           "url": item.findtext("link", ""),
                                           "snippet": html.unescape(re.sub(r"<[^>]+>", " ", item.findtext("description", "")))[:1000]})
                    else:
                        for item in re.findall(r'<li[^>]*class=["\'][^"\']*\bb_algo\b[^"\']*["\'][^>]*>(.*?)</li>', page, re.S | re.I):
                            heading = re.search(r"<h2[^>]*>(.*?)</h2>", item, re.S | re.I)
                            anchor = re.search(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', heading.group(1), re.S | re.I) if heading else None
                            if not anchor:
                                continue
                            paragraph = re.search(r"<p[^>]*>(.*?)</p>", item, re.S | re.I)
                            parsed.append({"title": html.unescape(re.sub(r"<[^>]+>", "", anchor.group(2)))[:200],
                                           "url": html.unescape(anchor.group(1)),
                                           "snippet": html.unescape(re.sub(r"<[^>]+>", "", paragraph.group(1)))[:1000] if paragraph else ""})
                    # Only validate structure/duplicates here. Model evaluates semantic relevance from evidence.
                    seen = set()
                    results = []
                    for item in parsed:
                        url = item.get("url") or ""
                        if not item.get("title") or urllib.parse.urlsplit(url).scheme not in {"http", "https"} or url in seen:
                            continue
                        seen.add(url)
                        results.append(item)
                        if len(results) >= limit:
                            return results
                    if results:
                        return results
                except Exception as exc:
                    last_error = exc
                    logger.warning("[higan] 搜索源不可用（%s）：%s", "rss" if is_feed else "html", type(exc).__name__)
        if last_error is not None:
            raise RuntimeError("search_sources_unavailable") from last_error
        return []

    @staticmethod
    def _fetch_page(url: str, limit: int = PAGE_TEXT_LIMIT) -> str:
        """读取公开网页并剔除脚本、样式和标签，返回供模型阅读的正文。"""
        import html
        import ipaddress
        import socket
        import urllib.parse
        import urllib.request

        def validate_public(target):
            """Reject credentials, non-HTTP URLs and non-public DNS targets, including redirects."""
            parsed = urllib.parse.urlsplit(target)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("public_http_url_required")
            if parsed.port not in (None, 80, 443):
                raise ValueError("public_web_port_required")
            addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
            if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
                raise ValueError("non_public_address")

        class PublicRedirect(urllib.request.HTTPRedirectHandler):
            """Apply the same public-address rule to every redirect before following it."""

            def redirect_request(self, request, response, code, message, headers, target):
                """Validate redirected destinations, not just the first requested URL."""
                validate_public(target)
                return super().redirect_request(request, response, code, message, headers, target)

        validate_public(url)
        req = urllib.request.Request(url, headers={
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0"),
            "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
                       "image/avif,image/webp,*/*;q=0.8"),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Referer": "https://cn.bing.com/",
            "Upgrade-Insecure-Requests": "1",
        })
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), PublicRedirect())
        with opener.open(req, timeout=SEARCH_TIMEOUT) as resp:
            if resp.headers.get_content_type() not in {"text/html", "text/plain", "application/xhtml+xml"}:
                raise ValueError("unsupported_content_type")
            raw = resp.read(800_000).decode(resp.headers.get_content_charset() or "utf-8", "replace")
        text = re.sub(r"<script.*?</script>|<style.*?</style>|<!--.*?-->", " ",
                      raw, flags=re.S)
        text = html.unescape(re.sub(r"<[^>]+>", " ", text))
        return re.sub(r"\s+", " ", text).strip()[:limit]

    @staticmethod
    def _format_results(results: list[dict]) -> str:
        """把搜索结果整理成给模型看的文本。"""
        return "\n".join("%d. %s\n   %s\n   %s"
                         % (i, r["title"], r["snippet"], r["url"])
                         for i, r in enumerate(results, 1))

    @staticmethod
    def _web_image_search(query: str, limit: int = 6) -> list[str]:
        """Use the same metadata search as the native tool; legacy markers keep their signature."""
        from .image_search import search
        result = search(query, limit)
        if not result.get("ok"):
            raise RuntimeError("image_search_unavailable")
        return [item["image_url"] for item in result.get("images", [])]

    async def _download_image(self, ref: str) -> Path | None:
        """将图片直链或关键词搜索到的图片下载到 data/downloads，供发送和复用。"""
        target = (ref or "").strip()
        if not target:
            return None
        if not target.startswith("http"):
            try:
                found = await asyncio.wait_for(
                    asyncio.to_thread(self._web_image_search, target, 3),
                    timeout=SEARCH_TIMEOUT + 5)
            except Exception as exc:
                logger.warning("[higan] 搜图失败（%s）：%r", target[:30], exc)
                return None
            if not found:
                logger.info("[higan] 搜图没找到：%s" % target[:30])
                return None
            target = found[0]
        try:
            raw = await asyncio.wait_for(asyncio.to_thread(self._read_image, target),
                                         timeout=SEARCH_TIMEOUT + 5)
        except Exception as exc:
            logger.warning("[higan] 下载图片失败（%s）：%r", target[:60], exc)
            return None
        if not raw or len(raw) < 512 or len(raw) > MAX_DOWNLOAD_BYTES:
            logger.info("[higan] 图片不合适（%d 字节），跳过" % len(raw or b""))
            return None
        suffix = (".gif" if raw[:3] == b"GIF" else ".png" if raw[:4] == b"\x89PNG"
                  else ".webp" if raw[:4] == b"RIFF" else ".jpg")
        digest = hashlib.sha1(raw).hexdigest()[:16]
        out = DOWNLOAD_DIR / ("%s%s" % (digest, suffix))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(raw)
        logger.info("[higan] 已下载图片 %s（%d 字节，来源 %s）"
                    % (out.name, len(raw), target[:60]))
        return out

    async def _handle_search(self, provider, reply: str, refs: list[str],
                             event: AstrMessageEvent) -> str:
        """处理搜索标记，读取搜索结果页面正文后交回模型生成回复。"""
        group = str(event.get_group_id() or "") or "private:" + str(event.get_sender_id())
        request_context = self._request_context(event, group, event.get_message_str() or "", bool(refs))
        m = SEARCH_MARKER.search(reply)
        if not m:
            return reply
        query = m.group(1).strip()
        logger.info("[<BOT_ID>] 模型要求搜索：%s", query)
        results: list[dict] = []
        queries = self._search_queries(query)
        logger.info("[<BOT_ID>] 搜索候选词：%s", " | ".join(queries))
        try:
            results = await asyncio.wait_for(
                asyncio.to_thread(self._web_search, query), timeout=SEARCH_TIMEOUT * SEARCH_QUERY_MAX + 5)
        except Exception as exc:
            logger.warning("[<BOT_ID>] 搜索失败：%r", exc)
        if not results:
            logger.info("[<BOT_ID>] 搜索无相关结果：query=%s", query)
            context = json.loads(request_context)
            context["observations"].append({"source": "web_search", "arguments": {"query": query},
                "result": {"status": "no_usable_evidence", "results": []}})
            prompt = json.dumps(context, ensure_ascii=False, allow_nan=False)
            return await self._call_llm(provider, prompt, system_prompt(self.persona), refs)
        logger.info("[<BOT_ID>] 搜索得到相关结果：%d 条", len(results))

        # 打开网页读正文：多试几个站（百度百科/知乎等会 403），
        # 并发拉取，取前 PAGE_FETCH_TOP 个成功的。
        pages: list[dict] = []
        candidates = results[:PAGE_FETCH_TRY]
        try:
            bodies = await asyncio.gather(*[
                asyncio.wait_for(asyncio.to_thread(self._fetch_page, r["url"]),
                                 timeout=SEARCH_TIMEOUT + 5)
                for r in candidates], return_exceptions=True)
            for r, body in zip(candidates, bodies):
                if isinstance(body, str) and len(body) > 80:
                    pages.append({"title": r["title"], "url": r["url"], "body": body})
                    logger.info("[<BOT_ID>] 已打开网页：%s（%d 字）"
                                % (r["url"][:60], len(body)))
                elif isinstance(body, Exception):
                    logger.info("[<BOT_ID>] 网页打不开（跳过）：%s → %s"
                                % (r["url"][:50], type(body).__name__))
                if len(pages) >= PAGE_FETCH_TOP:
                    break
        except Exception as exc:
            logger.warning("[<BOT_ID>] 打开网页失败：%r", exc)

        # Legacy marker search shares the same data envelope and persona/protocol contract.
        context = json.loads(request_context)
        context["observations"].append({"source": "web_search", "arguments": {"query": query},
            "result": {"status": "results_available", "results": results, "pages": pages}})
        prompt = json.dumps(context, ensure_ascii=False, allow_nan=False)
        out = await self._call_llm(provider, prompt, system_prompt(self.persona), refs)
        logger.info("[<BOT_ID>] 搜索后回复：%s" % (out[:60] or "（空）"))
        return out


    # ------------------------------------------------- 管理员命令（改关键词）

    async def cmd_list_stickers(self, event: AstrMessageEvent) -> None:
        """列出表情包与序号（只有主人可用）。用法：`/表情列表 [页码]`。"""
        if not self._is_owner(event):
            return
        try:
            page = max(1, int((event.get_message_str() or "").split()[-1]))
        except ValueError:
            page = 1
        per = 20
        items = self.manifest[(page - 1) * per: page * per]
        if not items:
            event.set_result(MessageEventResult().message("这一页没有表情包喵"))
            return
        lines = ["表情包（第 %d 页，共 %d 张）：" % (page, len(self.manifest))]
        for i, item in enumerate(items, (page - 1) * per + 1):
            lines.append("%d. %s" % (i, (item.get("desc") or "（无标签）")[:24]))
        lines.append("改标签：/表情改 <序号> <新关键词>（可用、分隔多个）")
        lines.append("删除：/表情删 <序号>")
        event.set_result(MessageEventResult().message("\n".join(lines)))

    async def cmd_edit_sticker(self, event: AstrMessageEvent) -> None:
        """修改某张表情包的关键词（只有主人可用）。用法：`/表情改 <序号> <关键词>`。

        关键词可用 `、` 或 `,` 分隔多个，便于模型模糊搜索命中。
        """
        if not self._is_owner(event):
            return
        parts = (event.get_message_str() or "").split(maxsplit=2)
        if len(parts) < 3:
            event.set_result(MessageEventResult().message(
                "用法：/表情改 <序号> <关键词>（多个关键词用、分隔）"))
            return
        try:
            idx = int(parts[1])
        except ValueError:
            event.set_result(MessageEventResult().message("序号得是数字喵"))
            return
        if not (1 <= idx <= len(self.manifest)):
            event.set_result(MessageEventResult().message("序号超范围喵（共 %d 张）"
                                                          % len(self.manifest)))
            return
        item = self.manifest[idx - 1]
        old = item.get("desc") or ""
        item["desc"] = "、".join(self._split_keywords(parts[2]))[:60]
        self._save_manifest()
        logger.info("[higan] 主人改了关键词：%s 「%s」→「%s」"
                    % (item["file"], old, item["desc"]))
        event.set_result(MessageEventResult().message(
            "改好了喵～\n%s\n旧：%s\n新：%s" % (item["file"], old[:30], item["desc"])))

    async def cmd_del_sticker(self, event: AstrMessageEvent) -> None:
        """把某张表情包移出索引（只有主人可用）。用法：`/表情删 <序号>`。

        只移出索引、不删文件（避免误删，且随时可以再加回来）。
        """
        if not self._is_owner(event):
            return
        parts = (event.get_message_str() or "").split()
        try:
            idx = int(parts[-1])
        except (ValueError, IndexError):
            event.set_result(MessageEventResult().message("用法：/表情删 <序号>"))
            return
        if not (1 <= idx <= len(self.manifest)):
            event.set_result(MessageEventResult().message("序号超范围喵"))
            return
        item = self.manifest.pop(idx - 1)
        self._save_manifest()
        logger.info("[higan] 主人移出表情包：%s（%s）" % (item["file"], item.get("desc")))
        event.set_result(MessageEventResult().message(
            "已移出索引（文件保留）：%s" % item["file"]))

    def _is_owner(self, event: AstrMessageEvent) -> bool:
        """Authenticate a native owner or this instance's private local capability."""
        from .local_runtime import is_local_event
        return is_local_event(event, self) or str(event.get_sender_id() or "") in OWNER_QQS

    # --------------------------------------------------------- 表情包收集

    async def _collect_images(self, event: AstrMessageEvent, group: str,
                              images: list) -> None:
        """把群友发的图存进表情包库，并用视觉模型打描述做索引。

        关键：AstrBot 会把收到的图下载到 `data/temp/`，此时组件里是本地路径
        （`file://` 或绝对路径）而不是 http URL —— 两种都必须处理，否则一张都收不到。
        """
        for comp in images[:2]:                        # 一条消息最多收 2 张
            src = getattr(comp, "url", None) or getattr(comp, "file", None) \
                or getattr(comp, "path", None)
            if not src:
                continue
            src = str(src)
            try:
                raw = await asyncio.to_thread(self._read_image, src)
            except Exception as exc:
                logger.warning("[higan] 取图失败（%s）：%r", src[:60], exc)
                continue
            if not raw or len(raw) < 1024 or len(raw) > MAX_STICKER_BYTES:
                continue
            digest = hashlib.sha1(raw).hexdigest()[:16]
            suffix = (".gif" if raw[:3] == b"GIF"
                      else ".png" if raw[:4] == b"\x89PNG"
                      else ".webp" if raw[:4] == b"RIFF"
                      else ".jpg")
            path = STICKER_DIR / ("%s%s" % (digest, suffix))
            if path.exists():
                continue
            path.write_bytes(raw)
            desc = await self._describe(self._as_url(src, path))
            self._register_sticker(path, desc)
            logger.info("[higan] 收集表情包 %s（%s）" % (path.name, desc))

    @staticmethod
    def _read_image(src: str) -> bytes:
        """读一张图：支持 http(s) URL 与本地路径（含 `file://` 形式）。"""
        import urllib.parse
        import urllib.request
        if src.startswith("http://") or src.startswith("https://"):
            req = urllib.request.Request(src, headers={"User-Agent": "astrbot-higan/1.1"})
            with urllib.request.urlopen(req, timeout=25) as resp:
                return resp.read()
        path = urllib.parse.unquote(src[7:] if src.startswith("file://") else src)
        if not os.path.isabs(path):                    # 相对 AstrBot 工作目录
            path = os.path.join(os.getcwd(), path)
        with open(path, "rb") as fh:
            return fh.read()

    @staticmethod
    def _as_url(src: str, saved: Path) -> str:
        """给视觉模型用的图片来源：优先 http，否则用刚存下的本地文件。"""
        return src if src.startswith("http") else str(saved)

    async def _member_avatar(self, qq: str) -> Path | None:
        """从 QQ 头像接口获取成员头像并缓存到本地，同一 QQ 复用缓存。"""
        qq = str(qq or "").strip()
        if not qq.isdigit():
            return None
        path = AVATAR_CACHE.parent / ("member_avatar_%s.jpg" % qq)
        if path.exists() and path.stat().st_size > 1024:
            return path
        try:
            url = "https://q1.qlogo.cn/g?b=qq&nk=%s&s=640" % qq
            raw = await asyncio.to_thread(self._read_image, url)
            if not raw or len(raw) < 1024:
                return None
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            return path
        except Exception as exc:
            logger.warning("[higan] 拉取成员 %s 头像失败：%r", qq, exc)
            return None

    # --------------------------------------------- 群成员变动（加群/退群）

    async def _watch_members(self) -> None:
        """按 MEMBER_WATCH_INTERVAL 轮询成员名单，将加入和退出变化写入群上下文。"""
        while True:
            try:
                await asyncio.sleep(MEMBER_WATCH_INTERVAL)
                # No configured groups means no polling, not authorization to
                # inspect every group belonging to an existing private account.
                if not MEMBER_WATCH_GROUPS:
                    continue
                groups = await self._onebot("get_group_list", {})
                for g in (groups.get("data") or []):
                    gid = str(g.get("group_id") or "")
                    if not gid or (MEMBER_WATCH_GROUPS and gid not in MEMBER_WATCH_GROUPS):
                        continue
                    await self._diff_members(gid, g.get("group_name") or gid)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("[higan] 成员变动监听出错：%r", exc)

    async def _diff_members(self, group: str, group_name: str) -> None:
        """拉一次成员名单，与上次比对，把变化写进该群的上下文。"""
        resp = await self._onebot("get_group_member_list", {"group_id": int(group)})
        members = resp.get("data") or []
        if not members:
            return
        current = {str(m.get("user_id")): (m.get("card") or m.get("nickname") or "")
                   for m in members if m.get("user_id")}
        prev = self._member_snapshot.get(group)
        self._member_snapshot[group] = current
        if prev is None:                      # 首次只建基线，不刷"全员加入"
            logger.info("[higan] 群 %s 成员基线：%d 人" % (group, len(current)))
            return
        joined = [qq for qq in current if qq not in prev]
        left = [qq for qq in prev if qq not in current]
        from .history_window import append_history
        self._history_origins = getattr(self, "_history_origins", {})
        for qq in joined:
            line = "【群动态】%s（QQ %s）加入了群聊" % (current[qq] or "某人", qq)
            append_history(self._recent, self._history_origins, group, line)
            logger.info("[higan] %s" % line)
        for qq in left:
            line = "【群动态】%s（QQ %s）退出了群聊" % (prev[qq] or "某人", qq)
            append_history(self._recent, self._history_origins, group, line)
            logger.info("[higan] %s" % line)

    @staticmethod
    async def _onebot(action: str, payload: dict) -> dict:
        """调 NapCat 的 OneBot HTTP 接口（只读用途）。"""
        import urllib.request
        from .runtime_settings import onebot_connection
        endpoint, token = onebot_connection(ONEBOT_BASE)
        if not endpoint:
            return {"status": "failed", "retcode": -1, "error": "qq_transport_unavailable"}
        if not isinstance(action, str) or not re.fullmatch(r"[A-Za-z0-9_]+", action):
            return {}
        url = "%s/%s" % (endpoint, action)
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST")
        req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", "Bearer " + token)

        def _call() -> dict:
            """同步读取 OneBot JSON 响应，由外层在线程执行。"""
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))

        try:
            return await asyncio.to_thread(_call)
        except Exception as exc:
            logger.warning("[higan] OneBot %s 调用失败：%r", action, exc)
            return {}

    async def _describe(self, image_ref: str) -> str:
        """用视觉模型为表情包生成中文关键词描述，支持 HTTP 与本地图片。"""
        text = await self._describe_image(
            image_ref, "用不超过 10 个字描述这张表情包表达的情绪或含义（只输出描述）。")
        return text or "表情包"

    async def _describe_image(self, image_ref: str, prompt: str) -> str:
        """为 HTTP 或本地图片生成视觉描述；本地图片压缩后转为 data URL。"""
        provider = self.context.get_using_provider()
        if provider is None:
            return ""
        try:
            if image_ref.startswith("http"):
                refs = [image_ref]
            else:
                path = Path(image_ref)
                refs = [self._data_url(path) if path.exists() else image_ref]
        except Exception as exc:
            logger.warning("[higan] 准备图片失败（%s）：%r", image_ref[:50], exc)
            return ""
        try:
            resp = await asyncio.wait_for(
                provider.text_chat(prompt=prompt, system_prompt="你是图像描述助手。",
                                   image_urls=refs),
                timeout=REVIEW_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning("[higan] 图像描述超时（>%ds）" % REVIEW_TIMEOUT)
            return ""
        except Exception as exc:
            logger.warning("[higan] 图像描述失败（%s）：%r", image_ref[:50], exc)
            return ""
        return (getattr(resp, "completion_text", "") or "").strip()[:60]

    async def terminate(self) -> None:
        """Let the replacement instance drain work; do not cancel an active model/send here.

        Framework calls terminate before unbinding old handlers. Waiting/canceling
        here races new admission; the replacement initialize observes exact old
        owners after unbind and drains every active handler/background operation.
        """
        # Stop admission now; the replacement drains existing local handlers too.
        self._stopping = True
        runtime = getattr(self, "_local_runtime", None)
        if runtime is not None:
            await runtime.close_acceptor()
        self._save_manifest()
        logger.info("[higan] 插件已卸载，表情包库 %d 张" % len(self.manifest))