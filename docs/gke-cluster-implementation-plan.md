# GKE Cluster Implementation Plan for Chores Tracker Application

## Overview
This document outlines the implementation plan for deploying a cost-optimized Google Kubernetes Engine (GKE) cluster to host the chores-tracker application and make it accessible at chores.arigsela.com. The implementation focuses on minimizing costs while maintaining reliability and performance.

## Architecture Overview

### Core Components
- **GKE Standard Cluster**: Regional cluster in us-central1
- **Node Pools**: Mixed strategy with spot instances for cost optimization
- **Ingress**: NGINX Ingress Controller with single Load Balancer
- **Container Registry**: Google Artifact Registry
- **DNS**: Integration with existing AWS Route53 domain
- **CI/CD**: ArgoCD for GitOps-based deployments

### Cost Optimization Strategy
1. **Spot VMs**: 60-80% cost reduction on compute resources
2. **Single Load Balancer**: Minimize networking costs with NGINX Ingress
3. **Regional Cluster**: Leverage free tier benefits for first zonal cluster
4. **Auto-scaling**: Scale down during low-traffic periods
5. **Appropriate Sizing**: Use e2-series instances for better price/performance

## Technical Specifications

### GKE Cluster Configuration
```hcl
# Primary cluster configuration
cluster_name     = "chores-tracker-cluster"
location        = "us-central1"  # Regional for HA
gke_version     = "1.29"         # Latest stable
network_mode    = "VPC_NATIVE"
release_channel = "REGULAR"

# Features
workload_identity    = true
horizontal_pod_autoscaling = true
http_load_balancing = false  # Using NGINX instead
network_policy      = true
```

### Node Pool Specifications

#### System Node Pool
- **Purpose**: Core Kubernetes system components
- **Instance Type**: e2-small (2 vCPU, 2GB RAM)
- **Count**: 1-2 nodes
- **Preemptible**: No (ensures system stability)
- **Components**: kube-system, ingress-controller, cert-manager

#### Application Node Pool
- **Purpose**: Application workloads
- **Instance Type**: e2-medium (2 vCPU, 4GB RAM)
- **Count**: 1-5 nodes (auto-scaling)
- **Spot Instances**: Yes (60-80% cost savings)
- **Components**: Applications, ArgoCD, monitoring

### Networking Architecture
```yaml
networking:
  vpc:
    name: "gke-vpc"
    cidr: "10.0.0.0/16"
  
  subnets:
    primary:
      name: "gke-subnet"
      cidr: "10.0.1.0/24"
      region: "us-central1"
    
    secondary_ranges:
      pods:
        name: "gke-pods"
        cidr: "10.1.0.0/16"
      services:
        name: "gke-services"
        cidr: "10.2.0.0/16"
  
  load_balancer:
    type: "EXTERNAL"
    static_ip: true
    ports: [80, 443]
```

## Implementation Phases

### Phase 1: Foundation Setup (Day 1-2)

#### 1.1 Google Cloud Project Setup
- [ ] Create new GCP project or use existing
- [ ] Enable required APIs:
  - Kubernetes Engine API
  - Compute Engine API
  - Artifact Registry API
  - Cloud DNS API
  - Secret Manager API
- [ ] Set up billing alerts at $50 and $100

#### 1.2 Terraform Backend Configuration
- [ ] Create GCS bucket for Terraform state
- [ ] Configure backend encryption
- [ ] Set up state locking with Cloud Storage

#### 1.3 Service Account Setup
- [ ] Create Terraform service account
- [ ] Assign required IAM roles:
  - Kubernetes Engine Admin
  - Compute Admin
  - Service Account User
  - Artifact Registry Admin

### Phase 2: Core Infrastructure (Day 3-4)

#### 2.1 VPC and Networking
```hcl
module "vpc" {
  source = "./modules/networking"
  
  project_id   = var.project_id
  region       = "us-central1"
  network_name = "gke-vpc"
  
  subnets = [{
    subnet_name   = "gke-subnet"
    subnet_ip     = "10.0.1.0/24"
    subnet_region = "us-central1"
  }]
  
  secondary_ranges = {
    "gke-subnet" = [
      {
        range_name    = "gke-pods"
        ip_cidr_range = "10.1.0.0/16"
      },
      {
        range_name    = "gke-services"
        ip_cidr_range = "10.2.0.0/16"
      }
    ]
  }
}
```

#### 2.2 GKE Cluster Creation
```hcl
module "gke" {
  source = "./modules/gke-cluster"
  
  project_id     = var.project_id
  name           = "chores-tracker-cluster"
  location       = "us-central1"
  node_locations = ["us-central1-a", "us-central1-b"]
  
  network    = module.vpc.network_name
  subnetwork = module.vpc.subnets["gke-subnet"].name
  
  ip_range_pods     = "gke-pods"
  ip_range_services = "gke-services"
  
  # Enable Workload Identity
  workload_identity_config = {
    workload_pool = "${var.project_id}.svc.id.goog"
  }
  
  # Remove default node pool
  remove_default_node_pool = true
  initial_node_count       = 1
  
  # Cluster autoscaling
  cluster_autoscaling = {
    enabled = true
    resource_limits = [
      {
        resource_type = "cpu"
        minimum       = 2
        maximum       = 20
      },
      {
        resource_type = "memory"
        minimum       = 8
        maximum       = 80
      }
    ]
  }
}
```

#### 2.3 Node Pool Configuration
```hcl
# System Node Pool
module "system_node_pool" {
  source = "./modules/gke-node-pool"
  
  name       = "system-pool"
  cluster    = module.gke.cluster_id
  location   = module.gke.location
  
  node_count = 1
  
  autoscaling = {
    min_node_count = 1
    max_node_count = 2
  }
  
  node_config = {
    machine_type = "e2-small"
    
    labels = {
      pool = "system"
    }
    
    taint = [{
      key    = "system"
      value  = "true"
      effect = "NO_SCHEDULE"
    }]
  }
}

# Application Node Pool (Spot Instances)
module "app_node_pool" {
  source = "./modules/gke-node-pool"
  
  name       = "app-spot-pool"
  cluster    = module.gke.cluster_id
  location   = module.gke.location
  
  node_count = 1
  
  autoscaling = {
    min_node_count = 1
    max_node_count = 5
  }
  
  node_config = {
    machine_type = "e2-medium"
    spot         = true  # Enable spot instances
    
    labels = {
      pool = "application"
      type = "spot"
    }
    
    oauth_scopes = [
      "https://www.googleapis.com/auth/cloud-platform"
    ]
  }
}
```

### Phase 3: Container Registry & IAM (Day 5)

#### 3.1 Artifact Registry Setup
```hcl
module "artifact_registry" {
  source = "./modules/artifact-registry"
  
  project_id    = var.project_id
  location      = "us-central1"
  repository_id = "chores-tracker"
  description   = "Docker repository for chores tracker applications"
  format        = "DOCKER"
}
```

#### 3.2 Workload Identity Configuration
```hcl
# Service Account for workloads
resource "google_service_account" "workload_identity" {
  account_id   = "chores-tracker-wi"
  display_name = "Chores Tracker Workload Identity"
}

# Bind to Kubernetes Service Account
resource "google_service_account_iam_binding" "workload_identity_binding" {
  service_account_id = google_service_account.workload_identity.name
  role               = "roles/iam.workloadIdentityUser"
  
  members = [
    "serviceAccount:${var.project_id}.svc.id.goog[chores-tracker/chores-tracker]"
  ]
}
```

### Phase 4: Kubernetes Components (Day 6-7)

#### 4.1 NGINX Ingress Controller
```yaml
# nginx-ingress-values.yaml
controller:
  service:
    type: LoadBalancer
    loadBalancerIP: ${STATIC_IP}
    annotations:
      cloud.google.com/load-balancer-type: "External"
  
  resources:
    requests:
      cpu: 100m
      memory: 128Mi
    limits:
      cpu: 500m
      memory: 512Mi
  
  autoscaling:
    enabled: true
    minReplicas: 2
    maxReplicas: 4
    targetCPUUtilizationPercentage: 80
  
  nodeSelector:
    pool: system
  
  tolerations:
  - key: system
    operator: Equal
    value: "true"
    effect: NoSchedule
```

Installation:
```bash
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm install nginx-ingress ingress-nginx/ingress-nginx \
  -f nginx-ingress-values.yaml \
  --namespace ingress-nginx \
  --create-namespace
```

#### 4.2 Cert-Manager Configuration
```yaml
# cert-manager-values.yaml
installCRDs: true

resources:
  requests:
    cpu: 50m
    memory: 64Mi

nodeSelector:
  pool: system

tolerations:
- key: system
  operator: Equal
  value: "true"
  effect: NoSchedule
```

Let's Encrypt Issuer:
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
    - http01:
        ingress:
          class: nginx
```

#### 4.3 ArgoCD Installation
```yaml
# argocd-values.yaml
server:
  service:
    type: ClusterIP
  
  ingress:
    enabled: true
    ingressClassName: nginx
    hosts:
      - argocd.chores.arigsela.com
    tls:
      - secretName: argocd-tls
        hosts:
          - argocd.chores.arigsela.com
  
  resources:
    requests:
      cpu: 100m
      memory: 128Mi

controller:
  resources:
    requests:
      cpu: 250m
      memory: 256Mi

redis:
  resources:
    requests:
      cpu: 100m
      memory: 128Mi
```

### Phase 5: DNS Configuration (Day 8)

#### 5.1 Static IP Reservation
```hcl
resource "google_compute_global_address" "ingress_ip" {
  name = "chores-tracker-ip"
}

output "ingress_ip" {
  value = google_compute_global_address.ingress_ip.address
}
```

#### 5.2 DNS Record Configuration
Two options:

**Option A: Cloud DNS (Recommended)**
```hcl
resource "google_dns_managed_zone" "chores_zone" {
  name     = "chores-arigsela-com"
  dns_name = "chores.arigsela.com."
}

resource "google_dns_record_set" "chores_a" {
  name = "chores.arigsela.com."
  type = "A"
  ttl  = 300
  
  managed_zone = google_dns_managed_zone.chores_zone.name
  rrdatas      = [google_compute_global_address.ingress_ip.address]
}
```

**Option B: AWS Route53 Integration**
```hcl
# In AWS Provider configuration
resource "aws_route53_record" "chores" {
  zone_id = var.route53_zone_id
  name    = "chores.arigsela.com"
  type    = "A"
  ttl     = 300
  records = [google_compute_global_address.ingress_ip.address]
}
```

### Phase 6: Application Migration (Day 9-10)

#### 6.1 Container Image Migration
```bash
# Pull from ECR
docker pull 852893458518.dkr.ecr.us-east-2.amazonaws.com/chores-tracker:5.2.0

# Tag for Artifact Registry
docker tag 852893458518.dkr.ecr.us-east-2.amazonaws.com/chores-tracker:5.2.0 \
  us-central1-docker.pkg.dev/${PROJECT_ID}/chores-tracker/chores-tracker:5.2.0

# Push to Artifact Registry
docker push us-central1-docker.pkg.dev/${PROJECT_ID}/chores-tracker/chores-tracker:5.2.0
```

#### 6.2 Update Kubernetes Manifests

**Deployment Updates:**
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: chores-tracker
  namespace: chores-tracker
spec:
  replicas: 2
  selector:
    matchLabels:
      app: chores-tracker
  template:
    metadata:
      labels:
        app: chores-tracker
    spec:
      serviceAccountName: chores-tracker
      containers:
      - name: chores-tracker
        image: us-central1-docker.pkg.dev/${PROJECT_ID}/chores-tracker/chores-tracker:5.2.0
        ports:
        - containerPort: 8000
        resources:
          requests:
            cpu: 100m
            memory: 256Mi
          limits:
            cpu: 500m
            memory: 512Mi
        envFrom:
        - configMapRef:
            name: chores-tracker-config
        - secretRef:
            name: chores-tracker-secrets
      nodeSelector:
        pool: application
```

**Ingress Configuration:**
```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: chores-tracker
  namespace: chores-tracker
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
spec:
  ingressClassName: nginx
  tls:
  - hosts:
    - chores.arigsela.com
    secretName: chores-tls
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
      - path: /
        pathType: Prefix
        backend:
          service:
            name: chores-tracker-frontend
            port:
              number: 80
```

#### 6.3 ArgoCD Application Configuration
```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: chores-tracker
  namespace: argocd
spec:
  project: default
  source:
    repoURL: https://github.com/arigsela/gcp-infrastructure
    targetRevision: main
    path: kubernetes/apps/chores-tracker
  destination:
    server: https://kubernetes.default.svc
    namespace: chores-tracker
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
    - CreateNamespace=true
```

### Phase 7: Testing & Validation (Day 11)

#### 7.1 Cluster Health Checks
```bash
# Check cluster status
gcloud container clusters describe chores-tracker-cluster \
  --region us-central1

# Verify node pools
kubectl get nodes -L pool

# Check pod distribution
kubectl get pods -A -o wide
```

#### 7.2 Application Testing
```bash
# Test internal connectivity
kubectl run test-pod --image=curlimages/curl -it --rm -- \
  curl http://chores-tracker.chores-tracker.svc.cluster.local:80/health

# Test external access
curl -I https://chores.arigsela.com/health

# Check SSL certificate
openssl s_client -connect chores.arigsela.com:443 -servername chores.arigsela.com
```

#### 7.3 Auto-scaling Validation
```bash
# Generate load
kubectl run -it --rm load-generator --image=busybox /bin/sh
# Inside the pod:
while true; do wget -q -O- http://chores-tracker.chores-tracker.svc.cluster.local; done

# Monitor scaling
kubectl get hpa -w
kubectl get nodes -w
```

## Cost Monitoring & Optimization

### Estimated Monthly Costs
| Component | Configuration | Estimated Cost |
|-----------|--------------|----------------|
| GKE Management | Regional cluster | $0 (free tier) |
| System Nodes | 1-2 e2-small | $15-30 |
| App Nodes (Spot) | 1-5 e2-medium spot | $10-25 |
| Load Balancer | 1 regional LB | $18 |
| Static IP | 1 reserved IP | $7 |
| Artifact Registry | ~10GB storage | $1 |
| Network Egress | ~50GB/month | $5 |
| **Total** | | **$56-86/month** |

### Cost Optimization Checklist
- [x] Use spot instances for non-critical workloads
- [x] Implement cluster auto-scaling
- [x] Use single ingress controller
- [x] Leverage free tier services
- [x] Set up billing alerts
- [x] Use regional cluster vs zonal
- [x] Optimize container image sizes
- [x] Implement resource quotas

### Monitoring Setup
```yaml
# Resource quotas per namespace
apiVersion: v1
kind: ResourceQuota
metadata:
  name: compute-quota
  namespace: chores-tracker
spec:
  hard:
    requests.cpu: "2"
    requests.memory: 4Gi
    limits.cpu: "4"
    limits.memory: 8Gi
    persistentvolumeclaims: "2"
```

## Repository Structure

```
gcp-infrastructure/
├── terraform/
│   ├── environments/
│   │   ├── dev/
│   │   │   ├── main.tf
│   │   │   ├── variables.tf
│   │   │   ├── terraform.tfvars
│   │   │   └── backend.tf
│   │   └── prod/
│   │       ├── main.tf
│   │       ├── variables.tf
│   │       ├── terraform.tfvars
│   │       └── backend.tf
│   ├── modules/
│   │   ├── gke-cluster/
│   │   │   ├── main.tf
│   │   │   ├── variables.tf
│   │   │   ├── outputs.tf
│   │   │   └── versions.tf
│   │   ├── gke-node-pool/
│   │   │   ├── main.tf
│   │   │   ├── variables.tf
│   │   │   └── outputs.tf
│   │   ├── networking/
│   │   │   ├── main.tf
│   │   │   ├── variables.tf
│   │   │   ├── outputs.tf
│   │   │   └── firewall.tf
│   │   ├── artifact-registry/
│   │   │   ├── main.tf
│   │   │   ├── variables.tf
│   │   │   └── outputs.tf
│   │   └── iam/
│   │       ├── main.tf
│   │       ├── variables.tf
│   │       └── outputs.tf
│   └── bootstrap/
│       └── backend.tf
├── kubernetes/
│   ├── base/
│   │   ├── argocd/
│   │   │   ├── namespace.yaml
│   │   │   ├── values.yaml
│   │   │   └── kustomization.yaml
│   │   ├── nginx-ingress/
│   │   │   ├── namespace.yaml
│   │   │   ├── values.yaml
│   │   │   └── kustomization.yaml
│   │   ├── cert-manager/
│   │   │   ├── namespace.yaml
│   │   │   ├── values.yaml
│   │   │   ├── cluster-issuer.yaml
│   │   │   └── kustomization.yaml
│   │   └── monitoring/
│   │       ├── namespace.yaml
│   │       └── kustomization.yaml
│   └── apps/
│       ├── chores-tracker/
│       │   ├── deployment.yaml
│       │   ├── service.yaml
│       │   ├── ingress.yaml
│       │   ├── configmap.yaml
│       │   ├── secrets.yaml
│       │   └── kustomization.yaml
│       └── chores-tracker-frontend/
│           ├── deployment.yaml
│           ├── service.yaml
│           └── kustomization.yaml
├── scripts/
│   ├── setup.sh
│   ├── deploy.sh
│   └── migrate-images.sh
└── docs/
    ├── README.md
    ├── ARCHITECTURE.md
    ├── RUNBOOK.md
    └── TROUBLESHOOTING.md
```

## Security Considerations

### Network Security
- Private GKE cluster with public endpoint
- Network policies for pod-to-pod communication
- Firewall rules restricting access
- SSL/TLS encryption for all external traffic

### Identity & Access
- Workload Identity for pod authentication
- Least privilege IAM roles
- Service account per application
- RBAC for Kubernetes access control

### Secrets Management
- Google Secret Manager integration
- External Secrets Operator for synchronization
- Encrypted secrets in Git (Sealed Secrets optional)
- Regular rotation of credentials

### Image Security
- Private Artifact Registry
- Vulnerability scanning enabled
- Binary Authorization (optional)
- Distroless base images recommended

## Backup & Disaster Recovery

### Backup Strategy
- GKE cluster configuration in Git
- Persistent volume snapshots
- Database backups (if applicable)
- Artifact Registry replication

### Disaster Recovery Plan
1. **Cluster Recovery**: Terraform apply from Git
2. **Application Recovery**: ArgoCD sync
3. **Data Recovery**: Restore from snapshots
4. **DNS Failover**: Update Route53 if needed

## Maintenance & Operations

### Regular Maintenance Tasks
- Weekly: Review cluster metrics and costs
- Monthly: Update container images
- Quarterly: GKE version upgrades
- Annually: Security audit and review

### Monitoring & Alerting
- Google Cloud Monitoring for infrastructure
- Prometheus/Grafana for application metrics
- Log aggregation with Cloud Logging
- Alerts for:
  - High resource utilization
  - Pod failures
  - Certificate expiration
  - Cost threshold breaches

## Rollback Procedures

### Application Rollback
```bash
# Via ArgoCD
argocd app rollback chores-tracker <revision>

# Via kubectl
kubectl rollout undo deployment/chores-tracker -n chores-tracker
```

### Infrastructure Rollback
```bash
# Via Terraform
cd terraform/environments/prod
terraform plan -target=module.gke
terraform apply -target=module.gke
```

## Success Criteria

- [ ] Cluster successfully provisioned
- [ ] Applications accessible at chores.arigsela.com
- [ ] SSL certificates working
- [ ] Auto-scaling functional
- [ ] Costs within $60/month budget
- [ ] GitOps pipeline operational
- [ ] Monitoring and alerting configured
- [ ] Documentation complete

## Timeline

| Phase | Duration | Target Completion |
|-------|----------|------------------|
| Phase 1: Foundation | 2 days | Day 2 |
| Phase 2: Core Infrastructure | 2 days | Day 4 |
| Phase 3: Registry & IAM | 1 day | Day 5 |
| Phase 4: Kubernetes Components | 2 days | Day 7 |
| Phase 5: DNS Configuration | 1 day | Day 8 |
| Phase 6: Application Migration | 2 days | Day 10 |
| Phase 7: Testing & Validation | 1 day | Day 11 |
| **Total** | **11 days** | |

## Next Steps

1. Review and approve this implementation plan
2. Create GCP project and enable billing
3. Set up Terraform Cloud or local backend
4. Begin Phase 1 implementation
5. Schedule daily progress reviews

---

**Document Version**: 1.0
**Last Updated**: 2025-08-28
**Author**: Infrastructure Team
**Status**: Draft - Pending Review