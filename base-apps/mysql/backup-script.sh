#!/bin/bash
set -euo pipefail

# MySQL Backup Script for S3
# This script creates a compressed MySQL backup and uploads it to S3
# with retention management

echo "=========================================="
echo "MySQL Backup Script Started"
echo "Time: $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "=========================================="

# Configuration from environment variables
MYSQL_HOST="${MYSQL_HOST:-mysql.mysql.svc.cluster.local}"
MYSQL_PORT="${MYSQL_PORT:-3306}"
MYSQL_DATABASE="${MYSQL_DATABASE:-}"
MYSQL_USER="${MYSQL_USER:-root}"
MYSQL_PASSWORD="${MYSQL_PASSWORD}"
S3_BUCKET="${S3_BUCKET}"
AWS_REGION="${AWS_REGION:-us-east-1}"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-30}"

# Validate required environment variables
if [ -z "${MYSQL_PASSWORD}" ]; then
  echo "ERROR: MYSQL_PASSWORD environment variable is not set"
  exit 1
fi

if [ -z "${S3_BUCKET}" ]; then
  echo "ERROR: S3_BUCKET environment variable is not set"
  exit 1
fi

# Generate backup filename with timestamp
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="mysql_backup_${TIMESTAMP}.sql.gz"
TEMP_FILE="/tmp/${BACKUP_FILE}"

echo "Configuration:"
echo "  MySQL Host: ${MYSQL_HOST}:${MYSQL_PORT}"
echo "  MySQL User: ${MYSQL_USER}"
echo "  S3 Bucket: ${S3_BUCKET}"
echo "  AWS Region: ${AWS_REGION}"
echo "  Retention Days: ${BACKUP_RETENTION_DAYS}"
echo "  Backup File: ${BACKUP_FILE}"
echo ""

# Test MySQL connection
echo "Step 1: Testing MySQL connection..."
if mysql -h"${MYSQL_HOST}" -P"${MYSQL_PORT}" -u"${MYSQL_USER}" -p"${MYSQL_PASSWORD}" -e "SELECT 1;" > /dev/null 2>&1; then
  echo "✓ MySQL connection successful"
else
  echo "✗ ERROR: Cannot connect to MySQL at ${MYSQL_HOST}:${MYSQL_PORT}"
  echo "  Please check MySQL is running and credentials are correct"
  exit 1
fi

# Get database size for logging
echo ""
echo "Step 2: Getting database information..."
DB_SIZE=$(mysql -h"${MYSQL_HOST}" -P"${MYSQL_PORT}" -u"${MYSQL_USER}" -p"${MYSQL_PASSWORD}" -e "SELECT ROUND(SUM(data_length + index_length) / 1024 / 1024, 2) AS 'Size (MB)' FROM information_schema.TABLES;" -N 2>/dev/null || echo "Unknown")
echo "  Database Size: ${DB_SIZE} MB"

# Create backup
echo ""
echo "Step 3: Creating MySQL backup..."
echo "  This may take a few minutes depending on database size..."

if mysqldump -h"${MYSQL_HOST}" -P"${MYSQL_PORT}" -u"${MYSQL_USER}" -p"${MYSQL_PASSWORD}" \
  --single-transaction \
  --routines \
  --triggers \
  --events \
  --all-databases \
  --add-drop-database 2>/dev/null | gzip > "${TEMP_FILE}"; then
  echo "✓ Backup created successfully"
else
  echo "✗ ERROR: mysqldump failed"
  rm -f "${TEMP_FILE}"
  exit 1
fi

# Verify backup file was created and has content
if [ ! -s "${TEMP_FILE}" ]; then
  echo "✗ ERROR: Backup file is empty or does not exist"
  rm -f "${TEMP_FILE}"
  exit 1
fi

BACKUP_SIZE=$(du -h "${TEMP_FILE}" | cut -f1)
BACKUP_SIZE_BYTES=$(stat -f%z "${TEMP_FILE}" 2>/dev/null || stat -c%s "${TEMP_FILE}" 2>/dev/null)
COMPRESSION_RATIO=$(echo "scale=2; (1 - ${BACKUP_SIZE_BYTES} / (${DB_SIZE} * 1024 * 1024)) * 100" | bc 2>/dev/null || echo "N/A")

echo "  Backup Size: ${BACKUP_SIZE}"
echo "  Compression Ratio: ${COMPRESSION_RATIO}%"

# Calculate MD5 checksum for verification
echo ""
echo "Step 4: Calculating backup checksum..."
CHECKSUM=$(md5sum "${TEMP_FILE}" | awk '{print $1}')
echo "  MD5 Checksum: ${CHECKSUM}"

# Upload to S3
echo ""
echo "Step 5: Uploading backup to S3..."
echo "  Destination: s3://${S3_BUCKET}/${BACKUP_FILE}"

if aws s3 cp "${TEMP_FILE}" "s3://${S3_BUCKET}/${BACKUP_FILE}" --region "${AWS_REGION}" 2>&1; then
  echo "✓ Backup uploaded to S3 successfully"
else
  echo "✗ ERROR: Failed to upload backup to S3"
  rm -f "${TEMP_FILE}"
  exit 1
fi

# Verify upload by checking file exists in S3
echo ""
echo "Step 6: Verifying S3 upload..."
if aws s3 ls "s3://${S3_BUCKET}/${BACKUP_FILE}" --region "${AWS_REGION}" > /dev/null 2>&1; then
  S3_SIZE=$(aws s3 ls "s3://${S3_BUCKET}/${BACKUP_FILE}" --region "${AWS_REGION}" | awk '{print $3}')
  echo "✓ Backup verified in S3"
  echo "  S3 File Size: ${S3_SIZE} bytes"
  echo "  Local File Size: ${BACKUP_SIZE_BYTES} bytes"

  if [ "${S3_SIZE}" -eq "${BACKUP_SIZE_BYTES}" ]; then
    echo "✓ File sizes match - upload integrity confirmed"
  else
    echo "⚠ WARNING: File sizes don't match - upload may be corrupted"
  fi
else
  echo "✗ WARNING: Cannot verify backup in S3"
fi

# Cleanup local file
echo ""
echo "Step 7: Cleaning up local files..."
rm -f "${TEMP_FILE}"
echo "✓ Local backup file removed"

# Cleanup old backups (retention policy)
echo ""
echo "Step 8: Applying retention policy (${BACKUP_RETENTION_DAYS} days)..."

# Calculate cutoff date
if date -v-${BACKUP_RETENTION_DAYS}d +%Y%m%d > /dev/null 2>&1; then
  # macOS/BSD date
  CUTOFF_DATE=$(date -v-${BACKUP_RETENTION_DAYS}d +%Y%m%d)
else
  # GNU date (Linux)
  CUTOFF_DATE=$(date -d "${BACKUP_RETENTION_DAYS} days ago" +%Y%m%d)
fi

echo "  Deleting backups older than: $(date -d ${CUTOFF_DATE} +%Y-%m-%d 2>/dev/null || date -j -f %Y%m%d ${CUTOFF_DATE} +%Y-%m-%d)"

DELETED_COUNT=0
aws s3 ls "s3://${S3_BUCKET}/" --region "${AWS_REGION}" | grep "mysql_backup_" | while read -r line; do
  FILE_NAME=$(echo "$line" | awk '{print $4}')

  # Extract date from filename (format: mysql_backup_YYYYMMDD_HHMMSS.sql.gz)
  if [[ $FILE_NAME =~ mysql_backup_([0-9]{8})_[0-9]{6}\.sql\.gz ]]; then
    FILE_DATE="${BASH_REMATCH[1]}"

    if [[ "$FILE_DATE" < "$CUTOFF_DATE" ]]; then
      echo "  Deleting old backup: $FILE_NAME (${FILE_DATE})"
      aws s3 rm "s3://${S3_BUCKET}/${FILE_NAME}" --region "${AWS_REGION}"
      DELETED_COUNT=$((DELETED_COUNT + 1))
    fi
  fi
done

if [ $DELETED_COUNT -eq 0 ]; then
  echo "  No old backups to delete"
else
  echo "✓ Deleted ${DELETED_COUNT} old backup(s)"
fi

# List current backups in S3
echo ""
echo "Step 9: Current backups in S3:"
aws s3 ls "s3://${S3_BUCKET}/" --region "${AWS_REGION}" | grep "mysql_backup_" | tail -10 || echo "  No backups found"

# Final summary
echo ""
echo "=========================================="
echo "Backup Completed Successfully!"
echo "=========================================="
echo "Summary:"
echo "  Backup File: ${BACKUP_FILE}"
echo "  Size: ${BACKUP_SIZE}"
echo "  Checksum: ${CHECKSUM}"
echo "  S3 Location: s3://${S3_BUCKET}/${BACKUP_FILE}"
echo "  Completed: $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "=========================================="

exit 0
