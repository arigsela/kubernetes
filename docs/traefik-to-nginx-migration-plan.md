# Traefik to NGINX Ingress Controller Migration Plan

**Date**: September 2, 2025  
**Status**: 🟡 **IN PROGRESS**  
**Completion**: 25% (1/4 phases completed - Phase 2 ready)

---

## 🎯 **Migration Objective**

Migrate from Traefik to NGINX Ingress Controller to resolve the remaining 18% 503 error rate while maintaining zero-downtime and rollback capability.

## 📊 **Current State Analysis**

| **Component** | **Status** | **Performance** | **Issues** |
|---------------|------------|-----------------|------------|
| **Backend Services** | ✅ Healthy | 100% success rate | None |
| **Traefik Ingress** | ⚠️ Issues | 18% error rate | HTTP/2 + Cloudflare tunnel compatibility |
| **Cloudflare Tunnel** | ✅ Healthy | Stream cancellation resolved | None |
| **Applications** | ✅ Healthy | chores-tracker + frontend | None |

**Root Cause**: Ingress-layer compatibility issues with Cloudflare tunnels despite timeout fixes.

---

## 📋 **Implementation Phases**

### ✅ Phase Status Legend
- ⬜ **Not Started** - Phase not begun
- 🟡 **In Progress** - Phase currently being worked on  
- ✅ **Completed** - Phase finished successfully
- ❌ **Failed** - Phase encountered issues
- 🔄 **Rollback** - Phase reverted due to issues

---

## 🚀 **Phase 1: Preparation and Planning** *(30 minutes)*

**Status**: ✅ **COMPLETED**  
**Started**: September 2, 2025 - 2:45 PM  
**Completed**: September 2, 2025 - 2:55 PM  
**Progress**: 3/3 tasks completed

### Tasks:

#### ✅ 1.1 Document Current Configuration *(5 minutes)*
```bash
# Backup current Traefik setup
kubectl get ingressroute -A -o yaml > traefik-backup.yaml
kubectl get serversTransport -A -o yaml >> traefik-backup.yaml
kubectl get configmap -n cloudflare cloudflared-config -o yaml > cloudflare-tunnel-backup.yaml
```
**Status**: ✅ **COMPLETED**  
**Notes**: _Backup files created: traefik-backup.yaml, cloudflare-tunnel-backup.yaml_

#### ✅ 1.2 Create NGINX Ingress Controller Configuration *(15 minutes)*

**Create**: `/base-apps/nginx-ingress/nginx-ingress-controller.yaml`
```yaml
apiVersion: helm.cattle.io/v1
kind: HelmChart
metadata:
  name: ingress-nginx
  namespace: kube-system
spec:
  chart: ingress-nginx
  repo: https://kubernetes.github.io/ingress-nginx
  targetNamespace: ingress-nginx
  createNamespace: true
  version: v4.11.2
  valuesContent: |-
    controller:
      kind: DaemonSet
      hostNetwork: false
      service:
        type: ClusterIP
        ports:
          http: 80
          https: 443
      config:
        # Timeout configurations equivalent to our ServersTransport fixes
        proxy-read-timeout: "30"      # responseHeaderTimeout equivalent
        proxy-connect-timeout: "30"   # dialTimeout equivalent  
        proxy-send-timeout: "30"      # writeTimeout equivalent
        keep-alive-requests: "100"    # maxIdleConnsPerHost equivalent
        upstream-keepalive-connections: "100"
        upstream-keepalive-requests: "100"
        upstream-keepalive-timeout: "60"
        # SSL and connection optimizations
        ssl-protocols: "TLSv1.2 TLSv1.3"
        use-http2: "true"
        # Cloudflare tunnel optimization
        use-forwarded-headers: "true"
        compute-full-forwarded-for: "true"
        forwarded-for-header: "X-Forwarded-For"
        real-ip-header: "X-Forwarded-For"
        trusted-proxies: "173.245.48.0/20,103.21.244.0/22,103.22.200.0/22,103.31.4.0/22,141.101.64.0/18,108.162.192.0/18,190.93.240.0/20,188.114.96.0/20,197.234.240.0/22,198.41.128.0/17,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
    tcp: {}
    udp: {}
```
**Status**: ✅ **COMPLETED**  
**Notes**: _NGINX configuration created with Cloudflare-optimized timeouts and equivalent ServersTransport settings_

#### ✅ 1.3 Convert IngressRoute to Standard Ingress Resources *(10 minutes)*

**Create**: `/base-apps/chores-tracker/nginx-ingress.yaml`
```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: chores-tracker-nginx
  namespace: chores-tracker
  annotations:
    nginx.ingress.kubernetes.io/ssl-redirect: "false"
    nginx.ingress.kubernetes.io/backend-protocol: "HTTP"
    nginx.ingress.kubernetes.io/proxy-read-timeout: "30"
    nginx.ingress.kubernetes.io/proxy-connect-timeout: "30"
    nginx.ingress.kubernetes.io/proxy-send-timeout: "30"
spec:
  ingressClassName: nginx
  rules:
  - host: chores.arigsela.com
    http:
      paths:
      - path: /api
        pathType: Prefix
        backend:
          service:
            name: chores-tracker
            port:
              number: 80
```

**Create**: `/base-apps/chores-tracker-frontend/nginx-ingress.yaml`
```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: chores-tracker-frontend-nginx
  namespace: chores-tracker-frontend
  annotations:
    nginx.ingress.kubernetes.io/ssl-redirect: "false"
    nginx.ingress.kubernetes.io/backend-protocol: "HTTP"
    nginx.ingress.kubernetes.io/proxy-read-timeout: "30"
    nginx.ingress.kubernetes.io/proxy-connect-timeout: "30"
    nginx.ingress.kubernetes.io/proxy-send-timeout: "30"
    nginx.ingress.kubernetes.io/priority: "10"
spec:
  ingressClassName: nginx
  rules:
  - host: chores.arigsela.com
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: chores-tracker-frontend
            port:
              number: 80
```
**Status**: ✅ **COMPLETED**  
**Notes**: _Created NGINX Ingress resources for both chores-tracker API and frontend with equivalent timeout configurations_

### Phase 1 Success Criteria:
- ✅ All configuration files created
- ✅ Backup files generated  
- ✅ Git branch prepared for deployment

---

## ⚙️ **Phase 2: Parallel Deployment** *(20 minutes)*

**Status**: ⬜ **NOT STARTED**  
**Started**: _Not started_  
**Completed**: _Not started_  
**Progress**: 0/3 tasks completed

### Tasks:

#### ⬜ 2.1 Deploy NGINX Ingress Controller *(10 minutes)*
```bash
# Create NGINX configuration branch
git checkout -b nginx-ingress-migration

# Add NGINX configurations
mkdir -p base-apps/nginx-ingress
# Copy configuration files created in Phase 1

# Deploy via ArgoCD
git add .
git commit -m "feat: Add NGINX Ingress Controller parallel deployment

- Add NGINX Ingress Controller with Cloudflare tunnel optimized timeouts
- Convert IngressRoutes to standard Ingress resources
- Configure equivalent timeout settings to resolved Traefik issues
- Parallel deployment for zero-downtime migration testing

🤖 Generated with [Claude Code](https://claude.ai/code)"

git push origin nginx-ingress-migration
```
**Status**: ⬜ Not started  
**Expected Result**: NGINX Ingress Controller deployed alongside Traefik

#### ⬜ 2.2 Verify NGINX Deployment *(5 minutes)*
```bash
# Wait for NGINX pods
kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=ingress-nginx -n ingress-nginx --timeout=300s

# Check NGINX service
kubectl get svc -n ingress-nginx
kubectl get pods -n ingress-nginx

# Expected: ingress-nginx-controller service with ClusterIP and Running pods
```
**Status**: ⬜ Not started  
**Expected Result**: All NGINX pods Running and Ready

#### ⬜ 2.3 Internal Connectivity Testing *(5 minutes)*
```bash
# Test NGINX internally - health endpoint
kubectl run nginx-test --rm -i --image=curlimages/curl --restart=Never -- \
  curl -H "Host: chores.arigsela.com" \
  http://ingress-nginx-controller.ingress-nginx.svc.cluster.local/health

# Test API path
kubectl run nginx-api-test --rm -i --image=curlimages/curl --restart=Never -- \
  curl -H "Host: chores.arigsela.com" \
  http://ingress-nginx-controller.ingress-nginx.svc.cluster.local/api/health

# Test frontend path  
kubectl run nginx-frontend-test --rm -i --image=curlimages/curl --restart=Never -- \
  curl -H "Host: chores.arigsela.com" \
  http://ingress-nginx-controller.ingress-nginx.svc.cluster.local/
```
**Status**: ⬜ Not started  
**Expected Result**: All internal tests return HTTP 200

### Phase 2 Success Criteria:
- ✅ NGINX Ingress Controller deployed and Running
- ✅ Internal connectivity tests pass
- ✅ Both API and frontend paths working
- ✅ Traefik still running in parallel

---

## 🔄 **Phase 3: Traffic Switching** *(10 minutes + monitoring)*

**Status**: ⬜ **NOT STARTED**  
**Started**: _Not started_  
**Completed**: _Not started_  
**Progress**: 0/3 tasks completed

### Tasks:

#### ⬜ 3.1 Update Cloudflare Tunnel Configuration *(5 minutes)*

**Update**: `/base-apps/cloudflare-tunnel/tunnel-config.yaml`
```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: cloudflared-config
  namespace: cloudflare
data:
  config.yml: |
    tunnel: homelab-k3s
    credentials-file: /etc/cloudflared/creds/token
    metrics: 0.0.0.0:2000
    no-autoupdate: true
    ingress:
      - hostname: "*.arigsela.com"
        service: https://ingress-nginx-controller.ingress-nginx.svc.cluster.local:443
        originRequest:
          noTLSVerify: true
      - hostname: "arigsela.com"  
        service: https://ingress-nginx-controller.ingress-nginx.svc.cluster.local:443
        originRequest:
          noTLSVerify: true
      - service: http_status:404
```
**Status**: ⬜ Not started  
**Critical Change**: `traefik.kube-system.svc.cluster.local:443` → `ingress-nginx-controller.ingress-nginx.svc.cluster.local:443`

#### ⬜ 3.2 Deploy Traffic Switch *(2 minutes)*
```bash
# Commit tunnel configuration change
git add base-apps/cloudflare-tunnel/tunnel-config.yaml
git commit -m "feat: Switch Cloudflare tunnel from Traefik to NGINX

- Update tunnel target: traefik -> ingress-nginx-controller  
- Maintain SSL and noTLSVerify settings
- Enable rollback by keeping Traefik running

🤖 Generated with [Claude Code](https://claude.ai/code)"

git push origin nginx-ingress-migration
```
**Status**: ⬜ Not started  
**Critical Moment**: Traffic switches from Traefik to NGINX

#### ⬜ 3.3 Immediate Validation *(3 minutes)*
```bash
# Test error rate immediately after switch
echo "🧪 Testing NGINX error rate after traffic switch..."
success=0 && total=20
for i in $(seq 1 $total); do
  response=$(curl -s -w "%{http_code}" -o /dev/null --max-time 10 https://chores.arigsela.com/health)
  if [[ "$response" == "200" ]]; then ((success++)); fi
  echo -n "$response "
done
echo ""
error_rate=$(( (total-success)*100/total ))
echo "NGINX Results: $success/$total successful ($error_rate% error rate)"

# Log results
echo "$(date): Traffic switch complete - $success/$total successful ($error_rate% error rate)" >> migration.log
```
**Status**: ⬜ Not started  
**Target**: Error rate < 10% (better than current 18%)

### Phase 3 Success Criteria:
- ✅ Cloudflare tunnel successfully updated
- ✅ Traffic routing to NGINX
- ✅ Error rate < 10% immediately after switch
- ✅ Both API and frontend accessible externally

---

## ✅ **Phase 4: Validation and Cleanup** *(24-48 hours monitoring)*

**Status**: ⬜ **NOT STARTED**  
**Started**: _Not started_  
**Completed**: _Not started_  
**Progress**: 0/3 tasks completed

### Tasks:

#### ⬜ 4.1 Extended Monitoring *(24-48 hours)*
```bash
# Create monitoring script
cat > scripts/nginx-migration-monitor.sh << 'EOF'
#!/bin/bash
LOG_FILE="nginx-migration-$(date +%Y%m%d-%H%M%S).log"
echo "$(date): Starting NGINX migration monitoring..." | tee -a $LOG_FILE

for hour in {1..24}; do
  echo "=== Hour $hour monitoring ===" | tee -a $LOG_FILE
  success=0 && total=100
  
  for i in $(seq 1 $total); do
    response=$(curl -s -w "%{http_code}" -o /dev/null --max-time 10 https://chores.arigsela.com/health 2>/dev/null || echo "000")
    if [[ "$response" == "200" ]]; then ((success++)); fi
  done
  
  error_rate=$(( (total-success)*100/total ))
  echo "$(date): Hour $hour - $success/$total successful ($error_rate% error rate)" | tee -a $LOG_FILE
  
  # Test concurrent requests every 6 hours
  if (( hour % 6 == 0 )); then
    echo "=== Hour $hour - Concurrent Test ===" | tee -a $LOG_FILE
    seq 1 10 | xargs -P 10 -I {} bash -c 'curl -s -w "%{http_code}" -o /dev/null --max-time 10 https://chores.arigsela.com/health' | sort | uniq -c | tee -a $LOG_FILE
  fi
  
  sleep 3600  # 1 hour
done
EOF

chmod +x scripts/nginx-migration-monitor.sh
nohup ./scripts/nginx-migration-monitor.sh &
echo "Monitoring PID: $!" >> migration.log
```
**Status**: ⬜ Not started  
**Duration**: 24-48 hours continuous monitoring

#### ⬜ 4.2 Performance Analysis *(Ongoing)*

**Metrics to Track**:
| **Metric** | **Current (Traefik)** | **Target (NGINX)** | **Actual (NGINX)** |
|------------|------------------------|-------------------|-------------------|
| **Error Rate** | 18% | < 5% | _TBD_ |
| **Concurrent (10 req)** | 80% success | > 90% success | _TBD_ |
| **Stream Cancellation** | Eliminated | Stay eliminated | _TBD_ |
| **Response Time** | Variable | < 500ms consistent | _TBD_ |

**Status**: ⬜ Not started  
**Update**: _Results will be logged here during monitoring_

#### ⬜ 4.3 Success Decision and Cleanup *(After 48 hours)*

**If Successful (Error rate < 5%)**:
```bash
# Clean up Traefik resources
kubectl delete helmchart traefik -n kube-system
kubectl delete serversTransport cloudflare-tunnel-transport -n kube-system  
kubectl delete ingressroute chores-tracker -n chores-tracker
kubectl delete ingressroute chores-tracker-frontend -n chores-tracker-frontend

# Merge migration branch
git checkout main
git merge nginx-ingress-migration
git push origin main

# Document success
echo "✅ NGINX Migration Successful - $(date)" >> migration-success.md
echo "Final Error Rate: [RECORD ACTUAL]%" >> migration-success.md
```

**If Unsuccessful (Error rate >= 10%)**:
```bash
# Initiate rollback (see Rollback Plan below)
echo "❌ NGINX Migration Failed - $(date)" >> migration-failure.md
# Follow rollback procedure
```
**Status**: ⬜ Not started  
**Decision Point**: Cleanup vs Rollback based on performance metrics

### Phase 4 Success Criteria:
- ✅ Error rate < 5% sustained for 48 hours
- ✅ Concurrent request success > 90%
- ✅ No stream cancellation errors
- ✅ Response times < 500ms consistently

---

## 🔙 **Rollback Plan**

### 🚨 **Immediate Rollback** *(< 5 minutes)*
**Use if**: Error rate > 50% or complete service failure

```bash
# 1. Revert Cloudflare tunnel configuration
git checkout main
git checkout -- base-apps/cloudflare-tunnel/tunnel-config.yaml
git add base-apps/cloudflare-tunnel/tunnel-config.yaml
git commit -m "rollback: Emergency revert to Traefik - NGINX migration failed"
git push origin nginx-ingress-migration

# 2. Verify Traefik is receiving traffic
curl -I https://chores.arigsela.com/health

# 3. Monitor recovery
echo "$(date): Emergency rollback initiated" >> rollback.log
```

### 🔄 **Planned Rollback** *(10 minutes)*
**Use if**: Error rate 10-50% or performance degradation

```bash
# 1. Document rollback reason
echo "$(date): Planned rollback - Error rate: [X]%" >> rollback.log

# 2. Revert tunnel configuration
git revert [commit-hash-of-tunnel-switch]
git push origin nginx-ingress-migration

# 3. Wait for ArgoCD sync (2-3 minutes)
kubectl wait --for=condition=ready pod -l app=cloudflared -n cloudflare --timeout=300s

# 4. Validate Traefik recovery
success=0 && total=20
for i in $(seq 1 $total); do
  response=$(curl -s -w "%{http_code}" -o /dev/null --max-time 10 https://chores.arigsela.com/health)
  if [[ "$response" == "200" ]]; then ((success++)); fi
done
error_rate=$(( (total-success)*100/total ))
echo "$(date): Rollback validation - $success/$total successful ($error_rate% error rate)" >> rollback.log
```

### 🧹 **Complete Rollback Cleanup** *(15 minutes)*
**Use if**: Abandoning NGINX migration completely

```bash
# 1. Remove NGINX Ingress resources
kubectl delete helmchart ingress-nginx -n kube-system
kubectl delete namespace ingress-nginx
kubectl delete ingress chores-tracker-nginx -n chores-tracker
kubectl delete ingress chores-tracker-frontend-nginx -n chores-tracker-frontend

# 2. Clean up git branch
git checkout main
git branch -D nginx-ingress-migration
git push origin --delete nginx-ingress-migration

# 3. Document rollback completion
echo "✅ Complete rollback to Traefik completed - $(date)" >> rollback-complete.log
```

---

## 📈 **Progress Tracking**

### Overall Migration Status
- **Phase 1 (Preparation)**: ✅ **COMPLETED** *(September 2, 2025 - 2:55 PM)*
- **Phase 2 (Deployment)**: ⬜ **READY TO START**  
- **Phase 3 (Traffic Switch)**: ⬜ Not Started
- **Phase 4 (Validation)**: ⬜ Not Started

### Key Milestones
- ✅ **Configuration files created** *(Phase 1 complete)*
- ✅ **Backup files generated** *(Phase 1 complete)*
- ✅ **Git branch prepared** *(Phase 1 complete)*
- ⬜ NGINX Ingress Controller deployed
- ⬜ Internal testing passed
- ⬜ Traffic switched to NGINX
- ⬜ Error rate < 10% achieved
- ⬜ 24-hour monitoring completed
- ⬜ Migration decision made

### Risk Assessment
- **Current Risk**: 🟡 **MEDIUM** - Have working rollback plan
- **Impact**: Service availability during migration
- **Mitigation**: Parallel deployment + instant rollback capability

---

## ⏱️ **Timeline Summary**

| **Phase** | **Duration** | **Status** | **Started** | **Completed** |
|-----------|-------------|------------|-------------|---------------|
| **Phase 1** | 30 minutes | ✅ **COMPLETED** | Sept 2, 2:45 PM | Sept 2, 2:55 PM |
| **Phase 2** | 20 minutes | ⬜ **READY** | _TBD_ | _TBD_ |
| **Phase 3** | 10 minutes | ⬜ Not Started | _TBD_ | _TBD_ |
| **Phase 4** | 24-48 hours | ⬜ Not Started | _TBD_ | _TBD_ |

**Total Active Time**: ~1 hour  
**Total Project Duration**: 2-3 days including monitoring

---

## 📝 **Notes and Updates**

### Migration Log

**September 2, 2025 - 2:55 PM**: ✅ **Phase 1 COMPLETED**  
- Created comprehensive NGINX Ingress Controller configuration with Cloudflare tunnel optimizations
- Generated backup files: `traefik-backup.yaml`, `cloudflare-tunnel-backup.yaml`
- Converted Traefik IngressRoute CRDs to standard Kubernetes Ingress resources
- Created ArgoCD Application manifest for parallel deployment
- Branch `nginx-ingress-migration` ready for Phase 2 deployment
- **Next**: Phase 2 - Deploy NGINX alongside Traefik for parallel testing

---

**Last Updated**: September 2, 2025  
**Next Review**: _After Phase 1 completion_  
**Migration Lead**: Claude Code Assistant  
**Document Version**: 1.0