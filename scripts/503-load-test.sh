#!/usr/bin/env bash
# 503-load-test.sh - Systematic load testing to reproduce 503 errors

set -euo pipefail

ENDPOINT="https://chores.arigsela.com/api/v1/families/context"
HEALTH_ENDPOINT="https://chores.arigsela.com/health"
RESULTS_FILE="/tmp/503-test-results-$(date +%Y%m%d-%H%M%S).txt"

echo "Starting 503 error reproduction testing..."
echo "Results will be logged to: $RESULTS_FILE"
echo "====================================="

# Test 1: Sequential requests
echo "[$(date)] Test 1: Sequential requests (50 requests)" | tee -a "$RESULTS_FILE"
for i in {1..50}; do
    response=$(curl -s -o /dev/null -w "%{http_code}" "$ENDPOINT")
    echo "Request $i: $response" | tee -a "$RESULTS_FILE"
    [ "$response" = "503" ] && echo "  *** 503 ERROR DETECTED ***" | tee -a "$RESULTS_FILE"
    sleep 0.2
done

# Count 503s in test 1
test1_503s=$(grep -c "503" "$RESULTS_FILE" || echo "0")
echo "Test 1 Summary: $test1_503s/50 requests returned 503 ($(( test1_503s * 100 / 50 ))%)" | tee -a "$RESULTS_FILE"
echo "" | tee -a "$RESULTS_FILE"

# Test 2: Concurrent requests
echo "[$(date)] Test 2: Concurrent requests (20 parallel)" | tee -a "$RESULTS_FILE"
for i in {1..20}; do
    curl -s -o /dev/null -w "Concurrent $i: %{http_code}\n" "$ENDPOINT" >> "$RESULTS_FILE" &
done
wait

# Count 503s in test 2
test2_503s=$(tail -20 "$RESULTS_FILE" | grep -c "503" || echo "0")
echo "Test 2 Summary: $test2_503s/20 concurrent requests returned 503 ($(( test2_503s * 100 / 20 ))%)" | tee -a "$RESULTS_FILE"
echo "" | tee -a "$RESULTS_FILE"

# Test 3: Health endpoint testing
echo "[$(date)] Test 3: Health endpoint testing (30 requests)" | tee -a "$RESULTS_FILE"
for i in {1..30}; do
    response=$(curl -s -o /dev/null -w "%{http_code}" "$HEALTH_ENDPOINT")
    echo "Health $i: $response" | tee -a "$RESULTS_FILE"
    [ "$response" = "503" ] && echo "  *** 503 ERROR ON HEALTH ENDPOINT ***" | tee -a "$RESULTS_FILE"
    sleep 0.1
done

# Count 503s in test 3
test3_503s=$(tail -30 "$RESULTS_FILE" | grep -c "503" || echo "0")
echo "Test 3 Summary: $test3_503s/30 health requests returned 503 ($(( test3_503s * 100 / 30 ))%)" | tee -a "$RESULTS_FILE"
echo "" | tee -a "$RESULTS_FILE"

# Overall summary
total_503s=$(grep -c "503" "$RESULTS_FILE" || echo "0")
echo "=== FINAL SUMMARY ===" | tee -a "$RESULTS_FILE"
echo "Total 503 errors: $total_503s out of 100 requests" | tee -a "$RESULTS_FILE"
echo "Overall error rate: $(( total_503s * 100 / 100 ))%" | tee -a "$RESULTS_FILE"
echo "Results saved to: $RESULTS_FILE"

# If high error rate, recommend immediate investigation
if [ "$total_503s" -gt 10 ]; then
    echo "" | tee -a "$RESULTS_FILE"
    echo "⚠️  HIGH ERROR RATE DETECTED! ($total_503s/100 = $(( total_503s ))%)" | tee -a "$RESULTS_FILE"
    echo "Immediate investigation recommended." | tee -a "$RESULTS_FILE"
fi