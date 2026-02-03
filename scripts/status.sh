#!/bin/bash
# Check TELEGLAS Pro status

echo "=== TELEGLAS Pro Status ==="
echo ""

# PM2 status
pm2 status

echo ""
echo "=== Recent Logs ==="
pm2 logs teleglas-pro --lines 20 --nostream

echo ""
echo "=== System Resources ==="
pm2 monit
