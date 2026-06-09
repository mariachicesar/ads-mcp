#!/usr/bin/env bash
# sync-and-publish.sh
#
# 1. Pulls latest code from origin/main (scripts only — queue file is not in git)
# 2. Runs the scheduled-post publisher
#
# The queue file (servers/gbp/scheduled_posts.json) lives only on EC2's disk.
# Push new posts to EC2 via: scripts/push-queue.sh
#
# Designed to be called by cron every 15 minutes.

set -euo pipefail

REPO=/opt/ads-mcp
PYTHON="$REPO/.venv/bin/python3"
export AWS_REGION="${AWS_REGION:-us-east-1}"

cd "$REPO"

# --- 1. Pull latest code from origin ---
git pull -q origin main || true

# --- 2. Publish due posts ---
"$PYTHON" "$REPO/scripts/run-scheduled-posts.py"
