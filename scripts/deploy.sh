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
UPDATE_NGINX=${UPDATE_NGINX:-false}

SSH="ssh -i $IDENTITY -o StrictHostKeyChecking=no"
SCP="scp -i $IDENTITY"
echo "==> Syncing code to ${REMOTE_HOST}:${REMOTE_DIR}"
tar -czf - \
    --exclude='./.venv' \
    --exclude='./__pycache__' \
    --exclude='./*.pyc' \
    --exclude='./.git' \
    --exclude='./local-dev-config.json' \
    --exclude='./.env' \
    --exclude='./servers/gbp/scheduled_posts.json' \
    . | $SSH "$REMOTE_HOST" "mkdir -p $REMOTE_DIR && tar -xzf - -C $REMOTE_DIR"

echo "==> Installing/updating Python dependencies"
$SSH "$REMOTE_HOST" bash -s <<ENDSSH
set -e
for svc in google-ads meta-ads analytics search-console content-agent gbp orchestrator; do
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
for f in $REMOTE_DIR/infrastructure/systemd/*.service $REMOTE_DIR/infrastructure/systemd/*.timer; do
    svc_name=\$(basename "\$f")
    sudo cp "\$f" "/etc/systemd/system/\$svc_name"
done
# Units that are no longer in the repo but older setups installed. A stale
# content-agent-mcp.service held port 8005 (without signed-request auth) and
# left content-agent.service in a restart loop that filled the disk.
for stale in content-agent-mcp.service; do
    if [[ -f "/etc/systemd/system/\$stale" ]]; then
        sudo systemctl disable --now "\$stale" || true
        sudo rm -f "/etc/systemd/system/\$stale"
        echo "  removed stale unit \$stale"
    fi
done
sudo systemctl daemon-reload

# Keep logs bounded: rotate /var/log/ads-mcp and cap the systemd journal.
sudo install -m 644 -o root -g root $REMOTE_DIR/infrastructure/logrotate/ads-mcp /etc/logrotate.d/ads-mcp
sudo mkdir -p /etc/systemd/journald.conf.d
sudo install -m 644 -o root -g root $REMOTE_DIR/infrastructure/journald/ads-mcp.conf /etc/systemd/journald.conf.d/ads-mcp.conf
sudo systemctl restart systemd-journald
ENDSSH

echo "==> Restarting services"
$SSH "$REMOTE_HOST" bash -s <<ENDSSH
set -e
for svc in google-ads-mcp meta-ads-mcp analytics-mcp search-console-mcp content-agent gbp-mcp orchestrator-mcp; do
    sudo systemctl restart "\$svc" && echo "  restarted \$svc" || echo "  WARNING: failed to restart \$svc"
done
# Scheduled social posts (Instagram) run from a 30-minute timer, not a daemon.
sudo systemctl enable --now ads-mcp-social.timer && echo "  enabled ads-mcp-social.timer"
ENDSSH

if [[ "$UPDATE_NGINX" == "true" ]]; then
    echo "==> Reloading Nginx from repo template"
    $SSH "$REMOTE_HOST" bash -s <<ENDSSH
sudo cp $REMOTE_DIR/infrastructure/nginx.conf /etc/nginx/sites-available/ads-mcp
sudo nginx -t && sudo systemctl reload nginx
ENDSSH
else
    echo "==> Skipping nginx template overwrite (preserves certbot-managed HTTPS config)"
fi

echo ""
echo "==> Verifying health endpoints"
sleep 2
BASE="https://mcp.rdtechbridge.com"
for path in google-ads meta-ads analytics search-console content gbp orchestrator; do
    status=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/$path/health" 2>/dev/null || echo "ERR")
    echo "  $path/health -> $status"
done

echo ""
echo "Deploy complete."
