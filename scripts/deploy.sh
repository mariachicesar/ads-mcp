#!/usr/bin/env bash
set -euo pipefail

REMOTE_HOST=${REMOTE_HOST:-ubuntu@your-ec2-host}
REMOTE_DIR=${REMOTE_DIR:-/opt/ads-mcp}

echo "Deploying ads-mcp to ${REMOTE_HOST}:${REMOTE_DIR}"
rsync -av --delete ./ "${REMOTE_HOST}:${REMOTE_DIR}/"
echo "Restart services manually or through your deployment pipeline after dependency install."
