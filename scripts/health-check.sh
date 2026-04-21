#!/usr/bin/env bash
set -euo pipefail

BASE_URL=${BASE_URL:-https://mcp.yourdomain.com}

for path in google-ads meta-ads analytics search-console content; do
  echo "Checking ${path}"
  curl -fsS "${BASE_URL}/${path}/health"
  echo
done
