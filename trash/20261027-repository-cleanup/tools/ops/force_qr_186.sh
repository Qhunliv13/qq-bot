#!/bin/sh
# 186：强制刷新二维码（重启容器 → 等新码 → 输出 base64）2026-10-25
set -e
OLD=$(docker exec napcat stat -c %Y /app/napcat/cache/qrcode.png 2>/dev/null || echo 0)
echo "  旧码 mtime: $OLD"
docker restart napcat >/dev/null 2>&1
echo "  容器已重启，等待新二维码…"
i=0
while [ $i -lt 40 ]; do
  sleep 3
  NEW=$(docker exec napcat stat -c %Y /app/napcat/cache/qrcode.png 2>/dev/null || echo 0)
  if [ "$NEW" != "0" ] && [ "$NEW" != "$OLD" ]; then
    docker cp napcat:/app/napcat/cache/qrcode.png /root/qr186.png >/dev/null 2>&1
    AGE=$(( $(date +%s) - $NEW ))
    echo "  ✓ 新码 mtime=$NEW（距今 ${AGE}s，$(wc -c < /root/qr186.png) 字节）"
    break
  fi
  i=$((i+1))
done
base64 -w0 /root/qr186.png
echo
echo "[qr fresh]"
