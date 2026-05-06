#!/usr/bin/env bash
set -euo pipefail

BASE_URL=${BASE_URL:-https://mcp.rctechbridge.com}

for path in google-ads meta-ads analytics search-console content gbp orchestrator; do
  echo "Checking ${path}"
  curl -fsS "${BASE_URL}/${path}/health"
  echo
done
