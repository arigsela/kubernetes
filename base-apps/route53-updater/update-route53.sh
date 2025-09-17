#!/bin/bash
set -euo pipefail

echo "Starting Route 53 IP update at $(date)"

# Debug: Check if AWS credentials are available
echo "Checking AWS credentials..."
if [[ -z "${AWS_ACCESS_KEY_ID:-}" ]]; then
  echo "ERROR: AWS_ACCESS_KEY_ID is not set"
  exit 1
fi
if [[ -z "${AWS_SECRET_ACCESS_KEY:-}" ]]; then
  echo "ERROR: AWS_SECRET_ACCESS_KEY is not set"
  exit 1
fi
echo "AWS credentials are available"

# Debug: Check AWS configuration
echo "AWS Region: ${AWS_REGION:-not set}"
echo "Hosted Zone ID: ${HOSTED_ZONE_ID:-not set}"
echo "Record Names: ${RECORD_NAMES:-not set}"

# Check AWS CLI version
echo "AWS CLI version:"
aws --version

# Test AWS connectivity
echo "Testing AWS connectivity..."
aws sts get-caller-identity

# Get current public IP
CURRENT_IP=$(curl -s --max-time 10 "${IP_DETECTION_URL}" || echo "")
if [[ -z "$CURRENT_IP" ]]; then
  echo "ERROR: Failed to get current public IP"
  exit 1
fi

echo "Current public IP: $CURRENT_IP"

# Convert comma-separated record names to array
IFS=',' read -ra RECORD_ARRAY <<< "$RECORD_NAMES"

# Track if any updates were made
UPDATES_MADE=false
ALL_CHANGE_IDS=()

# Process each record
for RECORD_NAME in "${RECORD_ARRAY[@]}"; do
  # Trim whitespace
  RECORD_NAME=$(echo "$RECORD_NAME" | xargs)

  echo ""
  echo "=== Processing record: $RECORD_NAME ==="

  # Get existing DNS record
  echo "Querying existing DNS record for ${RECORD_NAME}..."
  echo "Command: aws route53 list-resource-record-sets --hosted-zone-id $HOSTED_ZONE_ID --query \"ResourceRecordSets[?Name=='${RECORD_NAME}.' && Type=='${RECORD_TYPE}'].ResourceRecords[0].Value\" --output text"

  # Use a shorter timeout and better error handling
  EXISTING_IP=$(timeout 30 aws route53 list-resource-record-sets \
    --hosted-zone-id "$HOSTED_ZONE_ID" \
    --query "ResourceRecordSets[?Name=='${RECORD_NAME}.' && Type=='${RECORD_TYPE}'].ResourceRecords[0].Value" \
    --output text 2>/dev/null)

  EXIT_CODE=$?
  echo "AWS CLI exit code: $EXIT_CODE"

  if [ $EXIT_CODE -eq 124 ]; then
    echo "ERROR: AWS CLI command timed out after 30 seconds for $RECORD_NAME"
    continue
  elif [ $EXIT_CODE -ne 0 ]; then
    echo "ERROR: AWS CLI command failed with exit code $EXIT_CODE for $RECORD_NAME"
    EXISTING_IP="None"
  fi

  echo "AWS CLI command completed. Result: $EXISTING_IP"
  echo "Existing DNS record IP: $EXISTING_IP"

  # Compare IPs
  if [[ "$CURRENT_IP" == "$EXISTING_IP" ]]; then
    echo "IP address unchanged for $RECORD_NAME. No update needed."
    continue
  fi

  echo "IP address changed from $EXISTING_IP to $CURRENT_IP for $RECORD_NAME. Updating Route 53..."

  # Create change batch JSON for this record
  cat > /tmp/change-batch-${RECORD_NAME}.json << EOF
{
  "Comment": "Automated public IP update for ${RECORD_NAME} - $(date)",
  "Changes": [
    {
      "Action": "UPSERT",
      "ResourceRecordSet": {
        "Name": "${RECORD_NAME}",
        "Type": "${RECORD_TYPE}",
        "TTL": ${TTL},
        "ResourceRecords": [
          {
            "Value": "${CURRENT_IP}"
          }
        ]
      }
    }
  ]
}
EOF

  # Update Route 53 record
  CHANGE_ID=$(aws route53 change-resource-record-sets \
    --hosted-zone-id "$HOSTED_ZONE_ID" \
    --change-batch file:///tmp/change-batch-${RECORD_NAME}.json \
    --query 'ChangeInfo.Id' \
    --output text)

  echo "Route 53 change submitted for $RECORD_NAME: $CHANGE_ID"
  ALL_CHANGE_IDS+=("$CHANGE_ID")
  UPDATES_MADE=true
done

# Wait for all changes to propagate
if [ "$UPDATES_MADE" = true ]; then
  echo ""
  echo "=== Waiting for DNS changes to propagate ==="
  for CHANGE_ID in "${ALL_CHANGE_IDS[@]}"; do
    echo "Waiting for change $CHANGE_ID to propagate..."
    aws route53 wait resource-record-sets-changed --id "$CHANGE_ID"
    echo "Change $CHANGE_ID propagated successfully!"
  done

  echo ""
  echo "=== All Route 53 records updated successfully! ==="
  for RECORD_NAME in "${RECORD_ARRAY[@]}"; do
    RECORD_NAME=$(echo "$RECORD_NAME" | xargs)
    echo "DNS record ${RECORD_NAME} now points to ${CURRENT_IP}"
  done
else
  echo ""
  echo "=== No DNS updates were needed ==="
  echo "All records already point to the current IP: $CURRENT_IP"
fi