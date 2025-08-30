# Cloudflare Tunnel GitOps Implementation Plan

## Overview

This document provides a comprehensive GitOps-compatible implementation plan for deploying Cloudflare tunnels in our Kubernetes cluster. The implementation follows our established ArgoCD application patterns and integrates with our existing External Secrets Operator and Vault infrastructure.

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Architecture Overview](#architecture-overview)
3. [Implementation Phases](#implementation-phases)
4. [GitOps Deployment](#gitops-deployment)
5. [Verification and Testing](#verification-and-testing)
6. [Troubleshooting](#troubleshooting)
7. [Maintenance and Operations](#maintenance-and-operations)

## Prerequisites

### Required Information
- Cloudflare API Token with Zone:Read and DNS:Edit permissions
- Cloudflare Tunnel Token (create tunnel first via `cloudflared` CLI)
- Email address for ACME certificate registration
- Your k3s cluster node IP address

### Existing Infrastructure Dependencies
- ArgoCD master application watching `/base-apps/`
- Vault instance running in `vault` namespace
- External Secrets Operator deployed and configured
- Traefik ingress controller (will be reconfigured)

## Architecture Overview

### Components
```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   Cloudflare    │────│  Cloudflare      │────│    Traefik      │
│     DNS         │    │     Tunnel       │    │   Ingress       │
└─────────────────┘    └──────────────────┘    └─────────────────┘
                              │                          │
                              │                          │
                       ┌──────▼──────┐            ┌─────▼─────┐
                       │ cloudflared │            │   Apps    │
                       │   Pods      │            │Services   │
                       └─────────────┘            └───────────┘
```

### ArgoCD Applications Structure
```
base-apps/
├── cloudflare-tunnel.yaml          # ArgoCD Application
├── cloudflare-tunnel/              # Tunnel manifests
│   ├── external_secrets.yaml
│   ├── service_accounts.yaml
│   ├── configmaps.yaml
│   ├── deployments.yaml
│   └── services.yaml
├── traefik-config.yaml             # ArgoCD Application
├── traefik-config/                 # Traefik updates
│   └── helm_chart_config.yaml
└── whoami-test.yaml                # Test application
    └── whoami-test/
        ├── deployments.yaml
        ├── services.yaml
        └── ingress.yaml
```

## Implementation Phases

### Phase 1: Repository Structure Setup (10 minutes)

#### 1.1 Create Application Directory Structure
```bash
mkdir -p base-apps/cloudflare-tunnel
mkdir -p base-apps/traefik-config
mkdir -p base-apps/whoami-test
```

#### 1.2 Create ArgoCD Application Definitions

**File: `base-apps/cloudflare-tunnel.yaml`**
```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: cloudflare-tunnel
  namespace: argo-cd
spec:
  project: default
  source:
    repoURL: https://github.com/arigsela/kubernetes
    targetRevision: main
    path: base-apps/cloudflare-tunnel
  destination:
    server: https://kubernetes.default.svc
    namespace: cloudflare
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
```

**File: `base-apps/traefik-config.yaml`**
```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: traefik-config
  namespace: argo-cd
spec:
  project: default
  source:
    repoURL: https://github.com/arigsela/kubernetes
    targetRevision: main
    path: base-apps/traefik-config
  destination:
    server: https://kubernetes.default.svc
    namespace: kube-system
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
```

**File: `base-apps/whoami-test.yaml`**
```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: whoami-test
  namespace: argo-cd
spec:
  project: default
  source:
    repoURL: https://github.com/arigsela/kubernetes
    targetRevision: main
    path: base-apps/whoami-test
  destination:
    server: https://kubernetes.default.svc
    namespace: default
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
```

#### 1.3 Testing Phase 1
```bash
# Create feature branch
git checkout -b feature/cloudflare-tunnel-testing

# Commit Phase 1 changes
git add base-apps/cloudflare-tunnel.yaml base-apps/traefik-config.yaml base-apps/whoami-test.yaml
git commit -m "feat: Phase 1 - Create ArgoCD application definitions"
git push origin feature/cloudflare-tunnel-testing

# Test: Verify ArgoCD applications are created (they will be syncing to empty directories initially)
kubectl get applications -n argo-cd | grep -E "(cloudflare|traefik|whoami)"
# Expected: Applications should be created but in "OutOfSync" state due to empty paths
```

### Phase 2: Secret Management Integration (10 minutes)

#### 2.1 Update Existing SecretStore Configuration

**Update: `base-apps/vault/secret_stores.yaml`** (Already added)
```yaml
# Added cloudflare namespace to existing vault-backend SecretStore pattern
apiVersion: external-secrets.io/v1beta1
kind: SecretStore
metadata:
  name: vault-backend
  namespace: cloudflare
spec:
  provider:
    vault:
      server: "http://vault.vault.svc.cluster.local:8200"
      path: "secret"
      version: "v2"
      auth:
        tokenSecretRef:
          name: vault-token
          key: token
```

#### 2.2 External Secrets Configuration

**File: `base-apps/cloudflare-tunnel/external_secrets.yaml`**
```yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: cloudflare-credentials
  namespace: cloudflare
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: vault-backend
    kind: SecretStore
  target:
    name: cloudflare-api-credentials
    creationPolicy: Owner
  data:
  - secretKey: apitoken
    remoteRef:
      key: cloudflare
      property: api_token
---
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: tunnel-credentials
  namespace: cloudflare
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: vault-backend
    kind: SecretStore
  target:
    name: tunnel-credentials
    creationPolicy: Owner
  data:
  - secretKey: token
    remoteRef:
      key: cloudflare
      property: tunnel_token
```

> **Note**: Uses existing `vault-backend` SecretStore pattern from your repository. Ensure `vault-token` secret exists in the `cloudflare` namespace.

#### 2.3 Testing Phase 2
```bash
# Commit Phase 2 changes
git add base-apps/vault/secret_stores.yaml
git add base-apps/cloudflare-tunnel/external_secrets.yaml
git commit -m "feat: Phase 2 - Add External Secrets and SecretStore for cloudflare namespace"
git push origin feature/cloudflare-tunnel-testing

# Test: Verify External Secrets are working
kubectl get externalsecrets -n cloudflare
kubectl describe externalsecrets -n cloudflare
kubectl get secrets -n cloudflare

# Expected Results:
# - externalsecrets should show "SecretSynced" status: True
# - secrets cloudflare-api-credentials and tunnel-credentials should exist
# - If secrets fail to sync, check vault-token secret exists in cloudflare namespace

# Debug if needed:
kubectl logs -n external-secrets -l app.kubernetes.io/name=external-secrets --tail=20
```

### Phase 3: Traefik HTTPS/TLS Configuration (15 minutes)

This phase configures Traefik with HTTPS support, Cloudflare DNS challenge for automatic SSL certificates, and proper forwarded headers for tunnel integration.

#### 3.1 Current vs Target Configuration

**Current State**: HTTP-only IngressRoutes with `web` entry point
**Target State**: HTTPS-enabled with automatic SSL certificates and `websecure` entry point

#### 3.2 HelmChartConfig for Traefik

**File: `base-apps/traefik-config/helm_chart_config.yaml`**
```yaml
apiVersion: helm.sh/v1
kind: HelmChartConfig
metadata:
  name: traefik
  namespace: kube-system
spec:
  valuesContent: |-
    # Entry Points Configuration
    ports:
      web:
        port: 8000
        expose: true
        exposedPort: 80
        protocol: TCP
        # HTTP to HTTPS redirect
        redirectTo: websecure
        forwardedHeaders:
          trustedIPs:
            - 10.0.0.0/8
            - 172.16.0.0/12
            - 192.168.0.0/16
            - 173.245.48.0/20    # Cloudflare IP ranges
            - 103.21.244.0/22
            - 103.22.200.0/22
            - 103.31.4.0/22
            - 141.101.64.0/18
            - 108.162.192.0/18
            - 190.93.240.0/20
            - 188.114.96.0/20
            - 197.234.240.0/22
            - 198.41.128.0/17
      websecure:
        port: 8443
        expose: true
        exposedPort: 443
        protocol: TCP
        tls:
          enabled: true
        forwardedHeaders:
          trustedIPs:
            - 10.0.0.0/8
            - 172.16.0.0/12
            - 192.168.0.0/16
            - 173.245.48.0/20    # Cloudflare IP ranges
            - 103.21.244.0/22
            - 103.22.200.0/22
            - 103.31.4.0/22
            - 141.101.64.0/18
            - 108.162.192.0/18
            - 190.93.240.0/20
            - 188.114.96.0/20
            - 197.234.240.0/22
            - 198.41.128.0/17
    
    # API and Dashboard
    api:
      dashboard: true
      insecure: false  # Use HTTPS for dashboard
    
    # ACME Certificate Resolver for Cloudflare DNS Challenge
    certificatesResolvers:
      cloudflare:
        acme:
          email: YOUR_EMAIL@example.com  # UPDATE THIS BEFORE DEPLOYMENT
          storage: /data/acme.json
          dnsChallenge:
            provider: cloudflare
            delayBeforeCheck: 90
            resolvers:
              - "1.1.1.1:53"
              - "8.8.8.8:53"
    
    # Persistent storage for ACME certificates
    persistence:
      enabled: true
      size: 128Mi
      path: /data
      storageClass: local-path  # k3s default storage class
    
    # Environment variables for Cloudflare API
    env:
      - name: CF_DNS_API_TOKEN
        valueFrom:
          secretKeyRef:
            name: cloudflare-api-credentials
            key: apitoken
    
    # Additional configuration for tunnel compatibility
    additionalArguments:
      - "--serverstransport.insecureskipverify=true"  # For internal services
      - "--log.level=INFO"
    
    # Global redirect middleware
    providers:
      kubernetesCRD:
        enabled: true
      kubernetesIngress:
        enabled: true
```

> **⚠️ Important**: Update the email address before deployment!

#### 3.3 Update Existing IngressRoutes

Since we're enabling HTTPS, existing HTTP-only IngressRoutes need updates. This affects:

**File: `base-apps/chores-tracker/ingress.yaml`** (Update needed)
```yaml
# Current: HTTP only
entryPoints:
  - web

# Should become: HTTPS with automatic certificates
entryPoints:
  - websecure
tls:
  certResolver: cloudflare
  domains:
    - main: chores.arigsela.com
```

**File: `base-apps/vault/ingress.yaml`** (Update needed)
Similar changes required for vault and any other existing ingress routes.

#### 3.4 Global HTTPS Redirect Middleware (Optional)

**File: `base-apps/traefik-config/middleware.yaml`**
```yaml
apiVersion: traefik.containo.us/v1alpha1
kind: Middleware
metadata:
  name: https-redirect
  namespace: kube-system
spec:
  redirectScheme:
    scheme: https
    permanent: true
```

#### 3.5 Testing Phase 3
```bash
# Commit Phase 3 changes
git add base-apps/traefik-config/helm_chart_config.yaml
git commit -m "feat: Phase 3 - Configure Traefik HTTPS with Cloudflare DNS challenge"
git push origin feature/cloudflare-tunnel-testing

# Test: Verify Traefik configuration is applied
kubectl get helmchartconfig -n kube-system traefik -o yaml
kubectl get pods -n kube-system -l app.kubernetes.io/name=traefik
kubectl describe pods -n kube-system -l app.kubernetes.io/name=traefik

# Check Traefik logs for HTTPS setup
kubectl logs -n kube-system -l app.kubernetes.io/name=traefik --tail=50 | grep -E "(certificate|acme|cloudflare|error)"

# Verify Traefik service has both 80 and 443 ports
kubectl get svc -n kube-system traefik -o wide

# Expected Results:
# - HelmChartConfig should be applied
# - Traefik pods should restart and show websecure port (443) configured
# - Logs should show Cloudflare DNS challenge provider loaded
# - Service should expose both ports 80 and 443

# If Traefik dashboard is accessible, verify certificate resolver:
# kubectl port-forward -n kube-system svc/traefik 8080:8080
# Then check http://localhost:8080/dashboard/ for certificate resolvers
```

### Phase 4: Cloudflared Deployment (15 minutes)

#### 4.1 Configuration

**File: `base-apps/cloudflare-tunnel/configmaps.yaml`**
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
        service: https://traefik.kube-system.svc.cluster.local:443
        originRequest:
          noTLSVerify: true
      - hostname: "arigsela.com"
        service: https://traefik.kube-system.svc.cluster.local:443
        originRequest:
          noTLSVerify: true
      - service: http_status:404
```

#### 4.2 Deployment

**File: `base-apps/cloudflare-tunnel/deployments.yaml`**
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: cloudflared
  namespace: cloudflare
  labels:
    app: cloudflared
spec:
  replicas: 2
  selector:
    matchLabels:
      app: cloudflared
  template:
    metadata:
      labels:
        app: cloudflared
    spec:
      serviceAccountName: external-secrets-sa
      containers:
      - name: cloudflared
        image: cloudflare/cloudflared:latest
        args:
        - tunnel
        - --config
        - /etc/cloudflared/config.yml
        - --metrics
        - 0.0.0.0:2000
        - run
        env:
        - name: TUNNEL_TOKEN
          valueFrom:
            secretKeyRef:
              name: tunnel-credentials
              key: token
        livenessProbe:
          httpGet:
            path: /ready
            port: 2000
          failureThreshold: 1
          initialDelaySeconds: 10
          periodSeconds: 10
        readinessProbe:
          httpGet:
            path: /ready
            port: 2000
          initialDelaySeconds: 5
          periodSeconds: 5
        volumeMounts:
        - name: config
          mountPath: /etc/cloudflared
        - name: creds
          mountPath: /etc/cloudflared/creds
          readOnly: true
        resources:
          requests:
            memory: "128Mi"
            cpu: "100m"
          limits:
            memory: "256Mi"
            cpu: "500m"
      volumes:
      - name: config
        configMap:
          name: cloudflared-config
      - name: creds
        secret:
          secretName: tunnel-credentials
```

#### 4.3 Service

**File: `base-apps/cloudflare-tunnel/services.yaml`**
```yaml
apiVersion: v1
kind: Service
metadata:
  name: cloudflared
  namespace: cloudflare
  labels:
    app: cloudflared
spec:
  selector:
    app: cloudflared
  ports:
  - name: metrics
    port: 2000
    targetPort: 2000
    protocol: TCP
  type: ClusterIP
```

### Phase 5: Test Application (10 minutes)

#### 5.1 Deployment

**File: `base-apps/whoami-test/deployments.yaml`**
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: whoami
  namespace: default
spec:
  replicas: 2
  selector:
    matchLabels:
      app: whoami
  template:
    metadata:
      labels:
        app: whoami
    spec:
      containers:
      - name: whoami
        image: traefik/whoami
        ports:
        - containerPort: 80
        resources:
          requests:
            memory: "64Mi"
            cpu: "50m"
          limits:
            memory: "128Mi"
            cpu: "100m"
```

#### 5.2 Service

**File: `base-apps/whoami-test/services.yaml`**
```yaml
apiVersion: v1
kind: Service
metadata:
  name: whoami
  namespace: default
spec:
  selector:
    app: whoami
  ports:
  - port: 80
    targetPort: 80
```

#### 5.3 Ingress

**File: `base-apps/whoami-test/ingress.yaml`**
```yaml
apiVersion: traefik.containo.us/v1alpha1
kind: IngressRoute
metadata:
  name: whoami
  namespace: default
spec:
  entryPoints:
    - websecure
  routes:
    - match: Host(`whoami.arigsela.com`)
      kind: Rule
      services:
        - name: whoami
          port: 80
  tls:
    certResolver: cloudflare
    domains:
      - main: whoami.arigsela.com
```

## GitOps Deployment with Branch-Based Testing

### Pre-Deployment Checklist

1. **✅ Prepare Vault Secrets**
   ```bash
   # Access your Vault instance and add secrets at path: secret/cloudflare
   # Required keys:
   # - api_token: YOUR_CLOUDFLARE_API_TOKEN
   # - tunnel_token: YOUR_TUNNEL_TOKEN
   ```

2. **✅ Email Configuration** (Already configured: arigsela@gmail.com)

3. **✅ Verify ArgoCD is Running**
   ```bash
   kubectl get pods -n argo-cd
   ```

### Branch-Based Testing Strategy

Each phase will be implemented and tested on a feature branch before merging to main:

```bash
# Create feature branch for testing
git checkout -b feature/cloudflare-tunnel-testing

# After each phase implementation:
# 1. Commit changes to feature branch
# 2. Update ArgoCD ApplicationSet to use feature branch
# 3. Test functionality
# 4. Rollback if issues found, or proceed to next phase
# 5. Merge to main when all phases complete successfully
```

### Phase Testing Workflow

#### Phase 1 & 2 Testing: SecretStore and External Secrets
```bash
# Commit Phase 1 & 2 changes
git add base-apps/cloudflare-tunnel.yaml base-apps/traefik-config.yaml base-apps/whoami-test.yaml
git add base-apps/vault/secret_stores.yaml
git add base-apps/cloudflare-tunnel/external_secrets.yaml
git commit -m "feat: Phase 1-2 - ArgoCD apps and External Secrets setup"
git push origin feature/cloudflare-tunnel-testing

# Update ArgoCD ApplicationSet to use testing branch
# (Manually update targetRevision: feature/cloudflare-tunnel-testing)

# Test Phase 1-2
kubectl get applications -n argo-cd | grep -E "(cloudflare|traefik|whoami)"
kubectl get externalsecrets -n cloudflare
kubectl get secrets -n cloudflare
```

#### Phase 3 Testing: Traefik HTTPS Configuration
```bash
# Commit Phase 3 changes  
git add base-apps/traefik-config/helm_chart_config.yaml
git commit -m "feat: Phase 3 - Traefik HTTPS/TLS with Cloudflare DNS challenge"
git push origin feature/cloudflare-tunnel-testing

# Test Phase 3
kubectl get helmchartconfig -n kube-system traefik
kubectl logs -n kube-system -l app.kubernetes.io/name=traefik --tail=50
kubectl get pods -n kube-system -l app.kubernetes.io/name=traefik
# Check Traefik dashboard access (if exposed)
```

#### Phase 4 Testing: Cloudflared Deployment
```bash
# Commit Phase 4 changes
git add base-apps/cloudflare-tunnel/
git commit -m "feat: Phase 4 - Cloudflared deployment with tunnel configuration"
git push origin feature/cloudflare-tunnel-testing

# Test Phase 4
kubectl get pods -n cloudflare
kubectl logs -n cloudflare -l app=cloudflared --tail=50
kubectl get services -n cloudflare
# Check tunnel connection status
```

#### Phase 5 Testing: Test Application
```bash
# Commit Phase 5 changes
git add base-apps/whoami-test/
git commit -m "feat: Phase 5 - Whoami test application with HTTPS ingress"
git push origin feature/cloudflare-tunnel-testing

# Test Phase 5
kubectl get pods -n default -l app=whoami
kubectl get ingressroute -n default whoami
# Test internal access: curl https://whoami.arigsela.com (from cluster)
```

#### Final Integration Testing
```bash
# Test end-to-end functionality
# 1. External access test (from different network/mobile data)
curl https://whoami.arigsela.com

# 2. Certificate validation
openssl s_client -connect whoami.arigsela.com:443 -servername whoami.arigsela.com

# 3. Tunnel connectivity check
kubectl logs -n cloudflare -l app=cloudflared | grep "Connection registered"

# 4. ArgoCD application health
kubectl get applications -n argo-cd -o wide
```

### Rollback Strategy

If any phase fails during testing:

```bash
# Quick rollback - revert ArgoCD to main branch
# Update ApplicationSet targetRevision back to: main

# Or rollback specific commits
git revert <commit-hash>
git push origin feature/cloudflare-tunnel-testing

# For complete rollback
git checkout main
git branch -D feature/cloudflare-tunnel-testing
# ArgoCD will sync back to main branch state
```

### Merge to Production

Only after all phases test successfully:

```bash
# Switch to main and merge
git checkout main
git merge feature/cloudflare-tunnel-testing
git push origin main

# Update ArgoCD ApplicationSet back to main branch
# Clean up feature branch
git branch -d feature/cloudflare-tunnel-testing
git push origin --delete feature/cloudflare-tunnel-testing
```

### Post-Deployment Verification

```bash
# Check ArgoCD applications (if ArgoCD CLI is available)
# argocd app list
# argocd app get cloudflare-tunnel
# argocd app get traefik-config
# argocd app get whoami-test

# Verify deployments
kubectl get applications -n argo-cd | grep -E "(cloudflare|traefik|whoami)"
kubectl get pods -n cloudflare
kubectl get pods -n default -l app=whoami
```

## Verification and Testing

### Phase 6: Internal Cluster Testing (5 minutes)

```bash
# Check cloudflare tunnel pods
kubectl get pods -n cloudflare
kubectl logs -n cloudflare -l app=cloudflared --tail=50

# Verify external secrets are working
kubectl get externalsecrets -n cloudflare
kubectl get secrets -n cloudflare

# Check test application
kubectl get pods -n default -l app=whoami
kubectl get ingressroute -n default
```

### Phase 7: DNS Configuration (Manual - 10 minutes)

#### 7.1 Cloudflare Dashboard Configuration
1. Navigate to your Cloudflare dashboard
2. Go to DNS section
3. Add CNAME records:
   ```
   Name: whoami
   Target: TUNNEL_ID.cfargotunnel.com
   Proxy status: Proxied (orange cloud)
   
   Name: * (for wildcard)
   Target: TUNNEL_ID.cfargotunnel.com
   Proxy status: Proxied (orange cloud)
   ```

#### 7.2 Router/Local DNS Configuration
1. Access your router's admin panel
2. Find DNS/DHCP settings
3. Add custom DNS entries:
   ```
   *.arigsela.com → YOUR_K3S_NODE_IP
   arigsela.com → YOUR_K3S_NODE_IP
   ```

### Phase 8: End-to-End Testing (10 minutes)

```bash
# Test external access (use mobile data or different network)
curl https://whoami.arigsela.com

# Test local access (from your local network)
curl https://whoami.arigsela.com

# Expected response should include container information
```

## Troubleshooting

### Common Issues and Solutions

#### 1. Tunnel Not Connecting
**Symptoms**: Cloudflared pods not showing "Connection registered"
```bash
# Debug steps
kubectl logs -n cloudflare -l app=cloudflared
kubectl describe pods -n cloudflare
kubectl get secrets -n cloudflare tunnel-credentials -o yaml
```

**Solutions**:
- Verify tunnel token is correct in Vault
- Check External Secrets sync status
- Ensure outbound port 443 is open

#### 2. Certificate Issues
**Symptoms**: SSL/TLS errors or certificate warnings
```bash
# Debug steps
kubectl logs -n kube-system -l app.kubernetes.io/name=traefik
kubectl get secrets -n kube-system cloudflare-api-credentials -o yaml
```

**Solutions**:
- Verify Cloudflare API token permissions
- Check email address in Traefik config
- Ensure DNS challenge is working

#### 3. Too Many Redirects
**Symptoms**: Browser shows "ERR_TOO_MANY_REDIRECTS"

**Solutions**:
- Verify SSL/TLS mode in Cloudflare is "Full (strict)"
- Check Traefik forwarded headers configuration
- Ensure ingress routes are configured correctly

#### 4. External Secrets Not Syncing
```bash
# Debug steps
kubectl get externalsecrets -n cloudflare
kubectl describe externalsecrets -n cloudflare
kubectl logs -n external-secrets -l app.kubernetes.io/name=external-secrets
```

**Solutions**:
- Verify Vault connectivity
- Check service account permissions
- Ensure secret store configuration is correct

### Debugging Commands

```bash
# Check ArgoCD application sync status
kubectl get applications -n argo-cd cloudflare-tunnel -o yaml

# Monitor real-time logs
kubectl logs -n cloudflare -f -l app=cloudflared

# Check all resources in cloudflare namespace
kubectl get all -n cloudflare

# Verify ingress routes
kubectl get ingressroute --all-namespaces
```

## Maintenance and Operations

### Regular Maintenance Tasks

#### Monthly
- Review and rotate Cloudflare API tokens
- Check certificate expiration and renewal
- Monitor tunnel connectivity metrics
- Review resource usage and scaling

#### Quarterly  
- Update cloudflared image versions
- Review and update DNS records
- Audit access logs and security
- Performance optimization review

### Monitoring Integration

Consider adding these monitoring components:

```yaml
# Example ServiceMonitor for Prometheus (if using monitoring stack)
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: cloudflared
  namespace: cloudflare
spec:
  selector:
    matchLabels:
      app: cloudflared
  endpoints:
  - port: metrics
    path: /metrics
```

### Backup and Disaster Recovery

#### Critical Components to Backup
1. Vault secrets (api_token, tunnel_token)
2. Traefik certificate storage
3. DNS configurations
4. Git repository state

#### Recovery Process
1. Restore Git repository
2. Ensure Vault secrets are available
3. ArgoCD will automatically redeploy
4. Verify DNS configurations
5. Test connectivity

### Scaling Considerations

#### Horizontal Scaling
```yaml
# Increase cloudflared replicas for redundancy
spec:
  replicas: 3  # Increase from 2 to 3
```

#### Multi-Region Setup
- Deploy tunnel endpoints in multiple regions
- Use Cloudflare load balancing
- Configure failover scenarios

### Security Best Practices

1. **Secret Rotation**: Regularly rotate API tokens
2. **Access Control**: Use least-privilege RBAC policies
3. **Network Policies**: Restrict inter-namespace communication
4. **Audit Logging**: Enable and monitor access logs
5. **Image Security**: Use specific image tags, not `latest`

## Conclusion

This GitOps implementation provides:

- **Declarative Configuration**: Everything is version-controlled
- **Automated Deployment**: ArgoCD handles all deployments
- **Self-Healing**: Automatic drift detection and correction
- **Scalability**: Easy to replicate for additional services
- **Security**: Integrated secret management with Vault
- **Observability**: Built-in monitoring and logging

The implementation follows our established repository patterns and integrates seamlessly with existing infrastructure components.

---

**Document Version**: 1.0  
**Last Updated**: 2025-08-19  
**Author**: Claude Code Implementation  
**Review Status**: Ready for Implementation