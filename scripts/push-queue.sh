#!/usr/bin/env bash
# push-queue.sh
#
# Copies the local scheduled_posts.json to EC2, merging new entries into
# EC2's live queue (preserving already-published entries that were removed).
#
# Usage: bash scripts/push-queue.sh

set -euo pipefail

KEY="${SSH_KEY:-$HOME/.ssh/rd-mcp-key.pem}"
HOST="${EC2_HOST:-ubuntu@52.21.17.69}"
REMOTE_REPO="/opt/ads-mcp"
LOCAL_QUEUE="servers/gbp/scheduled_posts.json"
REMOTE_QUEUE="$REMOTE_REPO/servers/gbp/scheduled_posts.json"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "Pushing new queue entries to EC2..."

# Upload local queue to a temp file on EC2
scp -q -i "$KEY" "$LOCAL_QUEUE" "$HOST:/tmp/local_queue.json"

# Merge new entries into EC2's live queue
ssh -i "$KEY" "$HOST" python3 - <<'EOF'
import json

local_q = json.loads(open("/tmp/local_queue.json").read())
try:
    remote_q = json.loads(open("/opt/ads-mcp/servers/gbp/scheduled_posts.json").read())
except Exception:
    remote_q = []

remote_ids = {e["id"] for e in remote_q}
new_entries = [e for e in local_q if e["id"] not in remote_ids]

if new_entries:
    merged = remote_q + new_entries
    open("/opt/ads-mcp/servers/gbp/scheduled_posts.json", "w").write(
        json.dumps(merged, indent=2, ensure_ascii=False)
    )
    print(f"Merged {len(new_entries)} new entries into EC2 queue.")
else:
    print("No new entries to merge — EC2 queue is already up to date.")
EOF

echo "Done."
