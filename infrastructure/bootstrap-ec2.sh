#!/usr/bin/env bash
# bootstrap-ec2.sh — First-time setup for the rd-mcp EC2 instance.
# Run once after launch. Safe to re-run (idempotent).
#
# Usage (from your local machine):
#   ssh -i ~/.ssh/rd-mcp-key.pem ubuntu@54.81.33.167 'bash -s' < infrastructure/bootstrap-ec2.sh

set -euo pipefail

REPO_DIR=/opt/ads-mcp
VENV_DIR="$REPO_DIR/.venv"
LOG_DIR=/var/log/ads-mcp
SERVICE_USER=ubuntu

echo "==> Updating packages"
sudo apt-get update -q
sudo apt-get install -y -q \
    python3 python3-venv python3-pip \
    nginx certbot python3-certbot-nginx \
    rsync git

echo "==> Creating directories"
sudo mkdir -p "$REPO_DIR"
sudo mkdir -p "$LOG_DIR"
sudo chown "$SERVICE_USER:$SERVICE_USER" "$REPO_DIR" "$LOG_DIR"

echo "==> Creating Python virtualenv at $VENV_DIR"
if [[ ! -d "$VENV_DIR" ]]; then
    python3 -m venv "$VENV_DIR"
fi

echo "==> Installing Python dependencies (all services)"
"$VENV_DIR/bin/pip" install --quiet --upgrade pip

for svc in google-ads meta-ads analytics search-console content-agent; do
    req="$REPO_DIR/servers/$svc/requirements.txt"
    if [[ -f "$req" ]]; then
        echo "    installing $svc requirements..."
        "$VENV_DIR/bin/pip" install --quiet -r "$req"
    fi
done

# shared deps
"$VENV_DIR/bin/pip" install --quiet fastapi uvicorn boto3 fastmcp

echo "==> Installing systemd service files"
for f in "$REPO_DIR"/infrastructure/systemd/*.service; do
    svc_name=$(basename "$f")
    sudo cp "$f" "/etc/systemd/system/$svc_name"
done
sudo systemctl daemon-reload

echo "==> Enabling services (not starting yet — run deploy.sh first)"
for svc in google-ads-mcp meta-ads-mcp analytics-mcp search-console-mcp content-agent; do
    sudo systemctl enable "$svc" 2>/dev/null || true
done

echo "==> Configuring Nginx"
sudo cp "$REPO_DIR/infrastructure/nginx.conf" /etc/nginx/sites-available/ads-mcp
sudo ln -sf /etc/nginx/sites-available/ads-mcp /etc/nginx/sites-enabled/ads-mcp
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx

echo ""
echo "Bootstrap complete."
echo ""
echo "Next steps:"
echo "  1. Run: bash scripts/deploy.sh   — to sync code and start services"
echo "  2. Verify: curl http://54.81.33.167/google-ads/health"
echo "  3. Once DNS is pointed: sudo certbot --nginx -d mcp.rctechbridge.com"
