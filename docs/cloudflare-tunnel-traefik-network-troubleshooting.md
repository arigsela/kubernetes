# Cloudflare Tunnel → Traefik Network Troubleshooting Guide

## Overview

This document provides a comprehensive troubleshooting strategy for investigating network connectivity issues between Cloudflare tunnels and Traefik, specifically targeting remaining 503 Service Unavailable errors after certificate resolver issues have been resolved.

## Background Context

- **Primary Issue Resolved**: Certificate resolver configuration eliminated (67% improvement from 30% to 10% error rate)
- **Remaining Issue**: ~10-35% 503 error rate in low-traffic application
- **Connection Path**: `Cloudflare Tunnel → Traefik Service → Traefik Pod → Backend Service → Backend Pod`
- **Evidence**: Backend pods healthy, direct service calls work, Cloudflare tunnel healthy

## Troubleshooting Strategy

### Phase 1: Configuration & Connection Path Verification

#### 1.1 Verify Cloudflare Tunnel Target Configuration

```bash
# Check tunnel configuration
kubectl get configmap -n cloudflare cloudflared-config -o yaml

# Verify tunnel is targeting the correct Traefik service
kubectl describe svc -n kube-system traefik

# Expected: Service should show healthy endpoints and proper port configuration
```

#### 1.2 Check Traefik Service Endpoints

```bash
# Verify Traefik service has healthy endpoints
kubectl get endpoints -n kube-system traefik

# Check if all Traefik pods are ready
kubectl get pods -n kube-system -l app.kubernetes.io/name=traefik -o wide

# Expected: All pods should be Running and Ready
```

#### 1.3 DNS Resolution Test

```bash
# Test DNS resolution from Cloudflare tunnel to Traefik
kubectl exec -n cloudflare deployment/cloudflared -- nslookup traefik.kube-system.svc.cluster.local

# Check if DNS resolution is slow
kubectl exec -n cloudflare deployment/cloudflared -- time nslookup traefik.kube-system.svc.cluster.local

# Expected: Resolution should complete in <10ms consistently
```

### Phase 2: Connection Path Testing

#### 2.1 Hop-by-Hop Connectivity Test

```bash
# Test from Cloudflare pod to Traefik service directly
kubectl exec -n cloudflare deployment/cloudflared -- wget -qO- --timeout=5 http://traefik.kube-system.svc.cluster.local:80/ping

# Test HTTPS connectivity (what tunnel actually uses)
kubectl exec -n cloudflare deployment/cloudflared -- wget -qO- --timeout=5 --no-check-certificate https://traefik.kube-system.svc.cluster.local:443/ping

# Expected: Both should return HTTP 200 responses
```

#### 2.2 Network Debug Pod Deep Dive

```bash
# Deploy network debugging pod
kubectl run netshoot --rm -it --image=nicolaka/netshoot -- /bin/bash

# From within netshoot pod, test:

# DNS resolution timing
dig traefik.kube-system.svc.cluster.local

# HTTP connectivity with timing
curl -w "dns: %{time_namelookup}s, connect: %{time_connect}s, total: %{time_total}s\n" \
  -o /dev/null -s http://traefik.kube-system.svc.cluster.local/ping

# Test HTTPS (tunnel's actual path)
curl -w "dns: %{time_namelookup}s, connect: %{time_connect}s, total: %{time_total}s\n" \
  -o /dev/null -s -k https://traefik.kube-system.svc.cluster.local:443/ping

# Check for network policies
iptables -L | grep -i drop

# Exit netshoot pod
exit
```

**Expected Results:**
- DNS resolution: <10ms
- Connection establishment: <100ms  
- Total time: <200ms
- No iptables DROP rules affecting traffic

### Phase 3: Real-Time Monitoring & Correlation

#### 3.1 Comprehensive Monitoring Script

Create and execute this monitoring script to correlate 503 errors across all components:

```bash
# Create comprehensive monitoring script
cat > scripts/monitor-503-correlation.sh << 'EOF'
#!/bin/bash
set -euo pipefail

LOG_DIR="/tmp/503-correlation-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$LOG_DIR"

echo "🔍 Starting correlated monitoring for 503 analysis..."
echo "📁 Logs directory: $LOG_DIR"

# Monitor all components simultaneously
kubectl logs -f -n cloudflare deployment/cloudflared --prefix=true > "$LOG_DIR/tunnel.log" 2>&1 &
TUNNEL_PID=$!

kubectl logs -f -n kube-system deployment/traefik --prefix=true > "$LOG_DIR/traefik.log" 2>&1 &
TRAEFIK_PID=$!

# Monitor resource usage
while true; do
  echo "$(date '+%Y-%m-%d %H:%M:%S'),$(kubectl top pods -n kube-system -l app.kubernetes.io/name=traefik --no-headers)" >> "$LOG_DIR/traefik-resources.log"
  sleep 1
done &
RESOURCE_PID=$!

# Cleanup function
cleanup() {
  echo "🛑 Stopping monitoring..."
  kill $TUNNEL_PID $TRAEFIK_PID $RESOURCE_PID 2>/dev/null || true
  wait 2>/dev/null || true
  echo "✅ Monitoring complete: $LOG_DIR"
}
trap cleanup EXIT

# Run test requests while monitoring
echo "🧪 Running test requests..."
for i in {1..50}; do
  timestamp=$(date '+%Y-%m-%d %H:%M:%S.%3N')
  response=$(curl -s -w "%{http_code},%{time_total},%{time_namelookup},%{time_connect}" \
    -o /dev/null https://chores.arigsela.com/health 2>/dev/null || echo "000,timeout,timeout,timeout")
  echo "$timestamp,$i,$response" | tee -a "$LOG_DIR/requests.log"
  
  # If 503, immediately capture state
  if [[ "$response" == *"503"* ]]; then
    echo "🚨 503 detected at $timestamp - capturing state..." | tee -a "$LOG_DIR/503-events.log"
    {
      echo "=== 503 Event at $timestamp ==="
      kubectl get pods -n kube-system -l app.kubernetes.io/name=traefik -o wide
      kubectl get pods -n cloudflare -o wide
      echo "=== Traefik Endpoints ==="
      kubectl get endpoints -n kube-system traefik
      echo "---"
    } >> "$LOG_DIR/503-pod-states.log"
  fi
  
  sleep 0.5
done

# Analysis
echo "📊 Analyzing results..."
{
  echo "=== REQUEST ANALYSIS ==="
  echo "Total requests: 50"
  echo "Success (200): $(grep -c ',200,' "$LOG_DIR/requests.log" || echo 0)"
  echo "Errors (503): $(grep -c ',503,' "$LOG_DIR/requests.log" || echo 0)"
  echo "Timeouts (000): $(grep -c ',000,' "$LOG_DIR/requests.log" || echo 0)"
  echo ""
  echo "=== TIMING ANALYSIS ==="
  echo "Average response time for successful requests:"
  grep ',200,' "$LOG_DIR/requests.log" | awk -F, '{sum+=$4; count++} END {if(count>0) printf "%.3fs\n", sum/count}'
  echo ""
  echo "=== 503 ERROR PATTERNS ==="
  if [ -f "$LOG_DIR/503-events.log" ]; then
    cat "$LOG_DIR/503-events.log"
  else
    echo "No 503 errors detected during monitoring period"
  fi
} > "$LOG_DIR/analysis-summary.log"

cat "$LOG_DIR/analysis-summary.log"
EOF

chmod +x scripts/monitor-503-correlation.sh
```

#### 3.2 Connection Pool Analysis

```bash
# Check Traefik connection pool settings
kubectl logs -n kube-system deployment/traefik | grep -i "maxIdleConnsPerHost\|connection"

# Monitor active connections
kubectl exec -n kube-system deployment/traefik -- netstat -an | grep :443 | wc -l

# Check connection states
kubectl exec -n kube-system deployment/traefik -- netstat -an | grep :443 | sort | uniq -c
```

### Phase 4: Load Pattern & Timing Analysis

#### 4.1 Concurrent Request Testing

```bash
# Test different concurrency levels to identify connection limits
echo "Testing concurrent request handling..."
for concurrency in 1 5 10 20; do
  echo "🧪 Testing concurrency: $concurrency"
  start_time=$(date +%s.%3N)
  
  # Use parallel requests
  seq 1 $concurrency | xargs -P $concurrency -I {} bash -c '
    response=$(curl -s -w "%{http_code}" -o /dev/null https://chores.arigsela.com/health)
    echo "$response"
  ' | sort | uniq -c
  
  end_time=$(date +%s.%3N)
  duration=$(echo "$end_time - $start_time" | bc)
  echo "   Duration: ${duration}s"
  echo ""
done
```

#### 4.2 Resource Constraint Analysis

```bash
# Monitor resource utilization during test period
echo "📊 Resource Analysis"

# Check current resource utilization
kubectl top pods -n kube-system -l app.kubernetes.io/name=traefik --containers

# Check resource limits and requests
kubectl describe pod -n kube-system -l app.kubernetes.io/name=traefik | grep -A 5 -B 5 "Limits\|Requests"

# Check node resource pressure
kubectl describe nodes | grep -A 10 "Conditions:"

# Check for resource-related events
kubectl get events -n kube-system --field-selector involvedObject.name=traefik --sort-by='.lastTimestamp'
```

### Phase 5: Specific High-Probability Issues

#### 5.1 Network Policies Investigation

```bash
# List all network policies that might affect traffic
kubectl get networkpolicies --all-namespaces -o wide

# Check specific namespaces
echo "=== Cloudflare Namespace Policies ==="
kubectl get networkpolicies -n cloudflare -o yaml

echo "=== Kube-System Namespace Policies ==="
kubectl get networkpolicies -n kube-system -o yaml

# Test network connectivity with nc (if network policies block traffic)
kubectl run test-nc --rm -it --image=nicolaka/netshoot -- nc -zv traefik.kube-system.svc.cluster.local 443
```

#### 5.2 Connection Pool & Timeout Investigation

```bash
# Check current Traefik configuration for connection settings
kubectl logs -n kube-system deployment/traefik | grep -A 10 -B 10 "serversTransport\|maxIdleConns"

# Create curl format file for detailed timing
cat > curl-format.txt << 'EOF'
     time_namelookup:  %{time_namelookup}\n
        time_connect:  %{time_connect}\n
     time_appconnect:  %{time_appconnect}\n
    time_pretransfer:  %{time_pretransfer}\n
       time_redirect:  %{time_redirect}\n
  time_starttransfer:  %{time_starttransfer}\n
                     ----------\n
          time_total:  %{time_total}\n
EOF

# Test with different connection types
echo "=== Keep-Alive Connection Test ==="
curl -w "@curl-format.txt" -H "Connection: keep-alive" -s -o /dev/null https://chores.arigsela.com/health

echo "=== New Connection Test ==="
curl -w "@curl-format.txt" -H "Connection: close" -s -o /dev/null https://chores.arigsela.com/health
```

## Expected Outcomes & Interpretation

### Success Indicators (Healthy System)
- **DNS resolution**: < 10ms consistently
- **Connection establishment**: < 100ms  
- **Total response time**: < 500ms
- **Resource utilization**: < 80% CPU/Memory
- **No network policy blocks**
- **Stable connection counts**

### Failure Indicators & Solutions

| **Symptom** | **Likely Cause** | **Investigation** | **Solution** |
|-------------|------------------|-------------------|--------------|
| **Slow DNS (>50ms)** | CoreDNS performance issues | Check CoreDNS pods, resource usage | Scale CoreDNS, increase resources |
| **Connection timeouts** | Network policies, firewall | Review NetworkPolicies, iptables rules | Remove blocking policies |
| **High resource usage** | Insufficient resources | Check CPU/memory limits vs usage | Scale Traefik, increase limits |
| **Connection pool exhaustion** | Too many concurrent connections | Check connection counts, pool settings | Tune `maxIdleConnsPerHost` |
| **Intermittent 503s** | Load balancer issues | Check service endpoints, pod health | Fix unhealthy pods/services |
| **Consistent 503s** | Routing configuration | Check IngressRoutes, service selectors | Fix routing configuration |

### Most Likely Culprits Based on Patterns

1. **Connection pool limits** (503s happen in bursts)
   - **Evidence**: Multiple 503s at same timestamp
   - **Fix**: Increase `maxIdleConnsPerHost` in Traefik config

2. **DNS resolution delays** (random 503s)
   - **Evidence**: High `time_namelookup` values
   - **Fix**: Optimize CoreDNS or add DNS caching

3. **Resource throttling** (503s correlate with high usage)
   - **Evidence**: High CPU/memory during 503s
   - **Fix**: Increase resource limits or scale Traefik

## Execution Workflow

### Quick Start (30 minutes)
1. Run **Phase 1** configuration verification
2. Execute **Phase 3.1** monitoring script  
3. Analyze results using pattern indicators above

### Deep Dive (2-3 hours)
1. Complete all phases systematically
2. Focus on phases where issues are detected
3. Implement fixes and re-test

### Emergency Response (5 minutes)
1. Check Traefik pod health: `kubectl get pods -n kube-system -l app.kubernetes.io/name=traefik`
2. Restart if unhealthy: `kubectl rollout restart deployment/traefik -n kube-system`
3. Monitor immediate improvement

## Documentation & Follow-up

- **Save all log outputs** for pattern analysis
- **Document successful fixes** for future reference  
- **Set up monitoring alerts** if issues are resolved
- **Consider permanent solutions** (scaling, resource increases) vs temporary fixes

---

**Created**: September 2025  
**Last Updated**: September 2025  
**Status**: Ready for execution