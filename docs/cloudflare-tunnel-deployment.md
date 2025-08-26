# Cloudflare Tunnel Deployment Documentation

## Overview

This document provides comprehensive documentation for our Cloudflare tunnel deployment within the k3s cluster. The tunnel enables secure external access to internal applications without requiring port forwarding or firewall changes, using Cloudflare's global network as a secure ingress proxy.

## Architecture Overview

```
External User → Cloudflare Edge → Cloudflare Tunnel → Traefik Ingress → Kubernetes Service → Application Pod
```

### Traffic Flow Diagram

```
[Internet] 
    ↓ HTTPS Request to *.arigsela.com
[Cloudflare Global Network]
    ↓ DNS Resolution (CNAME → tunnel ID)
[Cloudflare Tunnel Service] 
    ↓ Encrypted tunnel connection
[Cloudflared Pod in k3s cluster]
    ↓ Routes to internal service
[Traefik Ingress Controller]
    ↓ TLS termination & routing
[Kubernetes Services]
    ↓ Load balancing
[Application Pods]
```

## Deployment Components

### 1. Cloudflared Daemon
- **Location**: `base-apps/cloudflare-tunnel/`
- **Namespace**: `cloudflare`
- **Purpose**: Establishes secure tunnel connection to Cloudflare

### 2. Traefik Configuration
- **Location**: `base-apps/traefik-config/`
- **Namespace**: `kube-system`
- **Purpose**: HTTPS ingress controller with automatic TLS certificates

### 3. External Secrets Integration
- **Location**: Vault backend secrets management
- **Purpose**: Secure credential distribution across namespaces

## Detailed Component Analysis

### Cloudflared Configuration

**File**: `base-apps/cloudflare-tunnel/configmaps.yaml`

```yaml
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

**Key Features**:
- Wildcard routing for all subdomains (`*.arigsela.com`)
- Root domain handling (`arigsela.com`)
- Internal routing to Traefik on port 443
- TLS verification disabled for internal cluster communication

### Traefik HTTPS Configuration

**File**: `base-apps/traefik-config/helm_chart_config.yaml`

**Entry Points**:
- **web** (port 80): Redirects all HTTP traffic to HTTPS
- **websecure** (port 443): Handles HTTPS traffic with TLS termination

**Certificate Management**:
- Automatic TLS certificates via ACME protocol
- Cloudflare DNS challenge for validation
- Persistent storage for certificate data

**Cloudflare Integration**:
- Trusts Cloudflare IP ranges for forwarded headers
- Uses Cloudflare API for DNS challenges
- Environment variable injection for API credentials

### External Secrets Management

**Files**: 
- `base-apps/cloudflare-tunnel/external_secrets.yaml`
- `base-apps/traefik-config/external_secrets.yaml`

**Credentials Managed**:
1. **Tunnel Credentials**: Authentication token for cloudflared
2. **Cloudflare API Credentials**: DNS challenge authentication for Traefik

## Traffic Flow Detailed Analysis

### 1. External Request Initiation
```
User Browser → https://chores.arigsela.com
```

### 2. DNS Resolution
```
chores.arigsela.com → CNAME → 38066f51-d41a-41be-a9e6-e1c19c1e0776.cfargotunnel.com
```

### 3. Cloudflare Edge Processing
- Request hits Cloudflare's global network
- Identifies tunnel destination
- Encrypts and forwards to tunnel endpoint

### 4. Tunnel Ingress
```
Cloudflare Edge → Encrypted Tunnel → cloudflared pod (cloudflare namespace)
```

### 5. Internal Cluster Routing
```
cloudflared → https://traefik.kube-system.svc.cluster.local:443
```

### 6. Traefik Processing
- Receives encrypted HTTPS request
- Matches hostname against IngressRoute rules
- Performs TLS termination
- Routes to appropriate service

### 7. Service Discovery
```
Traefik → chores-tracker service (port 80) → chores-tracker pod
```

## Application Integration Examples

### Chores Tracker Application

**File**: `base-apps/chores-tracker/ingress.yaml`

```yaml
apiVersion: traefik.containo.us/v1alpha1
kind: IngressRoute
metadata:
  name: chores-tracker
  namespace: chores-tracker
spec:
  entryPoints:
    - websecure
  routes:
  - match: Host(`chores.arigsela.com`)
    kind: Rule
    services:
    - name: chores-tracker
      port: 80
  tls:
    certResolver: cloudflare
    domains:
      - main: chores.arigsela.com
```

**Integration Points**:
- Uses `websecure` entry point (HTTPS only)
- Hostname matching for `chores.arigsela.com`
- Automatic TLS certificate from Cloudflare resolver
- Routes to internal service on port 80

### Vault Application

**File**: `base-apps/vault/ingress.yaml`

Similar pattern with `vault.arigsela.com` hostname and port 8200.

### Test Application (whoami)

**File**: `base-apps/whoami-test/ingress.yaml`

Simple test service for validating tunnel functionality.

## Security Considerations

### 1. Encrypted Tunnel
- All traffic between Cloudflare and cluster is encrypted
- No inbound firewall rules required
- Outbound HTTPS connection only (port 443)

### 2. TLS Termination
- End-to-end encryption maintained
- Automatic certificate renewal
- Strong cipher suites enforced

### 3. Access Control
- Can be enhanced with Cloudflare Access policies
- Rate limiting available at Cloudflare edge
- DDoS protection included

### 4. Secret Management
- Credentials stored in Vault
- Automatic secret rotation capability
- Cross-namespace secret distribution

## Monitoring and Troubleshooting

### Cloudflared Health Checks

```bash
# Check tunnel status
kubectl get pods -n cloudflare

# View tunnel logs
kubectl logs -n cloudflare deployment/cloudflared

# Check tunnel metrics
kubectl port-forward -n cloudflare svc/cloudflared-metrics 2000:2000
curl http://localhost:2000/metrics
```

### Traefik Dashboard Access

```bash
# Port forward to Traefik dashboard
kubectl port-forward -n kube-system svc/traefik 9000:9000
# Access: http://localhost:9000/dashboard/
```

### Certificate Status

```bash
# Check certificate resolver status
kubectl logs -n kube-system deployment/traefik | grep -i acme
```

### DNS Verification

```bash
# Verify DNS resolution
nslookup chores.arigsela.com
dig chores.arigsela.com CNAME
```

## Disaster Recovery

### Tunnel Recreation
1. Backup tunnel credentials from Vault
2. Recreate tunnel in Cloudflare dashboard if needed
3. Update tunnel ID in DNS records
4. Restart cloudflared deployment

### Certificate Recovery
- Certificates auto-renew via ACME
- Persistent storage maintains certificate state
- Manual renewal possible via Traefik dashboard

## Performance Optimization

### Connection Pooling
- Cloudflared maintains persistent connections
- Multiple tunnel replicas for high availability
- Automatic failover between tunnel instances

### Caching
- Static assets cached at Cloudflare edge
- Origin cache headers respected
- Custom cache rules configurable

## Scalability

### Horizontal Scaling
```yaml
# Increase cloudflared replicas
spec:
  replicas: 4  # Current configuration
```

### Geographic Distribution
- Cloudflare automatically routes to nearest edge
- Global anycast network reduces latency
- Automatic DDoS mitigation

## Future Enhancements

### 1. Cloudflare Access Integration
- Zero Trust application access
- Identity-based access control
- Multi-factor authentication

### 2. Advanced Routing
- Path-based routing rules
- Geographic routing policies
- A/B testing capabilities

### 3. Monitoring Integration
- Prometheus metrics collection
- Grafana dashboard creation
- Alert manager configuration

## Deployment History

### Current Tunnel Details
- **Tunnel ID**: `38066f51-d41a-41be-a9e6-e1c19c1e0776`
- **Tunnel Name**: `homelab-k3s`
- **Active Connections**: 4 replicas
- **Deployment Date**: August 2025

### DNS Records Configured
- `chores.arigsela.com` → CNAME → `{tunnel-id}.cfargotunnel.com`
- `vault.arigsela.com` → CNAME → `{tunnel-id}.cfargotunnel.com`
- `whoami.arigsela.com` → CNAME → `{tunnel-id}.cfargotunnel.com`

## Maintenance Tasks

### Regular Tasks
1. Monitor tunnel health and connection count
2. Verify certificate renewals
3. Review Cloudflare analytics
4. Update cloudflared image versions

### Quarterly Tasks
1. Review access patterns and security
2. Evaluate performance metrics
3. Update documentation
4. Test disaster recovery procedures

---

*This document serves as the definitive reference for our Cloudflare tunnel implementation and should be updated whenever changes are made to the tunnel configuration or routing rules.*