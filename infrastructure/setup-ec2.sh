#!/usr/bin/env bash
set -euo pipefail

sudo apt update
sudo apt install -y python3.12 python3.12-venv python3-pip nginx certbot python3-certbot-nginx

sudo mkdir -p /opt/ads-mcp
sudo mkdir -p /var/log/ads-mcp

cat <<'EOF'
Next steps:
1. Copy repo into /opt/ads-mcp
2. Create virtualenv(s)
3. Install requirements for each service
4. Place nginx.conf into /etc/nginx/sites-available/ads-mcp
5. Enable site and request certbot certificate
6. Install systemd service files
7. Verify /health endpoints for all services
EOF
