#!/bin/sh
# 186：重启 NapCat 让它重连 OneBot；顺带报告是否掉登（2026-10-25）
set -e
echo "  重启前容器: $(docker inspect -f '{{.State.Status}}' napcat)"
docker restart napcat >/dev/null 2>&1
echo "  已重启 napcat，等待重连…"
i=0
while [ $i -lt 30 ]; do
  sleep 4
  A=$(journalctl -u astrbot-186 --since '-2min' --no-pager 2>/dev/null | grep -ac '适配器已连接')
  W=$(docker logs --tail 80 napcat 2>&1 | sed 's/\x1b\[[0-9;]*m//g' | grep -ac 'WebSocket')
  if [ "$A" -gt 0 ]; then
    echo "  ✓ AstrBot 已连接（发现 $A 条适配器已连接日志）"
    break
  fi
  i=$((i+1))
done
echo
echo "== 登录态 =="
docker logs --tail 120 napcat 2>&1 | sed 's/\x1b\[[0-9;]*m//g' \
  | grep -aE '登录成功|Login Success|二维码|ErrCode|接收 <-' | tail -5 | cut -c1-150 | sed 's/^/  /'
echo
echo "== OneBot 连接（napcat 侧）=="
docker logs --tail 120 napcat 2>&1 | sed 's/\x1b\[[0-9;]*m//g' \
  | grep -aE 'WebSocket|6199|连接成功' | tail -4 | cut -c1-150 | sed 's/^/  /'
echo
echo "== 二维码（如果重新要扫）=="
if docker exec napcat test -f /app/napcat/cache/qrcode.png 2>/dev/null; then
  docker cp napcat:/app/napcat/cache/qrcode.png /root/qr186.png >/dev/null 2>&1
  echo "  qrcode: $(stat -c '%s 字节 %y' /root/qr186.png)"
else
  echo "  容器里没有二维码文件（说明没退到扫码阶段）"
fi
echo "[napcat restart done]"
