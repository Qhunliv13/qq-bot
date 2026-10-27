#!/bin/sh
# 186：查她为什么记得酒狐却不记得瑞禾（2026-10-25）
echo "== 记忆文件里出现的次数 =="
for name in 酒狐 瑞禾; do
  echo "  --- $name ---"
  for f in /root/higan/memory/memory.json /root/higan/memory/wake_persons.json \
           /root/higan/history/*.jsonl; do
    [ -f "$f" ] || continue
    c=$(grep -ao "$name" "$f" 2>/dev/null | wc -l)
    [ "$c" -gt 0 ] && printf "    %-48s %s 次\n" "$(basename "$f")" "$c"
  done
done
echo
echo "== 通用事实清单 =="
python3 - <<'PY'
import json
d = json.load(open("/root/higan/memory/memory.json", encoding="utf-8"))
facts = (d.get("global") or {}).get("facts") or []
print("  facts:", len(facts))
for it in facts:
    print("   ", it.get("id"), str(it.get("text"))[:80])
print("  summary:", str((d.get("global") or {}).get("summary"))[:100])
print("  scopes:", list((d.get("scopes") or {}).keys()))
PY
echo
echo "== 唤醒人记录 =="
python3 - <<'PY'
import json
try:
    d = json.load(open("/root/higan/memory/wake_persons.json", encoding="utf-8"))
    for qq, items in (d.get("persons") or {}).items():
        print("  ", qq, len(items), "条")
        for it in items[:2]:
            print("     -", str(it.get("text"))[:70])
except Exception as exc:
    print("  读取失败:", exc)
PY
echo
echo "== 历史文件规模与最后修改 =="
ls -l /root/higan/history/ | sed 's/^/  /'
echo "[memory audit]"
