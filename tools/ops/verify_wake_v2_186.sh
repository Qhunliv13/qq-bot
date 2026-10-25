#!/bin/sh
# 186：核对唤醒 v2 + 唤醒人记录 + 事实 2000（2026-10-25）
BASE=/root/astrbot-src/data/plugins/astrbot_plugin_higan
echo "== 新工具 =="
grep -oE '_f\("(wake_[a-z]+|wp_fact_[a-z]+)"' $BASE/main.py | sed 's/_f("//;s/"//' | sort -u | tr '\n' ' '
echo
echo
echo "== 旧分支是否停用 =="
grep -n '__wake_v1_disabled__' $BASE/main.py | head -2 | cut -c1-90 | sed 's/^/  /'
echo
echo "== 事实上限 =="
grep -n '^MAX_FACTS' $BASE/memory.py | sed 's/^/  /'
grep -n 'MAX_PER_PERSON' $BASE/memory.py | head -2 | sed 's/^/  /'
echo
echo "== 唤醒表（groups.json）=="
python3 - <<'PY'
import json
c = json.load(open("/root/higan/groups.json", encoding="utf-8"))
print("  wake_persons:", c.get("wake_persons"))
print("  wake_words:", c.get("wake_words"))
print("  reply_pct:", c.get("reply_pct"))
PY
echo
echo "== 服务与自检 =="
systemctl is-active astrbot-186 | sed 's/^/  active: /'
/root/astrbot-venv/bin/python - <<'PY'
import importlib.util, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location(
    "m", "/root/astrbot-src/data/plugins/astrbot_plugin_higan/memory.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
st = mod.WakePersonFacts("/root/higan/memory/wake_persons.json").load()
fid = st.add("1454594675", "自检：摸头怪是唤醒人")
print("  WakePersonFacts add ->", fid, "count:", st.count("1454594675"))
print("  delete ->", st.delete("1454594675", fid))
print("  MAX_FACTS =", mod.MAX_FACTS, "| per-person =", st.MAX_PER_PERSON)
PY
echo "[wake v2 verify]"
