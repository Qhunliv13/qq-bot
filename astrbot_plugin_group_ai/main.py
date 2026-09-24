"""群聊 AI 插件：自主回复决策 + 看图 + 表情包 + 联网查询（AstrBot）。


  1. **不要固定概率**：由**模型自己决定**要不要接话（不是随机掷骰子）。
  2. **表情包**：能收（群友发的表情存成库）、能发（模型想发就发）。
  3. **回复要短**：**尽量 20 字以内**。
  4. **别太频繁**：默认沉默，且同群有间隔与频率上限。
  5. **快速连发时回复不能乱序**：同群消息**串行处理**，且"只回最新一条"。

设计要点：
  · `on_message` 处理所有群消息；先让模型判断要不要接话（输出 `SILENT` 就不回）。
  · **顺序保证**：每个群一个处理循环（`_busy`/`_pending`），同一时刻只处理一条，
    处理期间新到的消息只保留**最后一条**（latest-wins）。这同时解决了
    "先发的后回"乱序（并发调 LLM 导致）和积压后一次性刷屏两个问题。
  · **表情包收集**：群友发图 → 存进 `data/stickers/`。注意 AstrBot 会把收到的图
    **下载到 `data/temp/`**，此时组件里是**本地路径**而不是 http URL —— 两种都要处理
    。
  · **表情包扫描**：启动时扫描目录，把**手动放进去的**图也登记进索引（并异步补描述）。
  · **表情包发送**：模型在回复里写 `[表情包:关键词]`，插件按描述匹配一张发出去。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.message_components import At, Image, Reply
from astrbot.api.star import Context, Star, register
from astrbot.core.message.message_event_result import MessageChain

PLUGIN_ID = "astrbot_plugin_group_ai"
#: 日志前缀。全插件统一用它，便于 grep；改名只需改这一处。
LOG_TAG = "[群聊]"
STICKER_DIR = Path("data/stickers")
MANIFEST = STICKER_DIR / "manifest.json"
#: AstrBot 收到图片后会落到这个目录（本地路径形态）
TEMP_DIR = Path("data/temp")

#: 两条回复之间的最小间隔（秒）。实测反馈"非 @ 回复频率太低" → 90 降到 30。
MIN_REPLY_INTERVAL = 30
#: **被 @ 时**的最小间隔（秒）。对方明确叫你就该回，不该被间隔挡掉
#: —— 实测：群主 @ 了机器人却被限流静默丢弃，随后 AstrBot 默认流程
#: 用人设里的 `[表情包:关键词]` 写法回了纯文本，看起来就是"表情包没发出来"。
MIN_WOKEN_INTERVAL = 5
#: 每群每分钟最多回复条数
MAX_REPLY_PER_MIN = 3
#: 被 @ 时每分钟允许多少条（明确呼叫不受"少回复"限制）
MAX_WOKEN_PER_MIN = 6
#: 表情包库上限（超过后按最久未用淘汰）
MAX_STICKERS = 300
#: 单张表情包体积上限（字节）
MAX_STICKER_BYTES = 4 * 1024 * 1024
#: **动图抽几帧**
GIF_MAX_FRAMES = 4
#: 让模型能**主动丢掉没用的图**：写 `[删表情:关键词]`（只移出索引，不删文件）
DELETE_STICKER_MARKER = re.compile(
    r"[\[【（(［]\s*删(?:表情|图)\s*[:：]\s*([^\]】）)］]{1,60})\s*[\]】）)］]", re.I)
#: 让模型能**下载图并发出**：`[下图:关键词]` 或 `[下图:图片直链]`
#: 设计要求："可以下载搜到的图也可以下载群成员头像"。
DOWNLOAD_IMG_MARKER = re.compile(
    r"[\[【（(［]\s*下图\s*[:：]\s*([^\]】）)］]{1,120})\s*[\]】）)］]", re.I)
#: 下载图片的体积上限（字节）
MAX_DOWNLOAD_BYTES = 5 * 1024 * 1024
#: 从搜索结果里找图片时，最多打开几个页面去找 <img>
IMG_SEARCH_PAGES = 3
#: 挑图时最多给模型看几张候选（看得多挑得准，但每张都费 token）
MAX_CANDIDATES = 5
#: 挑图时把图片压到多少像素（越大越慢；判断"合不合适"512 足够）
REVIEW_IMAGE_MAX_SIDE = 512
#: 单次挑图的超时（秒）。超时就跳过挑图直接发，不能让用户干等。
REVIEW_TIMEOUT = 25
#: 生成回复的超时（秒）。超时就本次不回，避免用户无限等待。
CHAT_TIMEOUT = 45
#: 机器人自己的头像缓存（描述文本），避免每次重新拉图与识图
#: 机器人自己头像的描述缓存（文件名由插件 id 派生，避免各处写死旧名字）
AVATAR_CACHE = Path("data/%s_avatar.json" % PLUGIN_ID)
#: 下载来的图片（搜图/头像）放这里
DOWNLOAD_DIR = Path("data/downloads")
#: 轮询群成员变动的间隔（秒）。用于感知"加群/退群"；名单拉取有网络开销，别设太小
MEMBER_WATCH_INTERVAL = 60
#: 每个群记住最近几张图（供"看看刚才那张图"这类请求）
MAX_REMEMBER_IMAGES = 5
#: 单次送给模型的图片上限（含被引用的图），避免 token 爆炸
MAX_IMAGES_PER_CALL = 3

#: 表情包标记：兼容半角/全角方括号、书名号、圆括号与全角冒号；
#: 内容放宽到 60 字符，因为**允许给多个关键词**。
#: （模型写法不统一，只认一种会让标记原样发出去 —— 实测踩到）
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
#: 搜索后**打开**前几个网页读正文
PAGE_FETCH_TOP = 2
#: 最多尝试打开几个网页（百度百科/知乎等会反爬 403，多试几个才有成功的）
PAGE_FETCH_TRY = 4
#: 单个网页正文最多取多少字符（避免 prompt 过长）
PAGE_TEXT_LIMIT = 2500
#: 搜索超时（秒）
SEARCH_TIMEOUT = 20
SILENT_MARKERS = ("SILENT", "silent", "（不回复）", "(不回复)", "不回复")


# 实例配置（管理员/自称/人设/群范围/OneBot 地址）：代码里不写死具体值
from .config import Settings
# 提示词：模板化 + 通用默认人设（文案与逻辑分离）
from . import prompts as P

#: 运行期生效的配置。插件是单实例，`__init__` 里由 AstrBot 注入的 config 填充；
#: 静态方法也要读它，故放模块级（比把每个方法都改成实例方法改动更小、更安全）。
S: Settings = Settings()


def _self_label() -> str:
    """机器人在**群聊历史**里的发言标签（用配置的自称，回退默认名）。

    为什么要有它：历史行是 `标签：内容` 的形式，判断"是否两人对话"时要跳过机器人自己的行，
    而模型读历史时也会看到这个标签。若这里写死一个名字，使用者改了 `bot_name` 之后
    上下文里的身份就会和提示词不一致 —— 所以标签必须与自称同源。
    """
    return (S.bot_name or "").strip() or P.DEFAULT_BOT_NAME


def _persona_from_settings(settings: Settings) -> str:
    """按配置取人设正文：`persona_file` 优先，其次 `persona`，最后用通用默认人设。

    为什么放模块级：它不依赖插件状态，只是个"读配置"的纯函数；
    放在类里会让人误以为有隐藏状态。
    """
    if settings.persona_file:
        try:
            path = Path(settings.persona_file)
            if path.exists():
                return path.read_text(encoding="utf-8").strip()
            logger.warning("%s 人设文件不存在：%s" % (LOG_TAG, settings.persona_file))
        except Exception as exc:
            logger.warning("%s 读取人设文件失败：%r" % (LOG_TAG, exc))
    return P.persona_text(settings.persona, settings.bot_name)


@register(PLUGIN_ID, "群聊助手", "群聊 AI：自主决定是否接话 + 看图 + 表情包 + 联网查询", "1.0.0")
class HiganPlugin(Star):
    """群聊 AI 插件：自主回复、看图、表情包与联网能力。"""

    def __init__(self, context: Context, config=None) -> None:
        """初始化：装载配置、表情包索引与人设。

        @param config - AstrBot 注入的插件配置（有 `_conf_schema.json` 时由 WebUI 生成）。
        """
        super().__init__(context, config)
        global S
        S = Settings.from_mapping(config)
        STICKER_DIR.mkdir(parents=True, exist_ok=True)
        self.manifest: list[dict] = self._load_manifest()
        self._last_reply_at: dict[str, float] = {}
        self._reply_times: dict[str, list[float]] = {}
        self._recent: dict[str, list[str]] = {}
        # 每个群最近出现过的图（供"看看刚才那张图"这类请求，设计要求：）
        self._recent_images: dict[str, list[str]] = {}
        # 各群成员名单快照（用于 diff 出"加群/退群"，设计要求：）
        self._member_snapshot: dict[str, dict[str, str]] = {}
        # 顺序保证：每个群一个"忙碌"标记 + 只保留最新一条待处理消息
        self._busy: dict[str, bool] = {}
        self._pending: dict[str, tuple] = {}
        # 人设文本：插件自己调 LLM 会**覆盖** AstrBot 的 system prompt，
        # 所以必须把人设读进来拼在最前面，否则"人设就不牢靠"。
        self.persona: str = _persona_from_settings(S)
        scanned = self._scan_sticker_dir()
        logger.info(LOG_TAG + " 插件加载，表情包库 %d 张（新登记 %d），人设 %d 字"
                    % (len(self.manifest), scanned, len(self.persona)))

    async def initialize(self) -> None:
        """启动后台任务：① 补表情包描述 ② 监听群成员变动（加群/退群）。

        把**通用占位描述**（"表情包"）也算作"待补" —— 否则从 temp 导入或手动放进目录的
        图会永远停在通用描述上，按关键词挑图时永远匹配不到。
        """
        missing = [s for s in self.manifest
                   if (s.get("desc") or "").strip() in ("", "表情包")]
        if missing:
            logger.info(LOG_TAG + " 待补描述的表情包 %d 张" % len(missing))
            asyncio.create_task(self._describe_missing(missing))
        # 成员变动监听：设计要求："模型能看到加群退群"
        asyncio.create_task(self._watch_members())
        logger.info(LOG_TAG + " 已启动群成员变动监听（每 %ds 一次，群=%s）"
                    % (MEMBER_WATCH_INTERVAL,
                       S.owner_desc() if S.member_watch_groups else "（未配置，不监听）"))

    # ------------------------------------------------------------------ 工具

    def _load_manifest(self) -> list[dict]:
        """读表情包索引（`[{file, desc, ts, uses}]`）。"""
        if MANIFEST.exists():
            try:
                with open(MANIFEST, encoding="utf-8") as fh:
                    return json.load(fh)
            except Exception as exc:
                logger.warning(LOG_TAG + " 表情包索引损坏，重建：%r", exc)
        return []

    def _save_manifest(self) -> None:
        """写表情包索引。"""
        try:
            with open(MANIFEST, "w", encoding="utf-8") as fh:
                json.dump(self.manifest, fh, ensure_ascii=False, indent=1)
        except Exception as exc:
            logger.warning(LOG_TAG + " 表情包索引写入失败：%r", exc)

    def _scan_sticker_dir(self) -> int:
        """把目录里**未被索引登记**的图片补进索引（支持用户手动放进去的表情包）。"""
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
                logger.warning(LOG_TAG + " 补描述失败 %s：%r", item["file"], exc)
                continue
            item["desc"] = desc
            logger.info(LOG_TAG + " 表情包描述 %s → %s" % (item["file"], desc))
        self._save_manifest()

    def _load_avatar_cache(self) -> dict:
        """读机器人头像的描述缓存（`{qq, desc}`）。"""
        if AVATAR_CACHE.exists():
            try:
                return json.loads(AVATAR_CACHE.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning(LOG_TAG + " 头像缓存损坏：%r", exc)
        return {}

    def _avatar_path(self, qq: str) -> Path:
        """机器人头像的本地文件路径。"""
        return AVATAR_CACHE.parent / ("%s_avatar_%s.jpg" % (PLUGIN_ID, qq))

    async def _self_knowledge(self, event: AstrMessageEvent) -> str:
        """机器人对**自己**的认知：它的 QQ 头像长什么样（带缓存）。

        实测"模型不知道自己的头像是什么样" —— 它此前完全不了解自己。
        这里拉一次自己的 QQ 头像、用视觉模型描述一次并缓存，之后每次对话都带上，
        这样被问"你头像是什么 / 你长什么样"时能答得出来。
        """
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
                logger.info(LOG_TAG + " 已记住自己的头像：%s" % desc)
            return desc
        except Exception as exc:
            logger.warning(LOG_TAG + " 获取/描述自己的头像失败：%r", exc)
            return ""

    def _avatar_target(self, event: AstrMessageEvent, text: str) -> tuple[str, str]:
        """判断这句话在问**谁**的头像，返回 `(QQ, 这是谁的头像)`。

        这是"头像老出错"的根治点：
        模型此前**看不到提问者的头像**，被问"我的头像是什么"时只能拿**自己的**头像描述
        硬答（于是出现"主人的头像是银发猫耳女仆"这种把机器人自己当成对方的回答）。
        现在插件先把**正确那个人的头像**找出来发给它，它照着看就行。

        解析顺序：
          1. 「你/机器人 的头像」→ 机器人自己
          2. 「我的头像」→ 说话人自己
          3. 消息里 @ 了谁 → 那个人（用 At 组件的 qq）
          4. 「XX 的头像」→ 按昵称在群成员名单里找
        """
        t = text or ""
        if not any(k in t for k in ("头像", "长什么样", "长啥样", "样子", "自拍",
                                    "看看你", "你可爱", "你是谁")):
            return "", ""
        self_id = str(event.get_self_id() or "")
        sender = str(event.get_sender_id() or "")
        # 1) 机器人自己
        # 唤醒词里的机器人名字从配置来：名称是可配置的，不能写死在正则里
        names = [n for n in (S.bot_name, P.DEFAULT_BOT_NAME) if n]
        self_pat = r"你(的)?(头像|样子)|看看你|你长"
        if names:
            self_pat += "|" + "|".join(
                r"%s(的)?(头像|样子)" % re.escape(n) for n in names)
        if re.search(self_pat, t):
            return self_id, "你自己（机器人）的头像"
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

    @staticmethod
    def _at_targets(event: AstrMessageEvent) -> list[str]:
        """取本条消息里 @ 了哪些 QQ（用于判断"他是不是在跟别人说话"）。

        这是"别插嘴"判断的关键线索：有人 @ 了别的群友时，
        那条消息多半**不是对机器人说的**，机器人应当沉默。
        """
        out: list[str] = []
        for comp in event.get_messages():
            if isinstance(comp, At):
                qq = str(getattr(comp, "qq", "") or "")
                if qq.isdigit():
                    out.append(qq)
        return out

    def _recent_others_talking(self, group: str, who: str, window: int = 4) -> bool:
        """最近几条里，是否**正好两个人在一问一答**（真正的两人对话）。

        **必须严格**：早先只要最近几条出现 ≥2 个不同发言人就判为
        "别人在交谈"，可群里本来就人多，于是几乎每次都给模型"别插嘴"的线索，
        结果它什么都不说了（用户反馈"怎么不回复了"）。
        现在只认**恰好 2 个人、且交替发言**（A→B→A→B）这种真正的两人对话。
        """
        history = self._recent.get(group) or []
        seq: list[str] = []
        me_label = _self_label()
        for line in history[-window:]:
            name = line.split("：", 1)[0]
            if name and name != me_label:        # 跳过机器人自己的发言
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
        """把发言人描述成"昵称（身份，QQ 号）"，让模型能认出主人与群友。

        **只给昵称是不够的**：模型没法把昵称对应到主人名单上（用户反馈"不认人"）。
        这里直接算好身份再交给模型，比让它自己比对 QQ 号更可靠。

        **QQ 缺失时不要瞎标身份**：掉线期间积压补推的消息
        `get_sender_id()` 会是 0，那时若仍标"群友，QQ 0"，模型可能把主人当群友。
        此时只给昵称、并注明身份未知。
        """
        qq = str(event.get_sender_id() or "")
        name = event.get_sender_name() or ""
        if not qq or qq == "0":
            return "%s（QQ 未知，身份无法确认）" % (name or "某人")
        if not name:
            name = qq
        role = "主人" if S.is_owner(qq) else "群友"
        return "%s（%s，QQ %s）" % (name, role, qq)

    def _rate_ok(self, group: str, woken: bool = False) -> bool:
        """防刷屏：间隔 + 每分钟条数。

        **被 @ 时放宽**：对方明确叫你就该回，不该被"少回复"的 90 秒挡掉
        。
        """
        now = time.time()
        interval = MIN_WOKEN_INTERVAL if woken else MIN_REPLY_INTERVAL
        limit = MAX_WOKEN_PER_MIN if woken else MAX_REPLY_PER_MIN
        if now - self._last_reply_at.get(group, 0) < interval:
            logger.info(LOG_TAG + " 群 %s：限流跳过（距上次 %ds < %ds，woken=%s）"
                        % (group, int(now - self._last_reply_at.get(group, 0)),
                           interval, woken))
            return False
        times = [t for t in self._reply_times.get(group, []) if now - t < 60]
        self._reply_times[group] = times
        if len(times) >= limit:
            logger.info(LOG_TAG + " 群 %s：本分钟已达上限 %d 条，跳过（woken=%s）"
                        % (group, limit, woken))
            return False
        return True

    def _mark_replied(self, group: str) -> None:
        """记录一次回复时间。"""
        now = time.time()
        self._last_reply_at[group] = now
        self._reply_times.setdefault(group, []).append(now)

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
        """取**被引用消息的文字**。

        `Reply` 组件自带 `message_str`（被引用消息解析后的纯文本）与 `sender_nickname`，
        直接用它们；没有的话再从 `chain` 里的 Plain 段拼一份兜底。
        """
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
        """取本条消息里的图 + **被引用消息里的图**。

        "可以主动看某个图"：群里常见用法是**引用一张图**再问
        "这是什么"，此时本条消息里没有图，只看本条就什么都看不见（之前的缺陷）。
        """
        refs: list[str] = []
        for comp in event.get_messages():
            if isinstance(comp, Image):
                ref = self._ref_of(comp)
                if ref:
                    refs.append(ref)
            elif isinstance(comp, Reply):
                # 被引用消息的完整消息段列表就在 Reply.chain 里（含图片）
                for sub in (getattr(comp, "chain", None) or []):
                    if isinstance(sub, Image):
                        ref = self._ref_of(sub)
                        if ref:
                            refs.append(ref)
        return refs

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
        """把图片转成 data URL **列表**：静图 1 张，**动图抽多帧**。

        "动图需要模型多看几帧" —— 只取第一帧根本看不懂动图在演什么
        （例如"先是得意、然后翻车"这种）。这里对 GIF/WebP 动图等间隔抽 `max_frames` 帧，
        横向拼成一张长条图再发：既让模型看到过程，又只占一张图的 token。
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
            logger.warning(LOG_TAG + " 图片处理失败，改用原图：%r", exc)
            mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
            return ["data:%s;base64,%s" % (mime, base64.b64encode(raw).decode())]

    @staticmethod
    def _data_url(path: Path, max_side: int = REVIEW_IMAGE_MAX_SIDE) -> str:
        """单张 data URL（静图用；动图请用 `_data_urls` 取多帧）。

        会**先压缩到 `max_side` 像素**：判断"这张表情合不合适"不需要原图，
        而群里表情包常有 1~2MB，base64 后 2.7MB，上传+视觉推理要 100 秒以上
        。压到 512px/JPEG 后通常几十 KB。
        """
        return HiganPlugin._data_urls(path, max_side=max_side, max_frames=1)[0]

    async def _pick_by_looking(self, keywords: list[str], context: str) -> dict | None:
        """把候选表情包**直接给模型看**，让它自己挑一张。

        为什么这样改：之前模型只看到**文字标签**，挑图等于盲选，再靠"复核"补救；
        用户反馈"模型不知道自己的图长什么样"。现在把候选图连图带标签一起给它，
        它**看得见**再挑 —— 一次调用同时完成"挑图 + 判断合不合适"，
        比"盲选 + 复核"又快又准（少一次往返）。

        @returns 选中的表情包条目；返回 None 表示"都不合适/没搜到"。
        """
        if not self.manifest:
            return None
        pool = self._search_stickers(keywords)[:MAX_CANDIDATES]
        if not pool:
            logger.info(LOG_TAG + " 关键词 %s 没搜到任何表情包" % keywords)
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
                    system_prompt=P.PICK_STICKER_SYSTEM,
                    image_urls=refs),
                timeout=REVIEW_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning(LOG_TAG + " 挑图超时（>%ds），用第一张候选" % REVIEW_TIMEOUT)
            return pool[0]
        except Exception as exc:
            logger.warning(LOG_TAG + " 挑图失败（用第一张候选）：%r", exc)
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
            logger.info(LOG_TAG + " 模型看了 %d 张候选，认为都不合适（原答：%s）"
                        % (len(refs), text[:40].replace("\n", " ")))
            return None
        if 1 <= pick <= len(pool):
            chosen = pool[pick - 1]
            chosen["uses"] = int(chosen.get("uses", 0)) + 1
            chosen["last_used"] = time.time()
            self._save_manifest()
            logger.info(LOG_TAG + " 模型看图选中第 %d 张：%s（%s）"
                        % (pick, chosen["file"], (chosen.get("desc") or "")[:20]))
            return chosen
        return pool[0]

    def _apply_drop(self, line: str, pool: list[dict]) -> None:
        """处理模型给的"丢掉这张"指令：`删2` 或 `删表情:关键词`。

        "增加模型可以主动丢掉一些没用的图"。
        **只移出索引、不删文件**（可随时恢复；也避免误删）。
        """
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
                    logger.info(LOG_TAG + " 模型主动丢掉表情包：%s（%s）"
                                % (item["file"], (item.get("desc") or "")[:20]))
            if removed:
                self._save_manifest()
        except Exception as exc:
            logger.warning(LOG_TAG + " 解析丢图指令失败（%s）：%r", line[:30], exc)

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
            logger.info(LOG_TAG + " 模型改了关键词：%s 「%s」→「%s」"
                        % (item["file"], old, item["desc"]))
        except Exception as exc:
            logger.warning(LOG_TAG + " 解析标签修正失败（%s）：%r", line[:30], exc)

    async def _pick_sticker_reviewed(self, keywords: list[str],
                                     context: str) -> dict | None:
        """兼容旧名：实际走 `_pick_by_looking`（看图挑一张）。"""
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
        """所有消息入口：串行化 + latest-wins，保证回复顺序且不积压。"""
        try:
            group = str(event.get_group_id() or "")
            if not group:                      # 只处理群聊
                return
            if not S.group_allowed(group):
                return

            text = (event.get_message_str() or "").strip()
            images = [c for c in event.get_messages() if isinstance(c, Image)]

            # ① 群友发图 → 收集进表情包库（后台跑，不影响回复时序）+ 记住最近几张图
            if images:
                self._remember_images(group, self._image_refs(event))
                asyncio.create_task(self._collect_images(event, group, images))

            # ② 命令（以唤醒前缀开头）交给 AstrBot 自己处理，插件不插手、也不拦事件
            if text.startswith("/"):
                return

            logger.info(LOG_TAG + " 收到群 %s 消息：%s（%s）｜woken=%s"
                        % (group, text[:40] or "[图片]", self._who(event),
                           self._is_woken(event)))

            # **每条群消息都进历史**（含发言人身份），这样模型看到的上下文才完整。
            # 之前只有"决定要不要回"的消息才进历史，被限流/SILENT 的全丢了，
            # 导致它接不上话题。
            quoted = self._quoted_text(event)
            line = "%s：%s%s" % (self._who(event), text or "[图片]",
                                 "（引用 %s）" % quoted if quoted else "")
            history = self._recent.setdefault(group, [])
            history.append(line)
            del history[:-40]                      # 保留最近 40 条

            # ③ **单一回复通路**：@ 与非 @ 都由本插件处理，并**停止事件传播**，
            #    从而阻止 AstrBot 默认 LLM 再回一条。
            #
            #    为什么必须 stop_event()：AstrBot 的判定是
            #      `if (event.get_result() and not event.is_stopped()) or not event.get_result():`
            #      → 只有"有结果 **且** 已停止"才会跳过默认 LLM。
            #    而 `should_call_llm(False)` 的消费点在插件之前，**拦不住**（实测：@ 消息会
            #    出现"一条带引用、一条不带引用"两条回复，。
            #    代价：管线停止后 RespondStage 不会执行，所以回复必须用 `event.send()` 直接发
            #    （见 `_send`），不能再依赖 set_result。
            event.stop_event()
            self._pending[group] = (event, text, bool(images), self._is_woken(event))
            if self._busy.get(group):
                return
            self._busy[group] = True
            try:
                while self._pending.get(group):
                    ev, txt, has_img, woken = self._pending.pop(group)
                    await self._handle_one(ev, group, txt, has_img, woken)
            finally:
                self._busy[group] = False
        except Exception as exc:
            logger.error(LOG_TAG + " 处理消息出错：%r", exc)

    @staticmethod
    def _is_woken(event: AstrMessageEvent) -> bool:
        """该消息是否已经唤醒机器人（被 @ 或以唤醒前缀开头）。

        这类消息由 AstrBot 默认流程回复（带人设），插件不再插手，避免一次消息两条回复。
        """
        for attr in ("is_at_or_wake_command", "is_wake_command", "is_at_command"):
            value = getattr(event, attr, None)
            if isinstance(value, bool) and value:
                return True
        return False

    async def _handle_one(self, event: AstrMessageEvent, group: str, text: str,
                          has_image: bool, woken: bool) -> None:
        """处理单条消息：限流 → （被 @ 则直接回，否则让模型决定）→ 发送。

        **被 @ 时空消息也要回**：群里很常见的用法就是"只 @ 一下"当戳猫，
        之前 `if not text and not has_image: return` 会静默跳过，表现为"@ 了没反应"
        。现在 woken=True 时即使没文字也回一句。
        """
        if not self._rate_ok(group, woken):
            return
        if not text and not has_image and not woken:
            return
        reply = (await self._reply_direct(event, group, text, has_image) if woken
                 else await self._decide(event, group, text, has_image))
        if not reply:
            return
        # 模型要求联网搜索 → 先搜，再把结果喂回让它作答（工具调用）
        if SEARCH_MARKER.search(reply):
            provider = self.context.get_using_provider(event.unified_msg_origin)
            if provider is not None:
                search_refs, _ = await self._refs_for(event, group, text, has_image)
                reply = await self._handle_search(provider, reply, search_refs, event)
                if not reply:
                    return
        self._mark_replied(group)
        await self._send(event, group, reply)

    @staticmethod
    def _to_plain(reply: str) -> str:
        """兜底：把任何**没被识别**的表情包标记整段去掉，绝不让它当文字发出去。

        实测教训：模型可能写成 `【表情包：无语】`、`(表情包: 无语)` 等
        变体；若正则没覆盖，标记就会原样出现在群里。这里再做一次宽松兜底。
        """
        return re.sub(r"[\[【（(［]?\s*表情包\s*[:：][^\]】）)］]{0,20}[\]】）)］]?", "", reply).strip()

    @staticmethod
    def _split_keywords(raw: str) -> list[str]:
        """把 `[表情包:无语, 生无可恋]` 里的内容拆成多个关键词。

        "可以给出多个关键词允许模型按关键词模糊搜索"。
        """
        return [k.strip() for k in KEYWORD_SPLIT.split(raw or "") if k.strip()]

    def _search_stickers(self, keywords: list[str]) -> list[dict]:
        """按多个关键词**模糊搜索**表情包，命中越多排越前。

        匹配规则（从严到松）：
          1. 双向包含（关键词 ⊆ 描述，或描述 ⊆ 关键词）→ +2
          2. 关键词的任意**两字片段**出现在描述里 → +1
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
        """把**可用的表情包描述**列成一行，交给模型挑关键词。

        "让模型看到自己有哪些图可以发"。
        只给描述、不给图片：库里几十张图全塞进上下文会 token 爆炸，
        而描述足够模型挑一个**真实存在**的关键词（之前它是盲猜，猜不中就发不出去）。
        """
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
        """在提示词里补上"机器人对自己的认知"（当前是它的 QQ 头像是什么样）。

        实测"模型不知道自己的头像是什么样"。头像描述取自
        `_self_knowledge()`（有缓存，不会每次都识图）。
        """
        info = await self._self_knowledge(event)
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        weekday = "一二三四五六日"[int(time.strftime("%w"))]
        clock = ("\n\n【当前时间】%s（星期%s，北京时间）。"
                 "有人问几点/今天几号，**直接按这个回答，不用搜索**。"
                 % (now, weekday))
        if not info:
            return prompt + clock
        return ("%s\n\n【你自己】你是 QQ 号 %s 的猫娘 bot，你的 QQ 头像是：%s\n"
                "**注意**：这段信息只在对方明确问「你（机器人）的头像/你长什么样」时才用；"
                "别人说「我的头像」指的是**他自己**，不要拿这段去答。%s"
                % (prompt, event.get_self_id(), info, clock))

    async def _refs_for(self, event: AstrMessageEvent, group: str, text: str,
                        has_image: bool) -> tuple[list[str], str]:
        """决定这次调用要带上哪些图，以及"这张图是谁的"。

        优先级：本条消息的图 → 被引用的图 → **被问到的那个人的头像** →
        （若在问图）群里最近那张图。

        @returns `(图片引用列表, 图片归属说明)`；说明会写进提示词，避免模型张冠李戴
            。
        """
        refs = self._image_refs(event)
        if refs:
            return refs, "对方本条消息里发的图/引用的图"
        if has_image:
            return [], ""
        # 被问头像 → 拉**正确那个人**的头像（这是"头像老出错"的根治点）
        avatar, who_desc = await self._avatar_ref(event, text)
        if avatar:
            logger.info(LOG_TAG + " 被问头像，带上「%s」给模型看" % who_desc)
            return [avatar], who_desc
        if self._looks_like_asking_image(text):
            cache = self._recent_images.get(group) or []
            if cache:
                logger.info(LOG_TAG + " 本条无图，带上群里最近那张图供查看")
                return [cache[-1]], "群里最近发过的那张图"
        return [], ""

    async def _reply_direct(self, event: AstrMessageEvent, group: str, text: str,
                            has_image: bool) -> str:
        """被 @ 时直接回一句（不再判断该不该回，但仍是极短回复 + 人设）。"""
        provider = self.context.get_using_provider(event.unified_msg_origin)
        if provider is None:
            return ""
        who = self._who(event)
        refs, ref_desc = await self._refs_for(event, group, text, has_image)
        quoted = self._quoted_text(event)
        prompt = ("【%s 对你说】%s%s%s\n\n请回一句（20 字以内）。"
                  % (who, text or "（发了一张图）",
                     "（他发了图/引用了图，看图后回应）" if refs else "",
                     "\n（他引用了这条消息：%s）" % quoted if quoted else ""))
        if refs and ref_desc:
            # **明确告诉模型这是谁的头像/图**，否则它会把机器人的头像当成对方的
            prompt += "\n【随消息附上的图】%s（共 %d 张）" % (ref_desc, len(refs))
        prompt = await self._with_self(self._with_catalog(prompt), event)
        out = await self._call_llm(
            provider, prompt,
            P.direct_system(S.bot_name, S.owner_ids, S.web_enabled), refs)
        if out:
            logger.info(LOG_TAG + " 群 %s：被 @ 直接回复「%s」（带图 %d 张）"
                        % (group, out[:40], len(refs)))
        return out

    async def _call_llm(self, provider, prompt: str, extra_system: str,
                        refs: list[str]) -> str:
        """统一调用 LLM：**人设在前**（人设为核心），附加任务规则在后。

        @param refs - 要一起送给模型的图片引用（http URL 或本地路径）。
            本地路径会转成 **data URL**：实测把 `file://` 直接交给 provider 拿不到图，
            而 base64 内联一定能看到。
        """
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
                logger.warning(LOG_TAG + " 处理图片引用失败（%s）：%r", str(ref)[:50], exc)
        system_prompt = ("%s\n\n---\n\n%s" % (self.persona, extra_system)
                         if self.persona else extra_system)
        try:
            resp = await asyncio.wait_for(
                provider.text_chat(prompt=prompt, system_prompt=system_prompt,
                                   image_urls=image_urls or None),
                timeout=CHAT_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning(LOG_TAG + " 调用超时（>%ds），本次不回" % CHAT_TIMEOUT)
            return ""
        except Exception as exc:
            logger.warning(LOG_TAG + " 调用失败：%r", exc)
            return ""
        return (getattr(resp, "completion_text", "") or "").strip()

    async def _decide(self, event: AstrMessageEvent, group: str, text: str,
                      has_image: bool) -> str:
        """让模型自主决定要不要接话；返回空串表示不接。"""
        provider = self.context.get_using_provider(event.unified_msg_origin)
        if provider is None:
            return ""
        history = self._recent.setdefault(group, [])
        context_text = "\n".join(history[-20:]) or "（暂无上下文）"
        who = self._who(event)
        refs, ref_desc = await self._refs_for(event, group, text, has_image)
        quoted = self._quoted_text(event)
        # 给模型判断"是否该插嘴"的关键线索：这条消息是不是在跟**别人**说话
        ats = self._at_targets(event)
        talking_to_other = [qq for qq in ats if qq != str(event.get_self_id() or "")]
        scene = []
        if talking_to_other:
            scene.append("他 @ 了别人（QQ %s）——**很可能是在跟那个人说话，不是你**"
                         % "、".join(talking_to_other[:3]))
        if quoted:
            scene.append("他引用了另一条消息，像是在延续某个对话")
        if self._recent_others_talking(group, who):
            scene.append("最近几条是**两个人在一问一答**（像私聊），你插进去会打断")
        prompt = ("【最近群聊】\n%s\n\n【新消息】%s：%s%s%s\n\n%s"
                  "你要插嘴吗？接就写回复（20 字以内），不接就只输出 SILENT。"
                  % (context_text, who, text or "（发了一张图）",
                     "（他发了图/引用了图，你可以看图后回应）" if refs else "",
                     "\n（他引用了这条消息：%s）" % quoted if quoted else "",
                     ("【判断线索】%s\n" % "；".join(scene)) if scene else ""))
        if refs and ref_desc:
            prompt += "\n【随消息附上的图】%s（共 %d 张）" % (ref_desc, len(refs))
        prompt = await self._with_self(self._with_catalog(prompt), event)
        out = await self._call_llm(
            provider, prompt,
            P.decide_system(S.bot_name, S.owner_ids, S.web_enabled), refs)
        if not out or out.strip("`*_ ") in SILENT_MARKERS or out.upper().startswith("SILENT"):
            logger.info(LOG_TAG + " 群 %s：模型选择不接话（发言人=%s）" % (group, who))
            return ""
        # 只追加自己的回复；用户消息在 `on_message` 里已统一记入历史（避免重复）
        # 标签用**配置的自称**（`_self_label()`），保证模型在上下文里看到的身份与提示词一致
        history.append("%s：%s" % (_self_label(), out))
        del history[:-40]
        return out

    async def _send(self, event: AstrMessageEvent, group: str, reply: str) -> None:
        """发送回复：**引用被回复的消息** + 文字 + 可选表情包（**发前先看一眼**）。

        引用（QQ 的"回复某条消息"）很关键：快速连发时，光看内容分不清在回哪一句，
        引用后一目了然，顺序上的歧义也就消失了。
        写法与 AstrBot 官方一致：`chain.insert(0, Reply(id=消息ID))`。

        **用 `event.send()` 直接发**，而不是 `set_result()`：因为在 `on_message` 里调用了
        `stop_event()`（用来拦掉默认 LLM），管线会中止、RespondStage 不会执行，
        `set_result` 的结果就发不出去了。`event.send` 是官方命令插件同样在用的直接发送。
        """
        m = STICKER_MARKER.search(reply)
        text = self._to_plain(STICKER_MARKER.sub("", reply))
        chain = MessageChain()
        msg_id = getattr(getattr(event, "message_obj", None), "message_id", None)
        if msg_id:
            try:
                chain.chain.append(Reply(id=msg_id))
            except Exception as exc:
                logger.warning(LOG_TAG + " 构造引用失败：%r", exc)
        # `[头像:QQ]` → 把该成员的头像发出来
        av = AVATAR_MARKER.search(text)
        if av:
            text = self._to_plain(AVATAR_MARKER.sub("", text))
            try:
                avatar_file = await self._member_avatar(av.group(1))
                if avatar_file:
                    chain.chain.append(Image.fromFileSystem(str(avatar_file)))
                    logger.info(LOG_TAG + " 发出成员 %s 的头像" % av.group(1))
            except Exception as exc:
                logger.warning(LOG_TAG + " 取成员头像失败：%r", exc)
        # `[删表情:关键词]` → 模型主动丢掉没用的图（只移出索引，不删文件）
        drop = DELETE_STICKER_MARKER.search(text)
        if drop:
            text = self._to_plain(DELETE_STICKER_MARKER.sub("", text))
            keys = self._split_keywords(drop.group(1))
            victims = [s for s in self.manifest
                       if any(k and k in (s.get("desc") or "") for k in keys)]
            for item in victims:
                self.manifest.remove(item)
                logger.info(LOG_TAG + " 按模型要求丢掉表情包：%s（%s）"
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
                logger.info(LOG_TAG + " 已下载并发出图片：%s（%s）" % (path.name, arg[:30]))
            else:
                logger.info(LOG_TAG + " 下载图片失败/无结果：%s" % arg[:30])
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
                    logger.info(LOG_TAG + " 发表情包 %s（关键词=%s，描述=%s）"
                                % (sticker["file"], keywords, sticker.get("desc")))
                else:
                    logger.warning(LOG_TAG + " 表情包文件缺失：%s" % path)
        if not chain.chain:
            return
        try:
            await event.send(chain)
        except Exception as exc:
            logger.error(LOG_TAG + " 发送失败：%r", exc)
    # ----------------------------------------------------- 上网搜索（工具）

    @staticmethod
    def _web_search(query: str, limit: int = SEARCH_TOP_N) -> list[dict]:
        """用 Bing 搜一下，返回结构化结果 `[{title, url, snippet}]`。

        为什么自己实现而不开 AstrBot 的 `web_search`：那套要填第三方 key（Tavily 等），
        而 Bing 网页结果足够用，且本机出网正常。
        """
        import html
        import urllib.parse
        import urllib.request
        url = "https://cn.bing.com/search?" + urllib.parse.urlencode(
            {"q": query, "count": limit + 3, "mkt": "zh-CN", "setlang": "zh-hans"})
        req = urllib.request.Request(url, headers={
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0"),
            "Accept-Language": "zh-CN,zh;q=0.9"})
        with urllib.request.urlopen(req, timeout=SEARCH_TIMEOUT) as resp:
            page = resp.read().decode("utf-8", "replace")
        out: list[dict] = []
        for block in re.findall(r'<li class="b_algo".*?</li>', page, re.S):
            m = re.search(r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
            if not m:
                continue
            pm = re.search(r"<p[^>]*>(.*?)</p>", block, re.S)
            out.append({
                "title": html.unescape(re.sub(r"<[^>]+>", "", m.group(2))).strip()[:100],
                "url": html.unescape(m.group(1)),
                "snippet": (html.unescape(re.sub(r"<[^>]+>", "", pm.group(1))).strip()[:200]
                            if pm else "")})
            if len(out) >= limit:
                break
        return out

    @staticmethod
    def _fetch_page(url: str, limit: int = PAGE_TEXT_LIMIT) -> str:
        """**打开网页**并抽取正文文本（去脚本/样式/标签），供模型阅读。

        实测"搜到的都是网页标题，我哪知道几点" —— 只给摘要不够，
        必须真的把页面打开读正文。百度百科/知乎等会反爬（403），故调用方要多试几个站。
        """
        import html
        import urllib.request
        if not url.startswith("http"):
            return ""
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
        with urllib.request.urlopen(req, timeout=SEARCH_TIMEOUT) as resp:
            raw = resp.read(800_000).decode("utf-8", "replace")
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
        """Bing **图片搜索**，返回图片直链列表。"""
        import html
        import urllib.parse
        import urllib.request
        url = "https://cn.bing.com/images/search?" + urllib.parse.urlencode(
            {"q": query, "count": limit + 6, "mkt": "zh-CN", "setlang": "zh-hans"})
        req = urllib.request.Request(url, headers={
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0"),
            "Accept-Language": "zh-CN,zh;q=0.9"})
        with urllib.request.urlopen(req, timeout=SEARCH_TIMEOUT) as resp:
            page = resp.read().decode("utf-8", "replace")
        # 结果里每张图都带 murl（原图直链）
        urls: list[str] = []
        for m in re.finditer(r'murl&quot;:&quot;(.*?)&quot;', page):
            link = html.unescape(m.group(1)).replace("\\/", "/")
            if link.startswith("http") and link not in urls:
                urls.append(link)
            if len(urls) >= limit:
                break
        return urls

    async def _download_image(self, ref: str) -> Path | None:
        """下载一张图到本地（支持 http 直链或关键词 → 先搜图再下第一张）。

        "可以下载搜到的图也可以下载群成员头像"。
        下载后放到 `data/downloads/`，便于直接发出去或复用。
        """
        target = (ref or "").strip()
        if not target:
            return None
        if not target.startswith("http"):
            try:
                found = await asyncio.wait_for(
                    asyncio.to_thread(self._web_image_search, target, 3),
                    timeout=SEARCH_TIMEOUT + 5)
            except Exception as exc:
                logger.warning(LOG_TAG + " 搜图失败（%s）：%r", target[:30], exc)
                return None
            if not found:
                logger.info(LOG_TAG + " 搜图没找到：%s" % target[:30])
                return None
            target = found[0]
        try:
            raw = await asyncio.wait_for(asyncio.to_thread(self._read_image, target),
                                         timeout=SEARCH_TIMEOUT + 5)
        except Exception as exc:
            logger.warning(LOG_TAG + " 下载图片失败（%s）：%r", target[:60], exc)
            return None
        if not raw or len(raw) < 512 or len(raw) > MAX_DOWNLOAD_BYTES:
            logger.info(LOG_TAG + " 图片不合适（%d 字节），跳过" % len(raw or b""))
            return None
        suffix = (".gif" if raw[:3] == b"GIF" else ".png" if raw[:4] == b"\x89PNG"
                  else ".webp" if raw[:4] == b"RIFF" else ".jpg")
        digest = hashlib.sha1(raw).hexdigest()[:16]
        out = DOWNLOAD_DIR / ("%s%s" % (digest, suffix))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(raw)
        logger.info(LOG_TAG + " 已下载图片 %s（%d 字节，来源 %s）"
                    % (out.name, len(raw), target[:60]))
        return out

    async def _handle_search(self, provider, reply: str, refs: list[str],
                             event: AstrMessageEvent) -> str:
        """处理回复里的 `[搜索:关键词]`：联网搜 → **打开网页读正文** → 喂回模型作答。

        这是"工具调用"的轻量实现（用标记而不是 AstrBot 的 ToolSet）。
        关键点：**不只给搜索摘要，还要真的打开前几个页面读正文** ——
        否则遇到"现在几点/今天几号"这类问题，模型只能看到网页标题，答不出来
        。
        """
        m = SEARCH_MARKER.search(reply)
        if not m:
            return reply
        query = m.group(1).strip()
        logger.info(LOG_TAG + " 模型要求搜索：%s" % query)
        results: list[dict] = []
        try:
            results = await asyncio.wait_for(
                asyncio.to_thread(self._web_search, query), timeout=SEARCH_TIMEOUT + 5)
        except Exception as exc:
            logger.warning(LOG_TAG + " 搜索失败：%r", exc)
        if not results:
            logger.info(LOG_TAG + " 搜索无结果")
            cleaned = SEARCH_MARKER.sub("", reply).strip()
            prompt = ("【你刚才想说的话】%s\n\n【搜索关键词】%s\n\n"
                      "（没有搜到任何结果）请据此回一句（可以说搜不到喵）。"
                      % (cleaned or "（无）", query))
            return await self._call_llm(provider, prompt, P.SEARCH_ANSWER_SYSTEM,
                                        refs) or cleaned

        # **打开网页读正文**：多试几个站（百度百科/知乎等会 403），
        # 并发拉取，取前 PAGE_FETCH_TOP 个成功的。
        pages: list[str] = []
        candidates = results[:PAGE_FETCH_TRY]
        try:
            bodies = await asyncio.gather(*[
                asyncio.wait_for(asyncio.to_thread(self._fetch_page, r["url"]),
                                 timeout=SEARCH_TIMEOUT + 5)
                for r in candidates], return_exceptions=True)
            for r, body in zip(candidates, bodies):
                if isinstance(body, str) and len(body) > 80:
                    pages.append("【%s】\n%s" % (r["title"], body))
                    logger.info(LOG_TAG + " 已打开网页：%s（%d 字）"
                                % (r["url"][:60], len(body)))
                elif isinstance(body, Exception):
                    logger.info(LOG_TAG + " 网页打不开（跳过）：%s → %s"
                                % (r["url"][:50], type(body).__name__))
                if len(pages) >= PAGE_FETCH_TOP:
                    break
        except Exception as exc:
            logger.warning(LOG_TAG + " 打开网页失败：%r", exc)

        cleaned = SEARCH_MARKER.sub("", reply).strip()
        prompt = ("【你刚才想说的话】%s\n\n【搜索关键词】%s\n\n【搜索结果】\n%s\n"
                  % (cleaned or "（无）", query, self._format_results(results)))
        if pages:
            prompt += "\n【已打开的网页正文】\n%s\n" % "\n\n".join(pages)
        else:
            prompt += "\n（网页正文没能打开，只能依据上面的搜索摘要回答）\n"
        prompt += "\n请据此给一句回复。"
        out = await self._call_llm(provider, prompt, P.SEARCH_ANSWER_SYSTEM, refs)
        logger.info(LOG_TAG + " 搜索后回复：%s" % (out[:60] or "（空）"))
        return out or cleaned

    # ------------------------------------------------- 管理员命令（改关键词）

    @filter.command("表情列表")
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

    @filter.command("表情改")
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
        logger.info(LOG_TAG + " 主人改了关键词：%s 「%s」→「%s」"
                    % (item["file"], old, item["desc"]))
        event.set_result(MessageEventResult().message(
            "改好了喵～\n%s\n旧：%s\n新：%s" % (item["file"], old[:30], item["desc"])))

    @filter.command("表情删")
    async def cmd_del_sticker(self, event: AstrMessageEvent) -> None:
        """把某张表情包移出索引（只有主人可用）。用法：`/表情删 <序号>`。

        只移出索引、**不删文件**（避免误删，且随时可以再加回来）。
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
        logger.info(LOG_TAG + " 主人移出表情包：%s（%s）" % (item["file"], item.get("desc")))
        event.set_result(MessageEventResult().message(
            "已移出索引（文件保留）：%s" % item["file"]))

    @staticmethod
    def _is_owner(event: AstrMessageEvent) -> bool:
        """命令是否来自主人（群主 / 管理员）。非主人一律静默忽略。"""
        return S.is_owner(str(event.get_sender_id() or ""))

    # --------------------------------------------------------- 表情包收集

    async def _collect_images(self, event: AstrMessageEvent, group: str,
                              images: list) -> None:
        """把群友发的图存进表情包库，并用视觉模型打描述做索引。

        **关键**：AstrBot 会把收到的图下载到 `data/temp/`，此时组件里是**本地路径**
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
                logger.warning(LOG_TAG + " 取图失败（%s）：%r", src[:60], exc)
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
            logger.info(LOG_TAG + " 收集表情包 %s（%s）" % (path.name, desc))

    @staticmethod
    def _read_image(src: str) -> bytes:
        """读一张图：支持 http(s) URL 与本地路径（含 `file://` 形式）。"""
        import urllib.parse
        import urllib.request
        if src.startswith("http://") or src.startswith("https://"):
            req = urllib.request.Request(src, headers={"User-Agent": "astrbot-plugin/%s" % PLUGIN_ID})
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
        """取某个群成员的头像并缓存到本地。

        用 QQ 官方头像接口，无需登录态；同一 QQ 只拉一次（缓存复用）。
        """
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
            logger.warning(LOG_TAG + " 拉取成员 %s 头像失败：%r", qq, exc)
            return None

    # --------------------------------------------- 群成员变动（加群/退群）

    async def _watch_members(self) -> None:
        """后台任务：定期比对群成员名单，把**加群/退群**写进群上下文。

        "模型能看到加群退群"。AstrBot 不会把 OneBot 的
        成员变动通知转给插件，所以这里自己轮询 `get_group_member_list` 做 diff
        （只读、幂等；间隔 `MEMBER_WATCH_INTERVAL` 秒）。
        """
        while True:
            try:
                await asyncio.sleep(MEMBER_WATCH_INTERVAL)
                groups = await self._onebot("get_group_list", {})
                for g in (groups.get("data") or []):
                    gid = str(g.get("group_id") or "")
                    if not gid or not S.group_watched(gid):
                        continue
                    await self._diff_members(gid, g.get("group_name") or gid)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(LOG_TAG + " 成员变动监听出错：%r", exc)

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
            logger.info(LOG_TAG + " 群 %s 成员基线：%d 人" % (group, len(current)))
            return
        joined = [qq for qq in current if qq not in prev]
        left = [qq for qq in prev if qq not in current]
        history = self._recent.setdefault(group, [])
        for qq in joined:
            line = "【群动态】%s（QQ %s）加入了群聊" % (current[qq] or "某人", qq)
            history.append(line)
            logger.info(LOG_TAG + " %s" % line)
        for qq in left:
            line = "【群动态】%s（QQ %s）退出了群聊" % (prev[qq] or "某人", qq)
            history.append(line)
            logger.info(LOG_TAG + " %s" % line)
        if joined or left:
            del history[:-40]

    @staticmethod
    async def _onebot(action: str, payload: dict) -> dict:
        """调 NapCat 的 OneBot HTTP 接口（只读用途）。"""
        import urllib.request
        url = "%s/%s" % (S.onebot_base, action)
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST")
        req.add_header("Content-Type", "application/json")

        def _call() -> dict:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))

        try:
            return await asyncio.to_thread(_call)
        except Exception as exc:
            logger.warning(LOG_TAG + " OneBot %s 调用失败：%r", action, exc)
            return {}

    async def _describe(self, image_ref: str) -> str:
        """用视觉模型给表情包打一句中文描述（用于关键词匹配）。

        **本地文件也要传给模型**：实测只传 http 会让"手动放进目录 / 从 temp 导入"的
        表情包只能拿到通用描述，导致按关键词挑图时匹配不到。
        """
        text = await self._describe_image(
            image_ref, "用不超过 10 个字描述这张表情包表达的情绪或含义（只输出描述）。")
        return text or "表情包"

    async def _describe_image(self, image_ref: str, prompt: str) -> str:
        """通用视觉描述：把一张图（http URL 或本地路径）交给视觉模型。

        本地路径会转成 **data URL** 并压缩（实测 `file://` 交给 provider 拿不到图，
        而原图 base64 会让请求大到 2.7MB、单次调用 100 秒以上）。
        """
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
            logger.warning(LOG_TAG + " 准备图片失败（%s）：%r", image_ref[:50], exc)
            return ""
        try:
            resp = await asyncio.wait_for(
                provider.text_chat(prompt=prompt, system_prompt=P.DESCRIBE_SYSTEM,
                                   image_urls=refs),
                timeout=REVIEW_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning(LOG_TAG + " 图像描述超时（>%ds）" % REVIEW_TIMEOUT)
            return ""
        except Exception as exc:
            logger.warning(LOG_TAG + " 图像描述失败（%s）：%r", image_ref[:50], exc)
            return ""
        return (getattr(resp, "completion_text", "") or "").strip()[:60]

    async def terminate(self) -> None:
        """插件卸载：落盘索引。"""
        self._save_manifest()
        logger.info(LOG_TAG + " 插件已卸载，表情包库 %d 张" % len(self.manifest))
