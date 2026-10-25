#!/bin/sh
# 186：验证单唤醒人配置（B）——设一组值再读回（2026-10-25）
echo "== 设：瑞禾 pct=60 / words=[瑞禾, 瑞] / hours=[8,24] =="
python3 - <<'PY'
import json
p = "/root/higan/groups.json"
c = json.load(open(p, encoding="utf-8"))
c.setdefault("wake_person_conf", {})["3643349392"] = {
    "pct": 60.0, "words": ["瑞禾", "瑞"], "hours": [8, 24],
}
json.dump(c, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("  已写入:", json.dumps(c["wake_person_conf"], ensure_ascii=False))
PY
echo
echo "== 读回（配置表现状）=="
python3 -c "import json;print(json.dumps(json.load(open('/root/higan/groups.json',encoding='utf-8')).get('wake_person_conf'),ensure_ascii=False,indent=2))" | sed 's/^/  /'
echo
echo "== 工具是否注册 =="
grep -c 'wake_person_set' /root/astrbot-src/data/plugins/astrbot_plugin_higan/main.py | sed 's/^/  wake_person_set: /'
grep -c '_person_conf' /root/astrbot-src/data/plugins/astrbot_plugin_higan/main.py | sed 's/^/  _person_conf: /'
echo
echo "== 服务 =="
systemctl is-active astrbot-186 | sed 's/^/  /'
echo "[B verify]"
