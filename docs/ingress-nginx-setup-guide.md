# Nginx Ingress Controller Setup Guide

This document explains how the nginx ingress controller is configured in our K3s cluster and how to expose services to the internet through pfSense.

## Architecture Overview

Our setup uses:
- **K3s Cluster**: 4 nodes on 10.0.1.x network
- **Nginx Ingress Controller**: DaemonSet with hostPort binding
- **pfSense Router**: Port forwarding from public IP to cluster
- **ArgoCD**: GitOps deployment management

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

## pfSense Configuration

### Port Forward Rules

**HTTP (Port 80):**
- Interface: WAN
- Protocol: TCP
- Source: Any
- Destination: WAN Address
- Destination Port: 80
- Redirect Target IP: 10.0.1.110
- Redirect Target Port: 80

**HTTPS (Port 443):**
- Interface: WAN
- Protocol: TCP
- Source: Any
- Destination: WAN Address
- Destination Port: 443
- Redirect Target IP: 10.0.1.110
- Redirect Target Port: 443

### Firewall Rules
pfSense automatically creates corresponding WAN firewall rules for the port forwards.

## DNS Configuration

External DNS points to your public IP:
```bash
$ nslookup whoami.arigsela.com
Name: whoami.arigsela.com
Address: 73.7.190.154
```

## Request Flow

```
Internet → pfSense (73.7.190.154:80) → K3s Node (10.0.1.110:80) → Nginx Ingress → Application Pod
```

1. **External Request**: Client makes request to `whoami.arigsela.com`
2. **DNS Resolution**: Resolves to public IP `73.7.190.154`
3. **pfSense NAT**: Port forward redirects to `10.0.1.110:80`
4. **Nginx Ingress**: hostPort binding receives request on node
5. **Ingress Routing**: Routes based on Host header to target service
6. **Pod Response**: Application pod processes and responds

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

### Example Ingress Manifest
```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: my-app-ingress
  namespace: my-app
  annotations:
    kubernetes.io/ingress.class: nginx
spec:
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

### Common Issues

**1. Ingress not responding**
```bash
# Check nginx controller pods
kubectl get pods -n ingress-nginx

# Check logs
kubectl logs -n ingress-nginx deployment/ingress-nginx-controller
```

**2. 404 errors**
```bash
# Verify ingress resource exists
kubectl get ingress --all-namespaces

# Check ingress details
kubectl describe ingress <ingress-name> -n <namespace>
```

**3. pfSense connectivity**
```bash
# Test node connectivity from pfSense
ping 10.0.1.110

# Test hostPort binding
telnet 10.0.1.110 80
```

**4. DNS issues**
```bash
# Verify DNS resolution
nslookup myapp.arigsela.com

# Test with IP directly
curl -H "Host: myapp.arigsela.com" http://73.7.190.154
```

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
For HTTPS, configure cert-manager with Let's Encrypt or use pfSense SSL offloading.

## Security Notes

- Ingress controller has appropriate RBAC permissions
- Only ports 80/443 exposed externally via pfSense
- Internal cluster communication secured by Kubernetes network policies
- ArgoCD manages deployments with GitOps for audit trail