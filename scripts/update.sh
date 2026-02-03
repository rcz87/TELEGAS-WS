#!/bin/bash
# TELEGLAS Pro - Update from GitHub and Restart

echo "🔄 Updating TELEGLAS Pro from GitHub..."
cd /root/TELEGAS-WS

echo "📥 Pulling latest changes..."
git pull origin main

echo "📦 Installing/updating dependencies..."
pip3 install -r requirements.txt --break-system-packages --quiet

echo "🔄 Restarting system..."
pm2 restart teleglas-pro

echo ""
echo "✅ Update complete!"
echo ""
pm2 logs teleglas-pro --lines 10 --nostream
