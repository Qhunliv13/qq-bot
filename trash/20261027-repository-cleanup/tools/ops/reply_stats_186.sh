#!/bin/sh
# 186：统计她最近的"回/不回"（2026-10-25）
echo "== 近 40 分钟计数 =="
for k in 命中唤醒人 本节_at_了她 工具优先通路产出回复 她用工具结果自己回了 插件已发送 回过了 未命中回复概率 选择沉默; do
  c=$(journalctl -u astrbot-186 --since '-40min' --no-pager 2>/dev/null | grep -ac "$k")
  printf "  %-24s %s\n" "$k" "$c"
done
echo
echo "== 最近 8 条实际结果 =="
journalctl -u astrbot-186 --since '-40min' --no-pager 2>/dev/null \
  | grep -aE '产出回复|自己回了|插件已发送|选择沉默|回过了|本条 @ 了她|命中唤醒' \
  | tail -8 | cut -c1-150 | sed 's/^/  /'
echo
echo "== 她最近 3 条真实发送（NapCat 侧，按时间取）=="
docker logs --tail 400 napcat 2>&1 | sed 's/\x1b\[[0-9;]*m//g' | grep -a '发送 ->' | tail -3 | cut -c1-140 | sed 's/^/  /'
echo "[reply stats]"
