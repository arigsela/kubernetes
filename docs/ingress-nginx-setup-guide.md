# Nginx Ingress Controller Setup Guide

This document explains how the nginx ingress controller is configured in our K3s cluster and how to expose services to the internet through pfSense.

## Architecture Overview

Our setup uses:
- **K3s Cluster**: 4 nodes on 10.0.1.x network
- **Nginx Ingress Controller**: DaemonSet with hostPort binding
- **pfSense Router**: Port forwarding from public IP to cluster (behind ISP modem)
- **ArgoCD**: GitOps deployment management
- **cert-manager**: Automated SSL certificate management with Let's Encrypt
- **Route 53**: DNS-01 challenge provider for reliable certificate issuance

## Current Configuration

### Cluster Nodes
```
k3s-master:   10.0.1.110
k3s-worker-1: 10.0.1.111
k3s-worker-2: 10.0.1.112
k3s-worker-3: 10.0.1.113
```

### Ingress Controller Setup

The nginx ingress controller is deployed as a DaemonSet with hostPort configuration:

```yaml
ports:
- containerPort: 80
  hostPort: 80
  name: http
  protocol: TCP
- containerPort: 443
  hostPort: 443
  name: https
  protocol: TCP
- containerPort: 8443
  hostPort: 8443
  name: webhook
  protocol: TCP
```

This means nginx binds directly to ports 80/443 on each cluster node.

### Service Configuration
```bash
# Ingress controller runs as ClusterIP service
kubectl get svc -n ingress-nginx
NAME                                 TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)          AGE
ingress-nginx-controller             ClusterIP   10.43.213.227   <none>        80/TCP,443/TCP   5h15m
```

## Network Architecture

### Double NAT Setup
Our network uses a double NAT configuration:
```
Internet (73.7.190.154) → ISP Modem → pfSense (192.168.0.2) → K3s Cluster (10.0.1.x)
```

**ISP Modem Configuration:**
- Forwards ports 80/443 from public IP to pfSense (192.168.0.2)

### pfSense Configuration

#### Port Forward Rules (Critical Settings)

**HTTP (Port 80):**
- Interface: WAN
- Protocol: TCP
- Source: Any
- **Source Ports: Any** ⚠️ **Critical: Must be "Any", not specific ports**
- **Destination: Any** ⚠️ **Critical: Not "WAN address" - causes connectivity issues**
- Destination Port: 80 (HTTP)
- Redirect Target IP: 10.0.1.110
- Redirect Target Port: 80 (HTTP)
- **NAT Reflection: Disable** (conflicts with "Any" destination)

**HTTPS (Port 443):**
- Interface: WAN
- Protocol: TCP
- Source: Any
- **Source Ports: Any** ⚠️ **Critical: Must be "Any", not specific ports**
- **Destination: Any** ⚠️ **Critical: Not "WAN address" - causes connectivity issues**
- Destination Port: 443 (HTTPS)
- Redirect Target IP: 10.0.1.110
- Redirect Target Port: 443 (HTTPS)
- **NAT Reflection: Disable** (conflicts with "Any" destination)

#### Firewall Rules
pfSense automatically creates corresponding WAN firewall rules. Ensure they are:
- **Enabled** (green checkmarks)
- **Above any deny rules** in the rule order
- **Action: Pass** (not block)

## DNS Configuration

External DNS points to your public IP:
```bash
$ nslookup whoami.arigsela.com
Name: whoami.arigsela.com
Address: 73.7.190.154
```

## Request Flow

```
Internet → ISP Modem (73.7.190.154) → pfSense (192.168.0.2) → K3s Node (10.0.1.110) → Nginx Ingress → Application Pod
```

1. **External Request**: Client makes request to `whoami.arigsela.com`
2. **DNS Resolution**: Resolves to public IP `73.7.190.154`
3. **ISP Modem**: Forwards traffic to pfSense `192.168.0.2`
4. **pfSense NAT**: Port forward redirects to `10.0.1.110:80/443`
5. **Nginx Ingress**: hostPort binding receives request on node
6. **Ingress Routing**: Routes based on Host header to target service
7. **Pod Response**: Application pod processes and responds

## SSL Certificate Management

### cert-manager Setup

We use **cert-manager v1.18.2** with **Let's Encrypt** and **DNS-01 challenges** via **Route 53** for reliable certificate issuance.

#### ClusterIssuer Configuration

**Production ClusterIssuer (letsencrypt-prod):**
```yaml
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt-prod
spec:
  acme:
    server: https://acme-v02.api.letsencrypt.org/directory
    email: admin@arigsela.com
    privateKeySecretRef:
      name: letsencrypt-prod
    solvers:
    - dns01:
        route53:
          region: us-east-1
          accessKeyID: YOUR_AWS_ACCESS_KEY_ID
          secretAccessKeySecretRef:
            name: route53-credentials
            key: secret-access-key
```

**Staging ClusterIssuer (for testing):**
```yaml
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt-staging
spec:
  acme:
    server: https://acme-staging-v02.api.letsencrypt.org/directory
    email: admin@arigsela.com
    privateKeySecretRef:
      name: letsencrypt-staging
    solvers:
    - dns01:
        route53:
          region: us-east-1
          accessKeyID: YOUR_AWS_ACCESS_KEY_ID
          secretAccessKeySecretRef:
            name: route53-credentials
            key: secret-access-key
```

#### AWS Route 53 Credentials

Create a secret in the `cert-manager` namespace:
```yaml
apiVersion: v1
kind: Secret
metadata:
  name: route53-credentials
  namespace: cert-manager
type: Opaque
stringData:
  secret-access-key: "YOUR_AWS_SECRET_ACCESS_KEY"
```

**Required IAM permissions:**
```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "route53:GetChange",
                "route53:ChangeResourceRecordSets",
                "route53:ListHostedZonesByName"
            ],
            "Resource": [
                "arn:aws:route53:::hostedzone/*",
                "arn:aws:route53:::change/*"
            ]
        }
    ]
}
```

### Why DNS-01 vs HTTP-01?

**DNS-01 Advantages:**
- ✅ Works behind NAT/firewall (no incoming HTTP connections required)
- ✅ More reliable for home/residential setups
- ✅ Supports wildcard certificates (`*.domain.com`)
- ✅ No dependency on web server accessibility

**HTTP-01 Limitations:**
- ❌ Requires public HTTP accessibility (port 80)
- ❌ Can fail with complex network setups
- ❌ ISP blocking can prevent validation
- ❌ No wildcard certificate support

## Testing the Setup

### Internal Testing (from cluster network)
```bash
# Test ingress service directly
curl -H "Host: whoami.arigsela.com" http://10.43.213.227

# Test via node hostPort
curl -H "Host: whoami.arigsela.com" http://10.0.1.110
```

### External Testing (from internet)
```bash
# Test via public DNS
curl http://whoami.arigsela.com

# Test via public IP with Host header
curl -H "Host: whoami.arigsela.com" http://73.7.190.154
```

### Expected Response
A successful response shows:
```
Hostname: whoami-79dd455449-2xjdg
IP: 127.0.0.1
IP: ::1
IP: 10.42.1.198
RemoteAddr: 10.42.0.1:53620
GET / HTTP/1.1
Host: whoami.arigsela.com
X-Forwarded-For: 73.7.190.154
X-Real-Ip: 73.7.190.154
```

**Key indicators of success:**
- `RemoteAddr: 10.42.0.1` - Request from nginx ingress pod
- `Host: whoami.arigsela.com` - Proper header forwarding
- `X-Forwarded-For` - Contains original client IP

## Deploying New Ingress Resources

### Example Ingress Manifest (with SSL)
```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: my-app-ingress
  namespace: my-app
  annotations:
    # Use production ClusterIssuer for trusted certificates
    cert-manager.io/cluster-issuer: "letsencrypt-prod"
    # Force HTTPS redirects
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
    nginx.ingress.kubernetes.io/force-ssl-redirect: "true"
    # Backend protocol
    nginx.ingress.kubernetes.io/backend-protocol: "HTTP"
spec:
  ingressClassName: nginx
  # TLS configuration for automatic certificate management
  tls:
  - hosts:
    - myapp.arigsela.com
    secretName: my-app-tls  # cert-manager will create this secret
  rules:
  - host: myapp.arigsela.com
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: my-app-service
            port:
              number: 80
```

### Example Ingress Manifest (HTTP only)
```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: my-app-ingress-http
  namespace: my-app
  annotations:
    # Disable SSL redirects for HTTP-only
    nginx.ingress.kubernetes.io/ssl-redirect: "false"
    nginx.ingress.kubernetes.io/force-ssl-redirect: "false"
    nginx.ingress.kubernetes.io/backend-protocol: "HTTP"
spec:
  ingressClassName: nginx
  rules:
  - host: myapp.arigsela.com
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: my-app-service
            port:
              number: 80
```

### ArgoCD Application
```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: my-app
  namespace: argo-cd
spec:
  project: default
  source:
    repoURL: https://github.com/arigsela/kubernetes
    targetRevision: main
    path: base-apps/my-app
  destination:
    server: https://kubernetes.default.svc
    namespace: my-app
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
```

## Troubleshooting

### Network Connectivity Issues

**1. Complete connectivity loss (HTTP and HTTPS timeout)**

**Symptoms:**
- `telnet domain.com 80` and `telnet domain.com 443` both timeout
- Internal cluster connectivity works fine
- pfSense shows traffic as blocked in firewall logs

**Root Cause:** pfSense port forwarding misconfiguration

**Solution:** Check pfSense NAT rules:
```bash
# Firewall > NAT > Port Forward
# Critical settings that MUST be correct:
```
- **Source Ports: Any** (not specific ports)
- **Destination: Any** (not "WAN address")
- **NAT Reflection: Disable** (conflicts with "Any" destination)
- **Firewall rules must be enabled and above deny rules**

**2. HTTPS timeouts but HTTP works**

**Symptoms:**
- `telnet domain.com 80` works
- `telnet domain.com 443` times out
- HTTP shows 308 redirect to HTTPS

**Root Cause:** Port 443 not properly forwarded or nginx not binding to hostPort 443

**Diagnostics:**
```bash
# On K3s master node, verify nginx is listening on 443
ss -tulpn | grep :443

# Should show nginx processes bound to 443
# If not listening, check nginx controller configuration
kubectl get pod -n ingress-nginx -o yaml | grep -A10 -B5 hostPort
```

**3. Double NAT configuration issues**

**Symptoms:**
- pfSense shows WAN IP as private (192.168.x.x) instead of public IP
- External connectivity fails despite correct pfSense config

**Solution:** Configure upstream router/modem:
- Put pfSense in **DMZ mode**, OR
- **Bridge mode** to give pfSense the public IP directly, OR
- **Port forward** from router to pfSense (double NAT)

### SSL Certificate Issues

**4. Certificate shows "staging" instead of production**

**Symptoms:**
- HTTPS works but browser shows "Not Secure"
- Certificate issued by "Fake LE Intermediate X1"

**Root Cause:** Using staging ClusterIssuer instead of production

**Solution:**
```bash
# Check which ClusterIssuer is being used
kubectl describe certificate <cert-name> -n <namespace>

# Update ingress annotation to use production issuer
cert-manager.io/cluster-issuer: "letsencrypt-prod"

# Force certificate renewal
kubectl delete certificate <cert-name> -n <namespace>
kubectl delete secret <cert-secret> -n <namespace>
```

**5. DNS-01 challenge failures**

**Symptoms:**
- Certificate stays in "False" ready state
- Challenge shows DNS validation errors

**Diagnostics:**
```bash
# Check challenge status
kubectl get challenges -n <namespace>
kubectl describe challenge <challenge-name> -n <namespace>

# Verify DNS TXT record was created
dig TXT _acme-challenge.domain.com +short

# Check ClusterIssuer status
kubectl describe clusterissuer <issuer-name>
```

**Solution:**
- Verify AWS Route 53 credentials are correct
- Check IAM permissions for Route 53 access
- Ensure domain uses Route 53 nameservers

### General Debugging

**6. Ingress not responding (404/503 errors)**
```bash
# Check nginx controller pods
kubectl get pods -n ingress-nginx

# Check ingress resource exists and is configured
kubectl get ingress --all-namespaces
kubectl describe ingress <ingress-name> -n <namespace>

# Check service endpoints
kubectl get endpoints -n <namespace>
```

**7. DNS resolution issues**
```bash
# Verify DNS points to correct IP
nslookup myapp.arigsela.com

# Test with IP directly (bypasses DNS)
curl -H "Host: myapp.arigsela.com" http://73.7.190.154
```

### pfSense Firewall Log Analysis

**Reading pfSense logs (Status > System Logs > Firewall):**
- **Green checkmark (✓)**: Traffic allowed - good
- **Red X (✗)**: Traffic blocked - investigate why
- **"Default deny rule"**: Traffic hitting default block rule - need specific allow rule

**Common blocked traffic indicators:**
- External IP → `192.168.0.2:80` (TCP-S) - blocked incoming HTTP
- External IP → `192.168.0.2:443` (TCP-S) - blocked incoming HTTPS

**Solution:** Ensure port forward rules create corresponding firewall allow rules

### Useful Commands

```bash
# List all ingresses
kubectl get ingress --all-namespaces

# Check nginx controller status
kubectl get pods -n ingress-nginx -o wide

# View ingress controller logs
kubectl logs -n ingress-nginx -l app.kubernetes.io/name=ingress-nginx

# Test ingress from within cluster
kubectl run test-pod --image=curlimages/curl --restart=Never -- \
  curl -s -H "Host: myapp.arigsela.com" \
  http://ingress-nginx-controller.ingress-nginx.svc.cluster.local
```

## High Availability Considerations

### Load Balancing
For production, consider pfSense load balancing across multiple nodes:
- **System > Routing > Load Balancer**
- Create pool with all node IPs (10.0.1.110-113)
- Update port forwards to use the load balancer pool

### SSL/TLS Termination
We use **cert-manager with Let's Encrypt DNS-01 challenges** for automatic certificate management. This provides:
- Automatic certificate issuance and renewal
- DNS-01 challenges work reliably behind NAT
- Wildcard certificate support
- Industry-standard trusted certificates

## Security Notes

- **Ingress controller** has appropriate RBAC permissions
- **Only ports 80/443** exposed externally via pfSense
- **SSL certificates** automatically managed by cert-manager
- **Route 53 credentials** stored securely in Kubernetes secrets
- **Internal cluster communication** secured by Kubernetes network policies
- **ArgoCD** manages deployments with GitOps for complete audit trail
- **DNS-01 challenges** avoid exposing internal infrastructure to Let's Encrypt validation

## Production Readiness Checklist

### Network Configuration
- [ ] ISP modem forwards ports 80/443 to pfSense
- [ ] pfSense port forwards configured with "Any" destination and source ports
- [ ] pfSense firewall rules enabled and above deny rules
- [ ] NAT reflection disabled on port forward rules
- [ ] External DNS points to correct public IP

### SSL Certificate Management
- [ ] cert-manager v1.18.2+ deployed
- [ ] Route 53 credentials configured in cert-manager namespace
- [ ] Production ClusterIssuer configured and ready
- [ ] Staging ClusterIssuer available for testing
- [ ] IAM permissions configured for Route 53 DNS challenges

### Ingress Configuration
- [ ] nginx ingress controller deployed as DaemonSet
- [ ] hostPort binding enabled for ports 80/443
- [ ] Ingress resources use production ClusterIssuer
- [ ] SSL redirects configured appropriately
- [ ] Certificate secrets created automatically

### Monitoring & Maintenance
- [ ] ArgoCD sync policies configured for automatic deployment
- [ ] Certificate expiration monitoring (cert-manager handles auto-renewal)
- [ ] pfSense firewall log monitoring
- [ ] nginx controller log monitoring
- [ ] DNS-01 challenge monitoring for failures