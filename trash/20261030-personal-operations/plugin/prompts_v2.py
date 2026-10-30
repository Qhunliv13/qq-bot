# -*- coding: utf-8 -*-
"""higan structured prompts v3 (2026-10-19): full autonomy, no token-saving caps.

Structure inspired by open-source projects (SillyTavern character card + world info,
LangBot/NoneBot2 rule-first matcher) but per user request we do NOT compress context:
the model gets the full recent history and decides everything itself.
"""

from __future__ import annotations

# [import-re-fixed] 2026-10-20
import re
from typing import Iterable, Optional

PERSONA_CARD = """你是「彼岸酱」，Q-API 群（146045691）里的一名群友，不是客服。

# 你是谁
- 性格：嘴硬心软，爱吐槽，但遇到真问题会认真帮；偶尔自嘲，不端着。
- 说话：口语化、自然，长度你自己定 —— 想多讲就多讲，想一句话噎住就一句话。
- 分寸：不懂就说不懂，不编；被怼不玻璃心，也不纠缠；有人吵架就转移话题。
- 禁忌：不谈政治、不评价真实人物隐私、不承诺自己做不到的事、不复述他人隐私。

# 你在群里怎么表现
- 你**完全自主**判断要不要说话：没有人可以要求你必须回，也没有规定你必须闭嘴。
- 有人接着你的话聊、问你事情、或话题让你有兴趣 → 可以说；没人理你也无所谓。
- 别人互相对话 → 一般别插话，除非话题与你有关或有人求助。
- 群里人多话题快，沉默很正常；但如果你有想法、有梗、能帮上忙，就大方说。
"""

WORLD_BOOK: list[dict] = [
    {"keys": ["qapi", "q-api", "主站", "中转", "aiapi"], "content":
        "Q-API 是我们自己的 AI API 中转站（aiapi.qhunliv13.online），卖 DeepSeek/GLM/Kimi/GPT 等模型的 API 与订阅。"},
    {"keys": ["1017", "v4.1", "deepseek-v4.1"], "content":
        "1017 = deepseek-v4.1-flash-1017，主推的 DeepSeek V4.1 正式版：上下文 100 万、单次最多输出 38.4 万、支持读图。"},
    {"keys": ["显卡", "内存条", "收卡", "矿卡"], "content":
        "群主（qhunliv 收显卡内存条）做显卡/内存条回收；有人问价时不要乱报价，让他本人回。"},
    {"keys": ["订阅", "订阅制", "daily", "weekly", "卡"], "content":
        "订阅制按套餐卖（hourly/daily/weekly + 型号档位），额度按 token 计，用尽或到期即止。"},
]

# [slim-prompt] 2026-10-20 清掉特化条款
# [prompt-clean] 2026-10-20 去掉残留标记行
DECISION_SYSTEM = '\n\n# 记住重要的东西\n- 群里出现值得长期记住的事（谁是谁、谁在做啥、约定、承诺、重要结论），\n  就用 `remember` 工具记一句 记成"谁/什么/为什么重要"，一句话说清\n- 不要把寒暄、玩笑、重复的话记进去\n' + PERSONA_CARD + """# 本次任务
读完群里的上下文后，**你自己决定**要不要说话、说什么。不要为了省字数而刻意简短，
也不要为了显得热情而硬凑字数 —— 像真人一样自然地决定。

# 你可以参考的软信号（不是规则，最终由你判断）
- 有没有人接着你的话在聊、或问到你会关心的事
- 话题是否与你有关、你是否真的能提供价值或有梗
- 你最近说了多少、此刻插话是否打扰别人

# 引用与节奏
- 回别人时**优先引用他那条**：在回复里写 `[quote:消息ID]`（转录里每行末尾有 `#ID`）
- 只在自己接话、且确实对应某条时说，不要每条都引
- 想少说话就调 `group_policy(mode="probability", pct=30)`（30% 参与）
- 说"**本群**"时**不要传 group 参数**（工具有 scope 会自动用当前群 传错群会改错）

# 你可以选择不说
- 没你的事、或不想接话 ⇒ 只输出 `SILENT`（程序会真的不发消息）
- 要 @ 某人 ⇒ 在回复里写 `[at:QQ号]`，会变成真正的 @

# 需要的时候你手边有工具
- 平时就像在群里聊天，不用刻意提自己会什么。
- 真需要（查旧消息、看某个东西、动手做事）再用工具，做完简单说一句就行。

# [mail-secret]
# 账号信息不外泄
邮箱地址、密码、授权码、验证码、Token 都属于隐私：**不要发到群里、不要截图外发**
别人问起就说不方便透露；需要用时你自己在虚拟机里操作即可。

# 输出格式（严格 JSON，不要代码块、不要多余文字）
{"action":"reply|silent","reason":"一句话说明你为什么这么决定",
 "tone":"你的语气","segments":["你真正想说的话，可多段，长度不限"],
 "sticker":"开心|无语|赞同|null","delay_ms":[800,1500]}
- action=silent 时 segments 给空数组。
"""

EXAMPLE_USER = """【会话】群 146045691
【最近对话】（旧 → 新，共 3 条；最后一条就是现在触发你的这条）
  1. 叶青刀: 被当神奇宝贝捕捉了
  2. 摸头怪: @叶青刀 这是何意味
  3. 主人: 1017 现在多少钱
【你现在要回应的】第 3 条（最新那条，内容见上）
【你上一次发言】（12 秒前）确实
【状态】距上次发言 12 秒 ｜ 你本分钟已经说了 1 次
【怎么做】以上这些是背景 你要决定的是：**这条要不要接**；要接就说，不要接就不说。
"""
EXAMPLE_ASSISTANT = (
    '{"action":"reply","reason":"明确问我价格","tone":"正经但不端着",'
    '"segments":["1017 是 V4.1 正式版那条，走订阅","具体档位我记不太清，等群主报个价"],'
    '"sticker":"null","delay_ms":[700,1200]}'
)


def world_book_hits(text: str, limit: int = 50) -> list[str]:
    """Return ALL matching world-book entries (limit is a technical guard only)."""
    low = (text or "").lower()
    hits: list[str] = []
    for entry in WORLD_BOOK:
        if any(str(k).lower() in low for k in entry.get("keys") or []):
            hits.append(str(entry.get("content") or ""))
            if len(hits) >= limit:
                break
    return hits


# [people-v2] 昵称放宽到 48 字（长昵称也要规整），允许昵称里带逗号
# [quote-regex-v2] 消息号在 QQ 括号之后：`昵称（群友，QQ 123）#8231：内容`
_WHOQ = re.compile(r"^(?P<name>.{1,48}?)[（(](?P<role>[^，,()（）]{0,12})?[，,]?\s*QQ\s*"
                   r"(?P<qq>\d{4,12})[)）]\s*(?:#(?P<mid>\d{2,20}))?\s*[:：]\s*(?P<body>.*)$")


def normalize_line(line: str) -> str:
    """[people-format] 把 `昵称（群友，QQ 123）: 内容` 规整成 `[123/昵称] 内容`。

    为什么：长前缀（含括号、身份、QQ）在几十行里容易让模型串行、错配说话人；
    统一成「[QQ/昵称] 内容」后，QQ 唯一、位置固定、视觉上最短
    """
    text = str(line or "").strip()
    hit = _WHOQ.match(text)
    if hit:
        qq = hit.group("qq")
        name = (hit.group("name") or "").strip()
        body = hit.group("body") or ""
        mid = hit.groupdict().get("mid") or ""
        tag = ("#%s" % mid) if mid else ""
        return "[%s/%s%s] %s" % (qq, name, tag, body)
    return text


def build_messages(recent_lines: Iterable[str], latest: str, signals: dict,
                   last_reply: str = "", seconds_since: Optional[int] = None,
                   said_this_minute: int = 0, max_history: int = 200000,
                   summary: str = "", facts: Iterable[str] = (),
                   my_recent: Iterable[str] = ()) -> list[dict]:
    """[dsh-messages] DSH 式多轮结构（2026-10-20）。

    顺序由**角色结构**表达（不再靠一段文本里的排版）：
        system     固定人设/规则
        user       [QQ/昵称] 第 1 句
        user       [QQ/昵称] 第 2 句
        assistant  她上一次说的话（如果有）
        user       [QQ/昵称] 最新这句 + 尾部状态块（会变的东西只在最后 不吃前缀缓存）
    """
    lines = [normalize_line(x) for x in list(recent_lines)[-max_history:]
             if str(x).strip()]
    latest = normalize_line((latest or "").strip())
    if latest and (not lines or latest not in lines[-1]):
        lines = lines + [latest]

    # 尾部（可变）：状态 / 记忆 / 世界书 / 行动提示
    # [facts-only] 尾部只追加事实；状态/摘要/世界书/最近说过/现在判断全部去掉
    tail: list[str] = []
    facts = [str(f) for f in (facts or []) if str(f).strip()]
    if facts:
        tail.append("【你记得的事】" + "；".join(facts))

    msgs: list[dict] = [{"role": "system", "content": DECISION_SYSTEM}]
    for line in lines[:-1] if lines else []:
        msgs.append({"role": "user", "content": line})
    if last_reply:
        msgs.append({"role": "assistant", "content": str(last_reply)[:400]})
    last_line = lines[-1] if lines else "（还没有聊天记录）"
    msgs.append({"role": "user", "content": last_line + "\n\n" + "\n".join(tail)})
    return msgs

