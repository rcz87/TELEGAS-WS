#!/bin/bash
# TELEGLAS Pro — Auto Deploy
# Checks for new commits on the configured branch and auto-deploys.
# Run via cron every 2 minutes, or as a PM2 process with --cron.
#
# Usage:
#   # One-shot check (for cron):
#   */5 * * * * /root/TELEGAS-WS/scripts/auto-deploy.sh >> /root/TELEGAS-WS/logs/auto-deploy.log 2>&1
#
#   # Or run as PM2 managed process (loop mode):
#   pm2 start /root/TELEGAS-WS/scripts/auto-deploy.sh --name auto-deploy --cron "*/5 * * * *" --no-autorestart

set -euo pipefail

REPO_DIR="/root/TELEGAS-WS"
BRANCH="${DEPLOY_BRANCH:-main}"
DASHBOARD_DEST="/root/tg-dashboard"
LOG_PREFIX="[auto-deploy]"
LOCKFILE="/tmp/teleglas-auto-deploy.lock"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $LOG_PREFIX $*"; }

# Prevent concurrent runs
if [ -f "$LOCKFILE" ]; then
    LOCK_PID=$(cat "$LOCKFILE" 2>/dev/null || echo "")
    if [ -n "$LOCK_PID" ] && kill -0 "$LOCK_PID" 2>/dev/null; then
        log "Another deploy is running (PID $LOCK_PID), skipping"
        exit 0
    fi
    # Stale lock, remove it
    rm -f "$LOCKFILE"
fi
echo $$ > "$LOCKFILE"
trap 'rm -f "$LOCKFILE"' EXIT

cd "$REPO_DIR"

# Fetch latest from remote (only the target branch)
log "Fetching origin/$BRANCH..."
if ! git fetch origin "$BRANCH" --quiet 2>/dev/null; then
    log "WARN: git fetch failed (network?), retrying in 5s..."
    sleep 5
    if ! git fetch origin "$BRANCH" --quiet 2>/dev/null; then
        log "ERROR: git fetch failed twice, aborting"
        exit 1
    fi
fi

# Compare local HEAD with remote
LOCAL_HEAD=$(git rev-parse HEAD)
REMOTE_HEAD=$(git rev-parse "origin/$BRANCH")

if [ "$LOCAL_HEAD" = "$REMOTE_HEAD" ]; then
    log "Up to date ($LOCAL_HEAD)"
    exit 0
fi

log "New commits detected! $LOCAL_HEAD -> $REMOTE_HEAD"
log "Commits to deploy:"
git log --oneline "$LOCAL_HEAD".."$REMOTE_HEAD" | head -10

# Pull changes
log "Pulling changes..."
git pull origin "$BRANCH" --ff-only
if [ $? -ne 0 ]; then
    log "ERROR: git pull failed (merge conflict?), aborting"
    exit 1
fi

# Check if requirements.txt changed
if git diff --name-only "$LOCAL_HEAD" "$REMOTE_HEAD" | grep -q "requirements.txt"; then
    log "requirements.txt changed, installing dependencies..."
    pip3 install -r requirements.txt --break-system-packages --quiet 2>/dev/null || true
fi

# Ensure dashboard symlinks (idempotent — creates if missing, no-op if already correct)
DASHBOARD_CHANGED=false
if git diff --name-only "$LOCAL_HEAD" "$REMOTE_HEAD" | grep -q "src/dashboard/"; then
    DASHBOARD_CHANGED=true
    log "Dashboard files changed, ensuring symlinks to $DASHBOARD_DEST..."
    # Replace files with symlinks if they aren't already
    [ ! -L "$DASHBOARD_DEST/server.py" ] && rm -f "$DASHBOARD_DEST/server.py" && ln -s "$REPO_DIR/src/dashboard/server.py" "$DASHBOARD_DEST/server.py"
    [ ! -L "$DASHBOARD_DEST/static/index.html" ] && rm -f "$DASHBOARD_DEST/static/index.html" && ln -s "$REPO_DIR/src/dashboard/static/index.html" "$DASHBOARD_DEST/static/index.html"
    [ -f "$REPO_DIR/src/dashboard/static/sw.js" ] && [ ! -L "$DASHBOARD_DEST/static/sw.js" ] && rm -f "$DASHBOARD_DEST/static/sw.js" && ln -s "$REPO_DIR/src/dashboard/static/sw.js" "$DASHBOARD_DEST/static/sw.js"
fi

# Restart services
MAIN_FILES_CHANGED=$(git diff --name-only "$LOCAL_HEAD" "$REMOTE_HEAD" | grep -qE "^(main\.py|src/|config/)" && echo true || echo false)

if [ "$MAIN_FILES_CHANGED" = "true" ]; then
    log "Core files changed, restarting teleglas..."
    pm2 restart teleglas 2>/dev/null || pm2 restart teleglas-pro 2>/dev/null || log "WARN: Could not restart main process"
fi

if [ "$DASHBOARD_CHANGED" = "true" ]; then
    log "Restarting tg-dashboard..."
    pm2 restart tg-dashboard 2>/dev/null || log "WARN: Could not restart dashboard"
fi

log "Deploy complete! Now at $(git rev-parse --short HEAD)"
log "---"
