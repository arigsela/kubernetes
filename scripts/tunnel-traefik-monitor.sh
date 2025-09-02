#!/usr/bin/env bash
# tunnel-traefik-monitor.sh - Advanced monitoring for Cloudflare Tunnel → Traefik → Apps

set -euo pipefail

LOG_DIR="/tmp/tunnel-traefik-monitoring-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$LOG_DIR"

echo "🔍 Starting Cloudflare Tunnel → Traefik → Apps monitoring..."
echo "📁 Logs directory: $LOG_DIR"

# Function to run multiple tests in parallel
run_parallel_tests() {
    local test_name=$1
    local endpoint=$2
    local count=$3
    
    echo "🧪 Running $test_name ($count requests to $endpoint)..."
    
    # Parallel requests
    for i in $(seq 1 $count); do
        {
            response=$(curl -s -o /dev/null -w "%{http_code},%{time_total},%{time_namelookup},%{time_connect},%{time_starttransfer}" \
                --connect-timeout 10 --max-time 15 "$endpoint" 2>/dev/null || echo "000,timeout,timeout,timeout,timeout")
            echo "$(date '+%Y-%m-%d %H:%M:%S'),$i,$response" >> "$LOG_DIR/$(echo "$test_name" | tr '[:upper:]' '[:lower:]').csv"
        } &
    done
    wait
    
    # Analyze results
    if [ -f "$LOG_DIR/$(echo "$test_name" | tr '[:upper:]' '[:lower:]').csv" ]; then
        echo "📊 $test_name Results:"
        awk -F, '{print $3}' "$LOG_DIR/$(echo "$test_name" | tr '[:upper:]' '[:lower:]').csv" | sort | uniq -c | while read count code; do
            percentage=$(( count * 100 / $count ))
            echo "  HTTP $code: $count requests (${percentage}%)"
        done
        
        # Calculate timing stats for successful requests
        awk -F, '$3!="000" && $3!="503" {sum+=$4; count++} END {
            if (count > 0) printf "  ⚡ Avg response time: %.3fs\n", sum/count
        }' "$LOG_DIR/$(echo "$test_name" | tr '[:upper:]' '[:lower:]').csv"
    fi
    echo ""
}

# Function to monitor logs in real-time during tests
monitor_logs() {
    echo "📝 Starting log monitoring..."
    
    # Monitor Traefik logs
    kubectl logs -f -n kube-system deployment/traefik --prefix=true > "$LOG_DIR/traefik-monitor.log" 2>&1 &
    TRAEFIK_PID=$!
    
    # Monitor Cloudflare tunnel logs  
    kubectl logs -f -n cloudflare -l app=cloudflared --prefix=true > "$LOG_DIR/tunnel-monitor.log" 2>&1 &
    TUNNEL_PID=$!
    
    # Monitor backend logs
    kubectl logs -f -n chores-tracker -l app=chores-tracker --prefix=true > "$LOG_DIR/backend-monitor.log" 2>&1 &
    BACKEND_PID=$!
    
    echo "📝 Log monitoring started (PIDs: Traefik=$TRAEFIK_PID, Tunnel=$TUNNEL_PID, Backend=$BACKEND_PID)"
    
    # Return PIDs so we can kill them later
    echo "$TRAEFIK_PID $TUNNEL_PID $BACKEND_PID"
}

# Function to stop log monitoring
stop_monitoring() {
    local pids=$1
    echo "🛑 Stopping log monitoring..."
    kill $pids 2>/dev/null || true
    wait 2>/dev/null || true
}

# Function to collect system state
collect_system_state() {
    echo "📋 Collecting system state..."
    
    {
        echo "=== TIMESTAMP ==="
        date
        echo ""
        
        echo "=== CLOUDFLARE TUNNEL PODS ==="
        kubectl get pods -n cloudflare -o wide
        echo ""
        
        echo "=== TRAEFIK PODS ==="
        kubectl get pods -n kube-system -l app.kubernetes.io/name=traefik -o wide
        echo ""
        
        echo "=== BACKEND PODS ==="  
        kubectl get pods -n chores-tracker -o wide
        echo ""
        
        echo "=== FRONTEND PODS ==="
        kubectl get pods -n chores-tracker-frontend -o wide
        echo ""
        
        echo "=== TRAEFIK SERVICE ==="
        kubectl get svc -n kube-system traefik -o wide
        echo ""
        
        echo "=== TUNNEL METRICS (if available) ==="
        if kubectl port-forward -n cloudflare svc/cloudflared 2001:2000 --address='127.0.0.1' >/dev/null 2>&1 &
        then
            PF_PID=$!
            sleep 2
            curl -s http://localhost:2001/metrics | grep -E "(cloudflared_tunnel|cloudflared_proxy|error|5xx)" || echo "No tunnel metrics available"
            kill $PF_PID 2>/dev/null || true
        else
            echo "Unable to access tunnel metrics"
        fi
        echo ""
        
    } > "$LOG_DIR/system-state.log"
}

# Function to analyze patterns
analyze_patterns() {
    echo "📈 Analyzing error patterns..."
    
    {
        echo "=== ERROR PATTERN ANALYSIS ==="
        echo "Timestamp: $(date)"
        echo ""
        
        for csv_file in "$LOG_DIR"/*.csv; do
            if [ -f "$csv_file" ]; then
                filename=$(basename "$csv_file" .csv)
                echo "=== $filename ANALYSIS ==="
                
                # Time-based pattern analysis
                echo "Errors over time:"
                awk -F, '$3=="503" {print $1}' "$csv_file" | while read timestamp; do
                    echo "  503 at: $timestamp"
                done
                
                # Timing analysis for failed vs successful requests
                echo ""
                echo "Timing comparison:"
                echo "Successful requests (non-503):"
                awk -F, '$3!="503" && $3!="000" {sum+=$4; count++} END {
                    if (count > 0) printf "  Count: %d, Avg time: %.3fs\n", count, sum/count
                }' "$csv_file"
                
                echo "Failed requests (503):"
                awk -F, '$3=="503" {sum+=$4; count++} END {
                    if (count > 0) printf "  Count: %d, Avg time: %.3fs\n", count, sum/count
                }' "$csv_file"
                echo ""
            fi
        done
        
    } > "$LOG_DIR/pattern-analysis.log"
    
    cat "$LOG_DIR/pattern-analysis.log"
}

# Main execution
main() {
    echo "🚀 Starting comprehensive tunnel-traefik monitoring..."
    
    # Collect initial system state
    collect_system_state
    
    # Start log monitoring
    log_pids=$(monitor_logs)
    
    # Wait a moment for log collection to start
    sleep 2
    
    # Run tests
    run_parallel_tests "API_Endpoint_Test" "https://chores.arigsela.com/api/v1/families/context" 30
    sleep 2
    
    run_parallel_tests "Health_Endpoint_Test" "https://chores.arigsela.com/health" 20
    sleep 2
    
    run_parallel_tests "Root_Endpoint_Test" "https://chores.arigsela.com/" 15
    
    # Wait a moment for logs to capture everything
    sleep 5
    
    # Stop log monitoring
    stop_monitoring "$log_pids"
    
    # Analyze patterns
    analyze_patterns
    
    echo "✅ Monitoring complete!"
    echo "📁 All logs and analysis saved to: $LOG_DIR"
    echo ""
    echo "🔍 Quick summary files:"
    echo "  - System state: $LOG_DIR/system-state.log"
    echo "  - Pattern analysis: $LOG_DIR/pattern-analysis.log"
    echo "  - Traefik logs: $LOG_DIR/traefik-monitor.log"
    echo "  - Tunnel logs: $LOG_DIR/tunnel-monitor.log" 
    echo "  - Backend logs: $LOG_DIR/backend-monitor.log"
    echo "  - Test results: $LOG_DIR/*.csv"
}

# Handle cleanup on exit
cleanup() {
    echo "🧹 Cleaning up..."
    # Kill any remaining background processes
    jobs -p | xargs -r kill 2>/dev/null || true
}

trap cleanup EXIT

# Run main function
main "$@"