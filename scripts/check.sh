#!/bin/bash
# TELEGLAS Pro - System Health Check

echo "╔════════════════════════════════════════════════════════════╗"
echo "║         TELEGLAS Pro - System Health Check                ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""

echo "📊 PM2 Status:"
pm2 list

echo ""
echo "🔌 Port 8080 Status:"
if ss -tuln | grep -q ":8080"; then
    echo "✅ Port 8080 is OPEN"
else
    echo "❌ Port 8080 is CLOSED"
fi

echo ""
echo "🔥 Firewall Status:"
ufw status | grep "8080\|22"

echo ""
echo "💾 Disk Space:"
df -h / | grep -v Filesystem

echo ""
echo "🧠 Memory Usage:"
free -h | grep -E "Mem:|Swap:"

echo ""
echo "📝 Recent Logs (last 10 lines):"
pm2 logs teleglas-pro --lines 10 --nostream 2>/dev/null || echo "No logs available"

echo ""
echo "🌐 Dashboard Access:"
echo "   Local:  http://localhost:8080"
echo "   Public: http://YOUR_VPS_IP:8080"
echo ""
