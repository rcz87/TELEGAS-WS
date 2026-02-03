#!/bin/bash
# Stop TELEGLAS Pro

echo "Stopping TELEGLAS Pro..."

pm2 stop teleglas-pro
pm2 stop teleglas-monitor

echo "TELEGLAS Pro stopped"
