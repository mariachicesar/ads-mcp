#!/usr/bin/env bash
# sync-and-publish.sh
#
# 1. Fetches latest code from origin/main
# 2. Merges NEW queue entries from origin into EC2's live queue
#    (does NOT overwrite entries already removed by a prior publish run)
# 3. Checks out non-queue changed files from origin
# 4. Runs the scheduled-post publisher
#
# Designed to be called by cron every 15 minutes.

set -euo pipefail

REPO=/opt/ads-mcp
QUEUE="$REPO/servers/gbp/scheduled_posts.json"
PYTHON="$REPO/.venv/bin/python3"
export AWS_REGION="${AWS_REGION:-us-east-1}"

cd "$REPO"

# --- 1. Fetch latest from origin (no merge yet) ---
git fetch -q origin main

# --- 2. Merge new queue entries from origin into local queue ---
"$PYTHON" - <<'EOF'
import json, subprocess, sys

result = subprocess.run(
    ["git", "show", "FETCH_HEAD:servers/gbp/scheduled_posts.json"],
    capture_output=True, text=True
)
if result.returncode != 0:
    print("Could not read origin queue, skipping merge.", file=sys.stderr)
    sys.exit(0)

origin_queue = json.loads(result.stdout)

queue_path = "servers/gbp/scheduled_posts.json"
try:
    local_queue = json.loads(open(queue_path).read())
except Exception:
    local_queue = []

local_ids = {e["id"] for e in local_queue}
new_entries = [e for e in origin_queue if e["id"] not in local_ids]

if new_entries:
    merged = local_queue + new_entries
    open(queue_path, "w").write(json.dumps(merged, indent=2, ensure_ascii=False))
    print(f"Merged {len(new_entries)} new queue entries from origin.")
EOF

# --- 3. Checkout non-queue files from origin (code updates) ---
git checkout -q FETCH_HEAD -- \
    .gitignore \
    scripts/deploy.sh \
    scripts/run-scheduled-posts.py \
    scripts/sync-and-publish.sh 2>/dev/null || true

# --- 4. Publish due posts ---
"$PYTHON" "$REPO/scripts/run-scheduled-posts.py"
