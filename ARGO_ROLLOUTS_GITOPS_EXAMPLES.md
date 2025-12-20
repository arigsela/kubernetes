# ArgoCD with Argo Rollouts GitOps Examples

This document provides examples of how ArgoCD manages Argo Rollouts CRDs in a GitOps workflow, including ApplicationSet and app-of-apps patterns.

## Overview

Argo Rollouts extends Kubernetes with a `Rollout` CRD that replaces the standard `Deployment` resource, providing advanced deployment strategies like blue-green, canary, and progressive delivery.

## Basic Rollout Example

### 1. Simple Canary Rollout

```yaml
# base-apps/my-app/rollout.yaml
apiVersion: argoproj.io/v1alpha1
kind: Rollout
metadata:
  name: my-app
  namespace: my-app
spec:
  replicas: 5
  selector:
    matchLabels:
      app: my-app
  template:
    metadata:
      labels:
        app: my-app
    spec:
      containers:
      - name: my-app
        image: myregistry/my-app:v1.0.0
        ports:
        - containerPort: 8080
  strategy:
    canary:
      steps:
      - setWeight: 20
      - pause: {duration: 1m}
      - setWeight: 40
      - pause: {duration: 1m}
      - setWeight: 60
      - pause: {duration: 1m}
      - setWeight: 80
      - pause: {duration: 1m}
```

### 2. Blue-Green Rollout

```yaml
# base-apps/my-app/rollout-bluegreen.yaml
apiVersion: argoproj.io/v1alpha1
kind: Rollout
metadata:
  name: my-app-bluegreen
  namespace: my-app
spec:
  replicas: 3
  revisionHistoryLimit: 2
  selector:
    matchLabels:
      app: my-app
  template:
    metadata:
      labels:
        app: my-app
    spec:
      containers:
      - name: my-app
        image: myregistry/my-app:v1.0.0
        ports:
        - containerPort: 8080
  strategy:
    blueGreen:
      activeService: my-app-active
      previewService: my-app-preview
      autoPromotionEnabled: false
      scaleDownDelaySeconds: 30
      prePromotionAnalysis:
        templates:
        - templateName: success-rate
        args:
        - name: service-name
          value: my-app-preview
```

## ArgoCD Application for Rollouts

### 1. Single Application with Rollout

```yaml
# base-apps/my-app-rollout.yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: my-app-rollout
  namespace: argo-cd
  finalizers:
    - resources-finalizer.argocd.argoproj.io
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
      - RespectIgnoreDifferences=true
  # Ignore differences in Rollout status during sync
  ignoreDifferences:
  - group: argoproj.io
    kind: Rollout
    jsonPointers:
    - /status
```

### 2. ApplicationSet for Multiple Environments

```yaml
# base-apps/rollouts-appset.yaml
apiVersion: argoproj.io/v1alpha1
kind: ApplicationSet
metadata:
  name: rollouts-apps
  namespace: argo-cd
spec:
  generators:
  - list:
      elements:
      - app: frontend
        namespace: frontend
        environment: dev
      - app: frontend
        namespace: frontend-staging
        environment: staging
      - app: frontend
        namespace: frontend-prod
        environment: prod
      - app: backend
        namespace: backend
        environment: dev
      - app: backend
        namespace: backend-staging
        environment: staging
      - app: backend
        namespace: backend-prod
        environment: prod
  template:
    metadata:
      name: '{{app}}-{{environment}}'
      annotations:
        # For Kargo integration
        kargo.akuity.io/authorized-stage: '{{app}}:{{environment}}'
    spec:
      project: default
      source:
        repoURL: https://github.com/arigsela/kubernetes
        targetRevision: main
        path: 'rollouts/{{app}}/{{environment}}'
      destination:
        server: https://kubernetes.default.svc
        namespace: '{{namespace}}'
      syncPolicy:
        automated:
          prune: true
          selfHeal: true
        syncOptions:
          - CreateNamespace=true
          - RespectIgnoreDifferences=true
      ignoreDifferences:
      - group: argoproj.io
        kind: Rollout
        jsonPointers:
        - /status
```

## App-of-Apps Pattern with Rollouts

### 1. Master App for Rollouts

```yaml
# base-apps/rollouts-master-app.yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: rollouts-master
  namespace: argo-cd
  finalizers:
    - resources-finalizer.argocd.argoproj.io
spec:
  project: default
  source:
    repoURL: https://github.com/arigsela/kubernetes
    targetRevision: main
    path: rollouts/apps
  destination:
    server: https://kubernetes.default.svc
    namespace: argo-cd
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
```

### 2. Child Applications Structure

```yaml
# rollouts/apps/frontend-apps.yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: frontend-rollouts
  namespace: argo-cd
spec:
  project: default
  source:
    repoURL: https://github.com/arigsela/kubernetes
    targetRevision: main
    path: rollouts/frontend/base
  destination:
    server: https://kubernetes.default.svc
    namespace: frontend
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
```

## Advanced Patterns

### 1. Rollout with Analysis Templates

```yaml
# base-apps/my-app/rollout-with-analysis.yaml
apiVersion: argoproj.io/v1alpha1
kind: Rollout
metadata:
  name: my-app-analyzed
  namespace: my-app
spec:
  replicas: 5
  selector:
    matchLabels:
      app: my-app
  template:
    metadata:
      labels:
        app: my-app
    spec:
      containers:
      - name: my-app
        image: myregistry/my-app:v1.0.0
        ports:
        - containerPort: 8080
  strategy:
    canary:
      canaryService: my-app-canary
      stableService: my-app-stable
      trafficRouting:
        nginx:
          stableIngress: my-app-ingress
      steps:
      - setWeight: 20
      - pause: {duration: 1m}
      - analysis:
          templates:
          - templateName: success-rate
          args:
          - name: service-name
            value: my-app-canary
      - setWeight: 40
      - pause: {duration: 1m}
      - analysis:
          templates:
          - templateName: success-rate
      - setWeight: 60
      - pause: {duration: 1m}
      - setWeight: 80
      - pause: {duration: 1m}
---
apiVersion: argoproj.io/v1alpha1
kind: AnalysisTemplate
metadata:
  name: success-rate
  namespace: my-app
spec:
  args:
  - name: service-name
  metrics:
  - name: success-rate
    interval: 30s
    successCondition: result[0] >= 0.95
    failureLimit: 3
    provider:
      prometheus:
        address: http://prometheus:9090
        query: |
          sum(rate(
            http_requests_total{service="{{args.service-name}}",status=~"2.."}[5m]
          )) / 
          sum(rate(
            http_requests_total{service="{{args.service-name}}"}[5m]
          ))
```

### 2. ApplicationSet with Git Generator for Rollouts

```yaml
# base-apps/rollouts-git-appset.yaml
apiVersion: argoproj.io/v1alpha1
kind: ApplicationSet
metadata:
  name: rollouts-git-discovery
  namespace: argo-cd
spec:
  generators:
  - git:
      repoURL: https://github.com/arigsela/kubernetes
      revision: main
      directories:
      - path: rollouts/*
      - path: rollouts/*/base
  template:
    metadata:
      name: '{{path.basename}}'
    spec:
      project: default
      source:
        repoURL: https://github.com/arigsela/kubernetes
        targetRevision: main
        path: '{{path}}'
      destination:
        server: https://kubernetes.default.svc
        namespace: '{{path.basename}}'
      syncPolicy:
        automated:
          prune: true
          selfHeal: true
        syncOptions:
          - CreateNamespace=true
          - RespectIgnoreDifferences=true
      ignoreDifferences:
      - group: argoproj.io
        kind: Rollout
        jsonPointers:
        - /status
```

### 3. Multi-Cluster Rollout with ApplicationSet

```yaml
# base-apps/multi-cluster-rollouts.yaml
apiVersion: argoproj.io/v1alpha1
kind: ApplicationSet
metadata:
  name: multi-cluster-rollouts
  namespace: argo-cd
spec:
  generators:
  - matrix:
      generators:
      - clusters: {}
      - list:
          elements:
          - app: frontend
            namespace: frontend
          - app: backend
            namespace: backend
  template:
    metadata:
      name: '{{name}}-{{app}}'
    spec:
      project: default
      source:
        repoURL: https://github.com/arigsela/kubernetes
        targetRevision: main
        path: 'rollouts/{{app}}/overlays/{{name}}'
      destination:
        server: '{{server}}'
        namespace: '{{namespace}}'
      syncPolicy:
        automated:
          prune: true
          selfHeal: true
        syncOptions:
          - CreateNamespace=true
```

## Integration with Your Repository

To integrate Argo Rollouts into your existing GitOps workflow:

### 1. Install Argo Rollouts Controller

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
    chart: argo-rollouts
    targetRevision: 2.35.0
    helm:
      values: |
        dashboard:
          enabled: true
          ingress:
            enabled: true
            hosts:
              - rollouts.arigsela.com
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

### 2. Convert Existing Deployment to Rollout

To convert your chores-tracker deployment to use Rollouts:

```yaml
# base-apps/chores-tracker/rollout.yaml
apiVersion: argoproj.io/v1alpha1
kind: Rollout
metadata:
  name: chores-tracker
  namespace: chores-tracker
spec:
  replicas: 1
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
        image: 852893458518.dkr.ecr.us-east-2.amazonaws.com/chores-tracker:3.0.0
        imagePullPolicy: IfNotPresent
        ports:
        - containerPort: 8000
        envFrom:
        - configMapRef:
            name: chores-tracker-config
        - secretRef:
            name: chores-tracker-secrets
        readinessProbe:
          httpGet:
            path: /api/v1/healthcheck
            port: 8000
          initialDelaySeconds: 30
          periodSeconds: 5
      imagePullSecrets:
      - name: ecr-registry
  strategy:
    canary:
      stableService: chores-tracker
      canaryService: chores-tracker-canary
      steps:
      - setWeight: 20
      - pause: {duration: 2m}
      - setWeight: 40
      - pause: {duration: 2m}
      - setWeight: 60
      - pause: {duration: 2m}
      - setWeight: 80
      - pause: {duration: 2m}
```

### 3. Update Services for Rollout

```yaml
# base-apps/chores-tracker/services.yaml
apiVersion: v1
kind: Service
metadata:
  name: chores-tracker
  namespace: chores-tracker
spec:
  selector:
    app: chores-tracker
  ports:
    - protocol: TCP
      port: 80
      targetPort: 8000
---
apiVersion: v1
kind: Service
metadata:
  name: chores-tracker-canary
  namespace: chores-tracker
spec:
  selector:
    app: chores-tracker
  ports:
    - protocol: TCP
      port: 80
      targetPort: 8000
```

## Best Practices

1. **Ignore Status Fields**: Always configure ArgoCD to ignore Rollout status fields to prevent sync conflicts
2. **Use Analysis Templates**: Define success metrics for automated rollout decisions
3. **Separate Environments**: Use different strategies for dev/staging (fast rollout) vs production (careful canary)
4. **Monitor Rollouts**: Set up the Argo Rollouts dashboard for visibility
5. **Version Strategy**: Consider using image tags that include git SHA for better traceability
6. **Rollback Plan**: Configure revision history limits and ensure quick rollback capability

## Troubleshooting

### Common Issues

1. **Sync Conflicts**: If ArgoCD shows OutOfSync due to status changes, ensure ignoreDifferences is configured
2. **Traffic Splitting**: Verify your ingress controller supports traffic management (Nginx, Istio, etc.)
3. **Analysis Failures**: Check Prometheus queries and ensure metrics are available
4. **Image Pull Errors**: Verify ECR credentials are properly configured for Rollout pods

### Useful Commands

```bash
# Check rollout status
kubectl argo rollouts get rollout my-app -n my-app

# Promote a paused rollout
kubectl argo rollouts promote my-app -n my-app

# Abort and rollback
kubectl argo rollouts abort my-app -n my-app

# View rollout dashboard
kubectl argo rollouts dashboard
```

## References

- [Argo Rollouts Documentation](https://argoproj.github.io/argo-rollouts/)
- [ArgoCD ApplicationSet Documentation](https://argo-cd.readthedocs.io/en/stable/operator-manual/applicationset/)
- [GitOps Progressive Delivery](https://www.weave.works/blog/progressive-delivery-with-argo-rollouts)