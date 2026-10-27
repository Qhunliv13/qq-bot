"""Scoped fact labels and precise deletion; labels never grant permissions.

The caller supplies authenticated scope/write permission. Read-only delegates may
read categories and propose classifications, but cannot execute mutations here.
"""

from .memory import Memory


def schemas() -> list[dict]:
    """Expose additive tool schemas; existing facts_delete metadata stays with its caller."""
    return [
        {"type": "function", "function": {
            "name": "facts_categories",
            "description": "列出当前可见事实的类别及数量；未分类数量单独统计。类别只是标签，不代表权限。",
            "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}}},
        {"type": "function", "function": {
            "name": "facts_classify",
            "description": "按精确事实 ID 批量设置完整类别列表（替换旧标签）；空类别数组清除分类。可先读取全部可见事实并提出分类方案，不预设分类规则。所有 ID 均可见时整批设置，否则不修改。",
            "parameters": {"type": "object", "properties": {
                "ids": {"type": "array", "minItems": 1,
                        "items": {"type": "string", "minLength": 1}},
                "categories": {"type": "array", "items": {"type": "string", "minLength": 1}}},
                "required": ["ids", "categories"], "additionalProperties": False}}},
    ]


def dispatch(name: str, args: dict, *, memory: Memory, current_scope: str,
             can_write: bool, muted=()):
    """Handle scoped fact operations, returning None only for unrelated tool names."""
    if name not in {"facts_categories", "facts_classify", "facts_delete"}:
        return None
    if not isinstance(args, dict):
        return {"ok": False, "error": "工具参数必须是对象"}
    # Scope and write permission are exclusively caller context, never tool arguments.
    if not isinstance(current_scope, str) or not current_scope:
        return {"ok": False, "error": "缺少当前会话范围"}
    if name != "facts_categories" and not can_write:
        return {"ok": False, "error": "只读上下文不能修改事实"}
    try:
        if name == "facts_categories":
            return {"ok": True, **memory.category_counts(current_scope=current_scope, muted=muted)}
        if name == "facts_classify":
            if "categories" not in args:
                return {"ok": False, "error": "必须显式提供 categories 数组；空数组清除分类"}
            return memory.classify_facts(args.get("ids"), args["categories"],
                                         current_scope=current_scope, muted=muted)
        fact_id, keyword = args.get("id", ""), args.get("keyword", "")
        if not isinstance(fact_id, str) or not isinstance(keyword, str):
            return {"ok": False, "error": "id 和 keyword 必须是字符串"}
        fact_id, keyword = fact_id.strip(), keyword.strip()
        if not fact_id and not keyword:
            return {"ok": False, "error": "请指定精确事实 id，或明确的旧版关键词"}
        removed = memory.delete_fact(fact_id, keyword, current_scope=current_scope,
                                     muted=muted, checked=True)
        return {"ok": True, "removed": removed}
    except (TypeError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}
    except Exception:
        # Never expose raw storage paths/errors or claim an unpersisted mutation succeeded.
        return {"ok": False, "error": "事实保存失败，未完成修改"}
