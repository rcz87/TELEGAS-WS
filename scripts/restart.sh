#!/bin/bash
# TELEGLAS Pro - Restart Script

echo "🔄 Restarting TELEGLAS Pro..."
pm2 restart teleglas-pro

echo ""
echo "📊 Current Status:"
pm2 list

echo ""
echo "✅ Restart complete!"
