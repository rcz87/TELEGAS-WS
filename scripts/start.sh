#!/bin/bash
# Start TELEGLAS Pro

echo "Starting TELEGLAS Pro..."

# Check if PM2 is installed
if ! command -v pm2 &> /dev/null; then
    echo "PM2 not found. Please install: npm install -g pm2"
    exit 1
fi

# Start with PM2
pm2 start ecosystem.config.js

# Show status
pm2 status

echo "TELEGLAS Pro started successfully!"
echo "View logs: pm2 logs teleglas-pro"
echo "Stop: pm2 stop teleglas-pro"
