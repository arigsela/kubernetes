# 503 Error Troubleshooting Plan - Chores Tracker Application

## Problem Statement
Intermittent 503 Service Unavailable errors occurring on chores.arigsela.com, specifically on API endpoints like `/api/v1/families/context`. The error appears randomly across different endpoints and affects the application's functionality.

## Architecture Overview

### Traffic Flow
```
Browser → Cloudflare DNS → Cloudflare Tunnel (2 replicas) → Traefik Ingress → Backend/Frontend Services
```

### Service Architecture
- **Cloudflare Tunnel** (namespace: `cloudflare`): 2 replicas, port 2000 metrics
- **Traefik Ingress** (namespace: `kube-system`): Load balancer with SSL termination
- **Backend API** (namespace: `chores-tracker`): 1 replica, port 8000, `/health` endpoint
- **Frontend** (namespace: `chores-tracker-frontend`): 2 replicas, port 3000, `/health` endpoint

### Current Configuration Issues Identified
1. **Backend single point of failure**: Only 1 replica
2. **Missing liveness probe**: Backend has readiness but no liveness probe
3. **Security concerns**: Traefik has `insecureskipverify=true`
4. **Resource constraints**: Potential memory/CPU limits too low

## Investigation Strategy

### Phase 1: Real-Time Monitoring Setup

#### 1.1 Pod Status and Health Monitoring
```bash
# Check all service pods
kubectl get pods -n cloudflare
kubectl get pods -n kube-system | grep traefik
kubectl get pods -n chores-tracker
kubectl get pods -n chores-tracker-frontend

# Check for recent restarts
kubectl get pods --all-namespaces --field-selector=status.phase=Running --sort-by=.status.startTime
```

#### 1.2 Resource Utilization Monitoring
```bash
# Check resource usage
kubectl top pods -n chores-tracker
kubectl top pods -n chores-tracker-frontend
kubectl top pods -n cloudflare
kubectl top pods -n kube-system | grep traefik

# Check resource limits vs actual usage
kubectl describe pod -n chores-tracker -l app=chores-tracker
kubectl describe pod -n chores-tracker-frontend -l app=chores-tracker-frontend
```

#### 1.3 Service Endpoint Verification
```bash
# Check service endpoints
kubectl get endpoints -n chores-tracker
kubectl get endpoints -n chores-tracker-frontend
kubectl get svc -n chores-tracker
kubectl get svc -n chores-tracker-frontend
```

### Phase 2: Log Collection and Analysis

#### 2.1 Simultaneous Log Monitoring
```bash
# Create log monitoring script
#!/bin/bash
# 503-error-monitor.sh

echo "Starting comprehensive log monitoring for 503 errors..."
echo "Press Ctrl+C to stop monitoring"

# Create log files with timestamps
LOG_DIR="/tmp/503-troubleshooting-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$LOG_DIR"

# Monitor all service logs simultaneously
kubectl logs -f -n cloudflare -l app=cloudflared --prefix=true > "$LOG_DIR/cloudflare.log" &
kubectl logs -f -n kube-system deployment/traefik --prefix=true > "$LOG_DIR/traefik.log" &
kubectl logs -f -n chores-tracker -l app=chores-tracker --prefix=true > "$LOG_DIR/backend.log" &
kubectl logs -f -n chores-tracker-frontend -l app=chores-tracker-frontend --prefix=true > "$LOG_DIR/frontend.log" &

echo "Logs being written to: $LOG_DIR"
echo "Monitoring for 503 errors..."

# Wait for interrupt
wait
```

#### 2.2 Error Pattern Analysis Commands
```bash
# Check recent logs for errors
kubectl logs --since=1h -n chores-tracker -l app=chores-tracker | grep -i "error\|fail\|503\|timeout"
kubectl logs --since=1h -n chores-tracker-frontend -l app=chores-tracker-frontend | grep -i "error\|fail\|503\|timeout"
kubectl logs --since=1h -n cloudflare -l app=cloudflared | grep -i "error\|fail\|503\|timeout"
kubectl logs --since=1h -n kube-system deployment/traefik | grep -i "error\|fail\|503\|timeout"
```

### Phase 3: Service Health and Connectivity Testing

#### 3.1 Internal Service Connectivity Tests
```bash
# Test backend health endpoint directly
kubectl exec -n chores-tracker deployment/chores-tracker -- curl -f http://localhost:8000/health

# Test frontend health endpoint directly
kubectl exec -n chores-tracker-frontend deployment/chores-tracker-frontend -- curl -f http://localhost:3000/health

# Test inter-service connectivity
kubectl exec -n chores-tracker-frontend deployment/chores-tracker-frontend -- curl -f http://chores-tracker.chores-tracker.svc.cluster.local/health
```

#### 3.2 External Connectivity Tests
```bash
# Test from external perspective
curl -v https://chores.arigsela.com/api/v1/health
curl -v https://chores.arigsela.com/health

# Test with different user agents and headers
curl -v -H "User-Agent: Mozilla/5.0" https://chores.arigsela.com/api/v1/families/context
```

### Phase 4: Load Testing and Pattern Recognition

#### 4.1 Controlled Load Testing
```bash
# Simple load test to reproduce 503 errors
for i in {1..50}; do
  curl -s -o /dev/null -w "%{http_code} %{time_total}\n" https://chores.arigsela.com/api/v1/families/context &
done
wait

# Monitor during load test
while true; do
  kubectl get pods -n chores-tracker -o wide
  kubectl get pods -n chores-tracker-frontend -o wide
  sleep 5
done
```

#### 4.2 Timing Pattern Analysis
```bash
# Check when errors occur most frequently
# Monitor during different times and record patterns
echo "Error Pattern Analysis - $(date)" >> /tmp/error-patterns.log
curl -s https://chores.arigsela.com/api/v1/families/context | echo "$(date): HTTP $(curl -o /dev/null -s -w "%{http_code}" https://chores.arigsela.com/api/v1/families/context)" >> /tmp/error-patterns.log
```

### Phase 5: Configuration Analysis

#### 5.1 Ingress Route Verification
```bash
# Check Traefik routing configuration
kubectl get ingressroute -n chores-tracker -o yaml
kubectl get ingressroute -n chores-tracker-frontend -o yaml

# Check for routing conflicts
kubectl describe ingressroute -n chores-tracker chores-tracker
kubectl describe ingressroute -n chores-tracker-frontend chores-tracker-frontend
```

#### 5.2 DNS and SSL Certificate Status
```bash
# Check certificate status
kubectl get certificates --all-namespaces
kubectl describe certificate -n chores-tracker
kubectl describe certificate -n chores-tracker-frontend

# Check Traefik dashboard for SSL status (if accessible)
kubectl port-forward -n kube-system deployment/traefik 8080:8080
# Access http://localhost:8080/dashboard/
```

## Immediate Fixes to Implement

### Fix 1: Increase Backend Replica Count
**Issue**: Single backend replica creates a single point of failure
**Solution**: Increase backend replicas to 2

```yaml
# Update base-apps/chores-tracker/deployments.yaml
spec:
  replicas: 2  # Change from 1 to 2
```

### Fix 2: Add Backend Liveness Probe
**Issue**: Missing liveness probe on backend service
**Solution**: Add liveness probe to match readiness probe

```yaml
# Add to backend deployment
livenessProbe:
  httpGet:
    path: /health
    port: 8000
  initialDelaySeconds: 60
  periodSeconds: 10
  timeoutSeconds: 5
  failureThreshold: 3
```

### Fix 3: Enable Debug Logging Temporarily
**Issue**: Insufficient logging for troubleshooting
**Solution**: Increase Traefik log level

```yaml
# Update base-apps/traefik-config/helm_chart_config.yaml
additionalArguments:
  - "--log.level=DEBUG"  # Change from INFO to DEBUG
  - "--accesslog=true"   # Enable access logging
```

### Fix 4: Add Resource Monitoring
**Issue**: No visibility into resource constraints
**Solution**: Add resource monitoring alerts

```bash
# Check if resources are hitting limits
kubectl top pods --all-namespaces --sort-by=memory
kubectl top pods --all-namespaces --sort-by=cpu
```

## Expected Root Causes and Solutions

### Scenario 1: Backend Pod Restarts
**Symptoms**: Single backend pod restarting due to memory pressure
**Detection**: Check `kubectl get pods -n chores-tracker` for restart count
**Solution**: Increase memory limits and add multiple replicas

### Scenario 2: Connection Pool Exhaustion
**Symptoms**: Backend can't handle concurrent connections
**Detection**: Backend logs show connection timeouts
**Solution**: Tune backend connection pool settings, add replicas

### Scenario 3: Traefik Routing Issues
**Symptoms**: Intermittent routing failures between API and static content
**Detection**: Traefik logs show routing mismatches
**Solution**: Review and optimize routing priorities

### Scenario 4: Cloudflare Tunnel Instability
**Symptoms**: Tunnel disconnections causing temporary outages
**Detection**: Cloudflare logs show reconnection attempts
**Solution**: Tune tunnel keep-alive settings, check network stability

### Scenario 5: SSL Certificate Issues
**Symptoms**: HTTPS handshake failures
**Detection**: Certificate renewal failures in logs
**Solution**: Fix ACME certificate resolver configuration

## Testing Script Template

```bash
#!/bin/bash
# comprehensive-503-test.sh

echo "=== Starting 503 Error Investigation ==="
echo "Timestamp: $(date)"

# Phase 1: Current Status
echo "Phase 1: Current System Status"
kubectl get pods --all-namespaces | grep -E "(chores|traefik|cloudflare)"

# Phase 2: Resource Check
echo "Phase 2: Resource Utilization"
kubectl top pods -n chores-tracker
kubectl top pods -n chores-tracker-frontend
kubectl top pods -n cloudflare

# Phase 3: Service Tests
echo "Phase 3: Service Health Tests"
curl -s -o /dev/null -w "Backend Health: %{http_code}\n" http://localhost:8080/health || echo "Backend health check failed"
curl -s -o /dev/null -w "Frontend Health: %{http_code}\n" https://chores.arigsela.com/health || echo "Frontend health check failed"
curl -s -o /dev/null -w "API Endpoint: %{http_code}\n" https://chores.arigsela.com/api/v1/families/context || echo "API endpoint failed"

# Phase 4: Log Collection
echo "Phase 4: Recent Error Logs"
kubectl logs --since=10m -n chores-tracker -l app=chores-tracker | tail -20
kubectl logs --since=10m -n chores-tracker-frontend -l app=chores-tracker-frontend | tail -20

echo "=== Investigation Complete ==="
```

## Action Items Checklist

### Immediate Actions (Priority 1)
- [ ] Increase backend replicas from 1 to 2
- [ ] Add liveness probe to backend deployment
- [ ] Enable debug logging on Traefik
- [ ] Set up continuous monitoring script

### Short-term Actions (Priority 2)
- [ ] Review and optimize resource limits
- [ ] Implement proper health check endpoints
- [ ] Set up alerting for pod restarts and 5xx errors
- [ ] Review Cloudflare tunnel stability

### Long-term Actions (Priority 3)
- [ ] Implement distributed tracing
- [ ] Set up proper monitoring dashboard
- [ ] Review security configurations
- [ ] Optimize application performance

## Documentation and Tracking

This document should be updated with findings as investigation progresses. Each phase should include:
- Timestamp of investigation
- Commands executed
- Results observed
- Actions taken
- Next steps

**Last Updated**: $(date)
**Status**: Investigation Plan Created
**Next Action**: Execute Phase 1 monitoring setup