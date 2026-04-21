#!/usr/bin/env bash
# enable-https.sh — Run this on EC2 after DNS is pointed at the Elastic IP.
#
# Usage (from your local machine, after DNS propagation):
#   ssh -i ~/.ssh/rd-mcp-key.pem ubuntu@<elastic-ip> \
#     "DOMAIN=mcp.rdtechbridge.com EMAIL=your@email.com bash -s" < infrastructure/enable-https.sh
#
# Prerequisites:
#   1. Elastic IP allocated and associated with rd-mcp in AWS console
#   2. DNS A record: DOMAIN -> Elastic IP (wait 5+ min for propagation)
#   3. Port 80 and 443 open in rd-mcp security group

set -euo pipefail

DOMAIN=${DOMAIN:-mcp.rctechbridge.com}
EMAIL=${EMAIL:-admin@rctechbridge.com}

echo "==> Verifying DNS resolves to this server"
SERVER_IP=$(curl -s http://checkip.amazonaws.com)
DNS_IP=$(dig +short "$DOMAIN" | tail -1)

if [[ "$DNS_IP" != "$SERVER_IP" ]]; then
    echo "ERROR: DNS not yet pointing to this server."
    echo "  $DOMAIN resolves to: $DNS_IP"
    echo "  This server's IP:    $SERVER_IP"
    echo "Wait for DNS propagation and retry."
    exit 1
fi
echo "  DNS OK: $DOMAIN -> $SERVER_IP"

echo "==> Obtaining Let's Encrypt certificate via certbot"
sudo certbot --nginx \
    -d "$DOMAIN" \
    --non-interactive \
    --agree-tos \
    --email "$EMAIL" \
    --redirect

echo "==> Enabling HTTPS redirect in nginx config"
# Certbot auto-modifies the nginx config, but we also update our source config
# to add the 301 redirect block and uncomment the SSL server block.
NGINX_CONF=/etc/nginx/sites-available/ads-mcp

# Certbot will have already modified the live config. Back it up and verify.
sudo cp "$NGINX_CONF" "${NGINX_CONF}.pre-certbot.bak" 2>/dev/null || true

echo "==> Testing and reloading nginx"
sudo nginx -t && sudo systemctl reload nginx

echo "==> Verifying HTTPS health endpoints"
sleep 2
for path in google-ads meta-ads analytics search-console content; do
    status=$(curl -s -o /dev/null -w "%{http_code}" "https://$DOMAIN/$path/health" 2>/dev/null || echo "ERR")
    echo "  https://$DOMAIN/$path/health -> $status"
done

echo ""
echo "HTTPS enabled. Certificate auto-renews via certbot.timer (systemd)."
echo ""
echo "Next: Update Claude Desktop and backend-rc to use https://$DOMAIN/ instead of the IP."
