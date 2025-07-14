# Argo Rollouts Blue/Green Implementation Guide

This guide details how to implement Argo Rollouts for blue/green deployments in our existing GitOps infrastructure using ArgoCD.

## Table of Contents
- [Overview](#overview)
- [Prerequisites](#prerequisites)
- [Architecture](#architecture)
- [Installation](#installation)
- [Converting Applications](#converting-applications)
- [Database Strategy](#database-strategy)
- [Monitoring and Rollback](#monitoring-and-rollback)
- [Best Practices](#best-practices)

## Overview

Argo Rollouts extends Kubernetes with advanced deployment strategies (blue/green, canary) while maintaining our GitOps principles. ArgoCD will manage the Rollout Custom Resources from Git, while the Argo Rollouts controller handles the actual deployment orchestration.

### Key Benefits
- Zero-downtime deployments (except 1-minute database switchover)
- Automated rollback on failures
- Integration with existing ArgoCD setup
- Progressive delivery with analysis
- GitOps-compliant workflow

## Prerequisites

- Existing ArgoCD installation (✓ Already in place)
- Kubernetes cluster access (✓ Already configured)
- External Secrets Operator (✓ Already installed)
- MySQL database (✓ Currently self-hosted)

## Architecture

### Current State
```
Git Repository → ArgoCD → Deployment → Pods → MySQL (self-hosted)
```

### Target State
```
Git Repository → ArgoCD → Rollout CRD → Blue/Green ReplicaSets → MySQL (with blue/green strategy)
```

## Installation

### Step 1: Install Argo Rollouts Controller

Create the Argo Rollouts application in ArgoCD:

```yaml
# base-apps/argo-rollouts.yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: argo-rollouts
  namespace: argo-cd
spec:
  project: default
  source:
    repoURL: https://argoproj.github.io/argo-helm
    targetRevision: 2.35.1
    chart: argo-rollouts
    helm:
      values: |
        controller:
          metrics:
            enabled: true
            serviceMonitor:
              enabled: true
        dashboard:
          enabled: true
          service:
            type: ClusterIP
  destination:
    server: https://kubernetes.default.svc
    namespace: argo-rollouts
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
    - CreateNamespace=true
```

Commit and push to trigger ArgoCD sync:
```bash
git add base-apps/argo-rollouts.yaml
git commit -m "Install Argo Rollouts controller"
git push origin main
```

### Step 2: Install Argo Rollouts CRDs

The CRDs are included in the Helm chart, but if needed separately:

```yaml
# base-apps/argo-rollouts-crds.yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: argo-rollouts-crds
  namespace: argo-cd
spec:
  project: default
  source:
    repoURL: https://github.com/argoproj/argo-rollouts
    targetRevision: v1.6.4
    path: manifests/crds
  destination:
    server: https://kubernetes.default.svc
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
```

## Converting Applications

### Example: Converting chores-tracker to Blue/Green

#### Step 1: Create Service Definitions

Replace the existing service with active and preview services:

```yaml
# base-apps/chores-tracker/services.yaml
---
apiVersion: v1
kind: Service
metadata:
  name: chores-tracker-active
  namespace: chores-tracker
spec:
  type: ClusterIP
  selector:
    app: chores-tracker
  ports:
    - port: 80
      targetPort: 8080
      protocol: TCP
---
apiVersion: v1
kind: Service
metadata:
  name: chores-tracker-preview
  namespace: chores-tracker
spec:
  type: ClusterIP
  selector:
    app: chores-tracker
  ports:
    - port: 80
      targetPort: 8080
      protocol: TCP
```

#### Step 2: Convert Deployment to Rollout

Replace `deployments.yaml` with `rollout.yaml`:

```yaml
# base-apps/chores-tracker/rollout.yaml
apiVersion: argoproj.io/v1alpha1
kind: Rollout
metadata:
  name: chores-tracker
  namespace: chores-tracker
spec:
  replicas: 3
  strategy:
    blueGreen:
      # Active service receives production traffic
      activeService: chores-tracker-active
      # Preview service for testing new version
      previewService: chores-tracker-preview
      # Manual promotion for safety
      autoPromotionEnabled: false
      # Keep old version for 5 minutes after promotion
      scaleDownDelaySeconds: 300
      # Optional: Run analysis before promotion
      prePromotionAnalysis:
        templates:
        - templateName: chores-tracker-analysis
        args:
        - name: service-name
          value: chores-tracker-preview.chores-tracker.svc.cluster.local
  selector:
    matchLabels:
      app: chores-tracker
  template:
    metadata:
      labels:
        app: chores-tracker
    spec:
      containers:
      - name: chores-tracker
        image: chores-tracker:3.0.0
        ports:
        - containerPort: 8080
        env:
        - name: DATABASE_URL
          valueFrom:
            secretKeyRef:
              name: chores-tracker-secrets
              key: DATABASE_URL
        - name: DB_PASSWORD
          valueFrom:
            secretKeyRef:
              name: chores-tracker-secrets
              key: DB_PASSWORD
        - name: SECRET_KEY
          valueFrom:
            secretKeyRef:
              name: chores-tracker-secrets
              key: SECRET_KEY
        envFrom:
        - configMapRef:
            name: chores-tracker-config
        livenessProbe:
          httpGet:
            path: /health
            port: 8080
          initialDelaySeconds: 30
          periodSeconds: 10
        readinessProbe:
          httpGet:
            path: /ready
            port: 8080
          initialDelaySeconds: 5
          periodSeconds: 5
        resources:
          requests:
            memory: "128Mi"
            cpu: "100m"
          limits:
            memory: "512Mi"
            cpu: "500m"
```

#### Step 3: Create Analysis Template

Define success criteria for automated promotion:

```yaml
# base-apps/chores-tracker/analysis-template.yaml
apiVersion: argoproj.io/v1alpha1
kind: AnalysisTemplate
metadata:
  name: chores-tracker-analysis
  namespace: chores-tracker
spec:
  metrics:
  - name: success-rate
    interval: 5m
    successCondition: result[0] >= 0.95
    failureLimit: 3
    provider:
      prometheus:
        address: http://prometheus-server.monitoring.svc.cluster.local:80
        query: |
          sum(rate(
            http_requests_total{
              service="{{args.service-name}}",
              status=~"2.."
            }[5m]
          )) / 
          sum(rate(
            http_requests_total{
              service="{{args.service-name}}"
            }[5m]
          ))
  - name: error-rate
    interval: 5m
    successCondition: result[0] <= 0.05
    failureLimit: 3
    provider:
      prometheus:
        address: http://prometheus-server.monitoring.svc.cluster.local:80
        query: |
          sum(rate(
            http_requests_total{
              service="{{args.service-name}}",
              status=~"5.."
            }[5m]
          )) / 
          sum(rate(
            http_requests_total{
              service="{{args.service-name}}"
            }[5m]
          ))
```

#### Step 4: Update ArgoCD Application

Modify the ArgoCD application to handle Rollout resources:

```yaml
# base-apps/chores-tracker.yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: chores-tracker
  namespace: argo-cd
spec:
  project: default
  source:
    repoURL: https://github.com/arigsela/kubernetes
    targetRevision: main
    path: base-apps/chores-tracker
  destination:
    server: https://kubernetes.default.svc
    namespace: chores-tracker
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
    - CreateNamespace=true
  # Important: Ignore dynamic fields that Rollouts controller manages
  ignoreDifferences:
  - group: argoproj.io
    kind: Rollout
    jsonPointers:
    - /status
    - /spec/replicas
  - group: argoproj.io
    kind: AnalysisRun
    jsonPointers:
    - /status
  - group: argoproj.io
    kind: Experiment
    jsonPointers:
    - /status
```

#### Step 5: Update Ingress/Routes

Update ingress to point to the active service:

```yaml
# base-apps/chores-tracker/ingress.yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: chores-tracker
  namespace: chores-tracker
  annotations:
    nginx.ingress.kubernetes.io/rewrite-target: /
spec:
  rules:
  - host: chores-tracker.example.com
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: chores-tracker-active  # Points to active service
            port:
              number: 80
```

## Database Strategy

### Option 1: Separate Database Instances (Recommended for Testing)

Create separate database configurations for blue and green environments:

```yaml
# base-apps/chores-tracker/external_secrets.yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: chores-tracker-secrets
  namespace: chores-tracker
spec:
  refreshInterval: 15s
  secretStoreRef:
    name: vault-secret-store
    kind: SecretStore
  target:
    name: chores-tracker-secrets
    creationPolicy: Owner
  data:
  # Production database
  - secretKey: DATABASE_URL
    remoteRef:
      key: chores-tracker
      property: DATABASE_URL
  # Preview database (for blue/green testing)
  - secretKey: DATABASE_URL_PREVIEW
    remoteRef:
      key: chores-tracker
      property: DATABASE_URL_PREVIEW
  - secretKey: DB_PASSWORD
    remoteRef:
      key: chores-tracker
      property: DB_PASSWORD
  - secretKey: SECRET_KEY
    remoteRef:
      key: chores-tracker
      property: SECRET_KEY
```

### Option 2: Database Sync Script

Create a CronJob to sync data from production to preview database:

```yaml
# base-apps/chores-tracker/db-sync-job.yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: chores-db-sync
  namespace: chores-tracker
spec:
  schedule: "0 */6 * * *"  # Every 6 hours
  jobTemplate:
    spec:
      template:
        spec:
          containers:
          - name: db-sync
            image: mysql:5.7
            command:
            - /bin/bash
            - -c
            - |
              # Dump production database
              mysqldump -h mysql.mysql.svc.cluster.local \
                -u root -p$MYSQL_ROOT_PASSWORD \
                chores-db > /tmp/dump.sql
              
              # Restore to preview database
              mysql -h mysql-preview.mysql.svc.cluster.local \
                -u root -p$MYSQL_ROOT_PASSWORD \
                chores-db-preview < /tmp/dump.sql
            env:
            - name: MYSQL_ROOT_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: mysql-credentials
                  key: mysql-root-password
          restartPolicy: OnFailure
```

## Deployment Workflow

### 1. Initial Setup
```bash
# Remove old deployment references
rm base-apps/chores-tracker/deployments.yaml

# Add new files
git add base-apps/chores-tracker/rollout.yaml
git add base-apps/chores-tracker/services.yaml
git add base-apps/chores-tracker/analysis-template.yaml
git commit -m "Convert chores-tracker to Argo Rollouts blue/green"
git push origin main
```

### 2. Deploying Updates
```bash
# Update image version in rollout.yaml
sed -i 's/chores-tracker:3.0.0/chores-tracker:3.1.0/g' base-apps/chores-tracker/rollout.yaml
git add base-apps/chores-tracker/rollout.yaml
git commit -m "Update chores-tracker to v3.1.0"
git push origin main
```

### 3. Monitoring Rollout Status
```bash
# Watch rollout status
kubectl argo rollouts get rollout chores-tracker -n chores-tracker -w

# Check analysis runs
kubectl get analysisrun -n chores-tracker

# View dashboard (requires port-forward)
kubectl port-forward -n argo-rollouts svc/argo-rollouts-dashboard 3100:3100
```

### 4. Manual Promotion
```bash
# After testing preview environment
kubectl argo rollouts promote chores-tracker -n chores-tracker
```

### 5. Emergency Rollback
```bash
# Abort and rollback to previous version
kubectl argo rollouts abort chores-tracker -n chores-tracker
```

## Monitoring and Rollback

### Prometheus Metrics

Argo Rollouts exposes metrics that should be scraped:

```yaml
# Metrics available at :8090/metrics
rollout_info
rollout_info_replicas_available
rollout_info_replicas_unavailable
rollout_phase
rollout_events_total
analysis_run_info
analysis_run_metric_phase
```

### Automated Rollback Conditions

The rollout will automatically rollback if:
- Analysis metrics fail (success rate < 95%)
- Error rate exceeds 5%
- Pods fail to become ready
- Manual abort is triggered

## Best Practices

### 1. GitOps Compliance
- All changes go through Git
- ArgoCD manages the desired state
- Rollouts controller manages the actual state

### 2. Testing Strategy
- Always test in preview environment first
- Use analysis templates for automated validation
- Keep manual promotion for critical services

### 3. Database Considerations
- For non-critical services: Use single database with careful testing
- For critical services: Use separate preview database
- Plan for 1-minute write downtime during database switches

### 4. Resource Management
- Set appropriate resource requests/limits
- Use horizontal pod autoscaling with Rollouts
- Monitor resource usage during blue/green transitions

### 5. Communication
- Notify teams before major deployments
- Document rollback procedures
- Keep runbooks updated

## Troubleshooting

### Common Issues

1. **Rollout Stuck in Progressing**
   ```bash
   # Check pods
   kubectl get pods -n chores-tracker -l app=chores-tracker
   
   # Check events
   kubectl describe rollout chores-tracker -n chores-tracker
   ```

2. **Analysis Failing**
   ```bash
   # Check analysis run details
   kubectl describe analysisrun -n chores-tracker
   
   # Verify Prometheus queries
   kubectl port-forward -n monitoring svc/prometheus-server 9090:80
   ```

3. **ArgoCD Sync Issues**
   ```bash
   # Force sync
   argocd app sync chores-tracker
   
   # Check application details
   argocd app get chores-tracker
   ```

## Next Steps

1. **Phase 1**: Install Argo Rollouts controller
2. **Phase 2**: Convert chores-tracker to blue/green
3. **Phase 3**: Set up monitoring and alerts
4. **Phase 4**: Document runbooks and train team
5. **Phase 5**: Extend to other applications

## References

- [Argo Rollouts Documentation](https://argoproj.github.io/rollouts/)
- [GitOps with ArgoCD](https://argo-cd.readthedocs.io/)
- [Blue/Green Deployment Best Practices](https://argoproj.github.io/rollouts/features/bluegreen/)