#!/bin/sh
# 186：唤醒人标记现在的样子 + 全部工具清单（2026-10-25）
BASE=/root/astrbot-src/data/plugins/astrbot_plugin_higan
echo "== 唤醒人标记代码 =="
grep -n '唤醒人 QQ' $BASE/main.py | head -3 | cut -c1-150 | sed 's/^/  /'
echo
echo "== 全部工具（名字）=="
grep -oE '_f\("[a-z_0-9]+"' $BASE/main.py | sed 's/_f("//;s/"//' | sort -u | nl | sed 's/^/  /'
echo
echo "== 工具总数 =="
grep -oE '_f\("[a-z_0-9]+"' $BASE/main.py | sort -u | wc -l | sed 's/^/  /'
echo
echo "== wp_* / facts_* / wake_* 实现是否齐全 =="
for n in wake_list wake_search wake_add wake_remove wp_fact_add wp_fact_list wp_fact_search wp_fact_delete wp_fact_move facts_list facts_search facts_edit facts_delete remember self_status; do
  c=$(grep -c "\"$n\"" $BASE/main.py)
  printf "  %-16s %s\n" "$n" "$c"
done
echo
echo "== 服务 =="
systemctl is-active astrbot-186 | sed 's/^/  /'
echo "[tools audit]"
