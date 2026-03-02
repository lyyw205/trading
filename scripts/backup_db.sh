#!/bin/bash
# Database backup script with rotation and optional S3 upload.
# Usage: ./scripts/backup_db.sh
# Requires: DATABASE_URL environment variable
set -euo pipefail

DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="${BACKUP_DIR:-/backups/trading}"
RETENTION_DAYS="${RETENTION_DAYS:-7}"

mkdir -p "$BACKUP_DIR"

echo "[$(date)] Starting backup..."

pg_dump "$DATABASE_URL" \
  --format=custom \
  --compress=9 \
  --file="$BACKUP_DIR/trading_${DATE}.dump"

echo "[$(date)] Backup saved to $BACKUP_DIR/trading_${DATE}.dump"

# Rotate old backups
DELETED=$(find "$BACKUP_DIR" -name "*.dump" -mtime +"$RETENTION_DAYS" -delete -print | wc -l)
if [ "$DELETED" -gt 0 ]; then
  echo "[$(date)] Cleaned up $DELETED old backups (>${RETENTION_DAYS} days)"
fi

# Optional S3 upload
if [ -n "${S3_BUCKET:-}" ]; then
  aws s3 cp "$BACKUP_DIR/trading_${DATE}.dump" "s3://${S3_BUCKET}/trading_${DATE}.dump"
  echo "[$(date)] Uploaded to s3://${S3_BUCKET}/"
fi

echo "[$(date)] Backup complete"
