#!/usr/bin/env bash
# configure-media-bucket.sh — lock down and wire up the existing media bucket
# used by the scheduled social posts queue (shared/media_queue.py).
#
# Idempotent. Run from the repo root on a machine with admin AWS credentials:
#   bash infrastructure/configure-media-bucket.sh
#
# Override defaults:
#   BUCKET=my-bucket ROLE=my-role bash infrastructure/configure-media-bucket.sh

set -euo pipefail
export MSYS_NO_PATHCONV=1  # Git Bash on Windows: keep paths/ARNs as-is

BUCKET=${BUCKET:-amz-s3-videos-ads-mcp-009959434008-us-east-1-an}
ROLE=${ROLE:-rd-mcp-ec2-role}
REGION=${REGION:-us-east-1}

echo "==> Checking bucket $BUCKET exists"
aws s3api head-bucket --bucket "$BUCKET" --region "$REGION"

echo "==> Blocking all public access"
aws s3api put-public-access-block --bucket "$BUCKET" --region "$REGION" \
    --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

echo "==> Default encryption (SSE-S3)"
aws s3api put-bucket-encryption --bucket "$BUCKET" --region "$REGION" \
    --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'

echo "==> Lifecycle: expire videos and finished queue entries after 90 days"
aws s3api put-bucket-lifecycle-configuration --bucket "$BUCKET" --region "$REGION" --lifecycle-configuration '{
  "Rules": [
    {"ID": "expire-videos",          "Status": "Enabled", "Filter": {"Prefix": "videos/"},          "Expiration": {"Days": 90}},
    {"ID": "expire-queue-done",      "Status": "Enabled", "Filter": {"Prefix": "queue/done/"},      "Expiration": {"Days": 90}},
    {"ID": "expire-queue-failed",    "Status": "Enabled", "Filter": {"Prefix": "queue/failed/"},    "Expiration": {"Days": 90}},
    {"ID": "expire-queue-cancelled", "Status": "Enabled", "Filter": {"Prefix": "queue/cancelled/"}, "Expiration": {"Days": 90}},
    {"ID": "abort-incomplete-uploads", "Status": "Enabled", "Filter": {"Prefix": ""}, "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 1}}
  ]
}'

echo "==> Granting $ROLE access to the bucket"
aws iam put-role-policy --role-name "$ROLE" --policy-name ads-mcp-media-s3 --policy-document "{
  \"Version\": \"2012-10-17\",
  \"Statement\": [
    {\"Effect\": \"Allow\", \"Action\": [\"s3:GetObject\", \"s3:PutObject\", \"s3:DeleteObject\"], \"Resource\": \"arn:aws:s3:::$BUCKET/*\"},
    {\"Effect\": \"Allow\", \"Action\": [\"s3:ListBucket\"], \"Resource\": \"arn:aws:s3:::$BUCKET\"}
  ]
}"

echo "Done. Set ADS_MCP_MEDIA_BUCKET=$BUCKET for the meta-ads service and the social runner."
