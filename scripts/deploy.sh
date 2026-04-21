#!/usr/bin/env bash
# deploy.sh — Sync code to EC2 and restart all services.
#
# Usage (from repo root):
#   bash scripts/deploy.sh
#
# Override defaults:
#   REMOTE_HOST=ubuntu@1.2.3.4 IDENTITY=~/.ssh/other-key.pem bash scripts/deploy.sh

set -euo pipefail

REMOTE_HOST=${REMOTE_HOST:-ubuntu@52.21.17.69}
REMOTE_DIR=${REMOTE_DIR:-/opt/ads-mcp}
IDENTITY=${IDENTITY:-~/.ssh/rd-mcp-key.pem}
VENV="$REMOTE_DIR/.venv"

SSH="ssh -i $IDENTITY -o StrictHostKeyChecking=no"
SCP="scp -i $IDENTITY"
RSYNC_SSH="ssh -i $IDENTITY -o StrictHostKeyChecking=no"

echo "==> Syncing code to ${REMOTE_HOST}:${REMOTE_DIR}"
rsync -az --delete \
    --exclude '.venv' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude '.git' \
    --exclude 'local-dev-config.json' \
    --exclude '.env' \
    -e "$RSYNC_SSH" \
    ./ "${REMOTE_HOST}:${REMOTE_DIR}/"

echo "==> Installing/updating Python dependencies"
$SSH "$REMOTE_HOST" bash -s <<ENDSSH
set -e
for svc in google-ads meta-ads analytics search-console content-agent; do
    req="$REMOTE_DIR/servers/\$svc/requirements.txt"
    if [[ -f "\$req" ]]; then
        "$VENV/bin/pip" install --quiet -r "\$req"
    fi
done
# Always ensure fastmcp is present
"$VENV/bin/pip" install --quiet "fastmcp>=2.0.0"
ENDSSH

echo "==> Updating systemd service files"
$SSH "$REMOTE_HOST" bash -s <<ENDSSH
set -e
for f in $REMOTE_DIR/infrastructure/systemd/*.service; do
    svc_name=\$(basename "\$f")
    sudo cp "\$f" "/etc/systemd/system/\$svc_name"
done
sudo systemctl daemon-reload
ENDSSH

echo "==> Restarting services"
$SSH "$REMOTE_HOST" bash -s <<ENDSSH
set -e
for svc in google-ads-mcp meta-ads-mcp analytics-mcp search-console-mcp content-agent; do
    sudo systemctl restart "\$svc" && echo "  restarted \$svc" || echo "  WARNING: failed to restart \$svc"
done
ENDSSH

echo "==> Reloading Nginx"
$SSH "$REMOTE_HOST" bash -s <<ENDSSH
sudo cp $REMOTE_DIR/infrastructure/nginx.conf /etc/nginx/sites-available/ads-mcp
sudo nginx -t && sudo systemctl reload nginx
ENDSSH

echo ""
echo "==> Verifying health endpoints"
sleep 2
BASE="http://52.21.17.69"
for path in google-ads meta-ads analytics search-console content; do
    status=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/$path/health" 2>/dev/null || echo "ERR")
    echo "  $path/health -> $status"
done

echo ""
echo "Deploy complete."
