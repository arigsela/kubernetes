# JupyterLab Workspace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy a single-workspace JupyterLab at `jupyter.arigsela.com`, usable from a browser and from Claude Code via `/api/kernels`, with a scoped S3 scratch bucket and no access to the rest of the cluster.

**Architecture:** One `Deployment` (upstream `quay.io/jupyter/scipy-notebook`, digest-pinned) behind the existing Istio Gateway, authenticated by a Vault-issued Jupyter token layered under the gateway's IP allow-list. A separate Crossplane-managed Application provisions an S3 bucket and a scoped IAM user. A `NetworkPolicy` plus `automountServiceAccountToken: false` confine the pod to local compute, AWS, and the internet.

**Tech Stack:** Kubernetes (k3s), Argo CD, Istio ambient + Gateway API, cert-manager, External Secrets Operator + Vault, Crossplane (provider-aws), Backstage agent-docs contract.

**Spec:** [`docs/superpowers/specs/2026-08-17-jupyter-notebooks-design.md`](../specs/2026-08-17-jupyter-notebooks-design.md)

## Global Constraints

- **No `kubectl apply`.** Every cluster change lands through git; Argo CD syncs it. `kubectl` is for *reading* and for the verification steps only.
- **Namespace:** `jupyter`. **Hostname:** `jupyter.arigsela.com`. **Bucket:** `asela-jupyter-scratch`.
- **TLS issuer MUST be `letsencrypt-route53`.** `letsencrypt-prod`'s only solver is `http01.ingress.class=nginx`, which nothing has satisfied since the Istio cutover.
- **Vault KV path:** `k8s-secrets/jupyter`. Vault Kubernetes auth role name MUST equal the namespace: `jupyter`.
- **AWS region for Crossplane resources: `us-east-1`** (matching `agent-audit-aws-infrastructure`; note ECR is `us-east-2` — do not copy that).
- **S3 tag values** allow only letters, digits, spaces and `+ - = . _ : / @`. Parentheses and commas cause `InvalidTag` and leave the bucket stuck `Ready=False`.
- **Node selector:** `node.kubernetes.io/workload: application` on the Deployment, matching other workloads.
- **Image user:** `scipy-notebook` runs as uid `1000` (`jovyan`), gid `100` (`users`). `fsGroup` MUST be `100`.
- **Branch:** work continues on `feature/jupyter-notebooks`, which already contains the spec commit.

---

## File Structure

| File | Responsibility |
|---|---|
| `appsets/managed-apps/jupyter-aws-infrastructure.yaml` | ApplicationSet config for the AWS Application |
| `tests/appset/golden/jupyter-aws-infrastructure.yaml` | Expected rendered Application spec |
| `base-apps/jupyter-aws-infrastructure/s3-bucket.yaml` | Bucket + public-access block + 90-day expiry (no versioning — see Task 1 Step 6) |
| `base-apps/jupyter-aws-infrastructure/iam-user.yaml` | Dedicated IAM service account |
| `base-apps/jupyter-aws-infrastructure/iam-policy.yaml` | Read/write scoped to the one bucket |
| `base-apps/jupyter-aws-infrastructure/iam-policy-attachment.yaml` | Binds policy to user |
| `base-apps/jupyter-aws-infrastructure/access-key.yaml` | Mints key, writes Secret into `jupyter` ns |
| `base-apps/jupyter.yaml` | Argo CD Application (hand-written; needs `directory.exclude`) |
| `base-apps/jupyter/pvc.yaml` | 20Gi `local-path`, `Prune=false` |
| `base-apps/jupyter/deployments.yaml` | The workspace pod and its security posture |
| `base-apps/jupyter/services.yaml` | ClusterIP :8888 |
| `base-apps/jupyter/secret-store.yaml` | Vault SecretStore, role `jupyter` |
| `base-apps/jupyter/external-secret.yaml` | `token` + `github-token` from Vault |
| `base-apps/jupyter/network-policy.yaml` | **The load-bearing control.** Blocks RFC1918 egress |
| `base-apps/jupyter/certificate.yaml` | `jupyter-tls` via `letsencrypt-route53` |
| `base-apps/jupyter/reference-grant.yaml` | Lets the Gateway read `jupyter-tls` cross-namespace |
| `base-apps/jupyter/httproute.yaml` | Routes the hostname to the Service |
| `base-apps/jupyter/{catalog-info.yaml,docs.md,runbook.md}` | agent-docs contract (hand-written) |
| `base-apps/jupyter/{mkdocs.yml,docs/}` | **Generated** by `gen-techdocs.py` — never hand-edit |
| `base-apps/istio-ingress/gateway.yaml` | *Modify:* add `https-jupyter` listener |
| `base-apps/istio-ingress/authorizationpolicy.yaml` | *Modify:* add restricted rule |
| `tests/appset/test_managed_apps.py:14-27` | *Modify:* add to `EXPECTED_APPS` |
| `scripts/agent-docs-scope.txt` | *Modify:* add `jupyter` |

**Note vs. the spec:** the spec's §5 inventory omitted `reference-grant.yaml`. It is required — the Gateway is in `istio-ingress` and the TLS Secret is in `jupyter`, and Gateway API forbids that cross-namespace Secret read without a grant (see `base-apps/n8n/reference-grant.yaml`).

---

### Task 1: AWS infrastructure (bucket + scoped IAM user)

Independently reviewable: a reviewer can accept or reject the IAM scope without any opinion on the workspace itself. Test-first is genuine here — `tests/appset/` asserts config/golden/`EXPECTED_APPS` agree, so the test fails before the config exists.

**Files:**
- Create: `tests/appset/golden/jupyter-aws-infrastructure.yaml`
- Create: `appsets/managed-apps/jupyter-aws-infrastructure.yaml`
- Create: `base-apps/jupyter-aws-infrastructure/{s3-bucket,iam-user,iam-policy,iam-policy-attachment,access-key}.yaml`
- Modify: `tests/appset/test_managed_apps.py:14-27`

**Interfaces:**
- Produces: a Kubernetes `Secret` named `jupyter-s3-creds` in namespace `jupyter`, with keys `username` (AWS access key ID) and `attribute.secret` (AWS secret access key). **There is no `attribute.id` key.** Task 3 consumes this.
- Produces: S3 bucket `asela-jupyter-scratch` in `us-east-1`.

- [ ] **Step 1: Add the app to `EXPECTED_APPS` (the failing test)**

In `tests/appset/test_managed_apps.py`, add `"jupyter-aws-infrastructure",` to the `EXPECTED_APPS` set, keeping alphabetical order (after `"ecr-auth",`).

- [ ] **Step 2: Run the tests to verify they fail**

```bash
python3 -m pytest tests/appset/ -q
```

Expected: FAIL. `test_expected_configs_present` reports the config-directory stems do not equal `EXPECTED_APPS`, and `test_golden_equivalence` reports the golden set differs.

- [ ] **Step 3: Write the ApplicationSet config**

Create `appsets/managed-apps/jupyter-aws-infrastructure.yaml`. All four keys are required; the field is `sourcePath`, **not** `path` (the git files generator injects its own `path` object and would silently shadow it).

```yaml
name: jupyter-aws-infrastructure
sourcePath: base-apps/jupyter-aws-infrastructure
# The AccessKey's connection secret is written into the jupyter namespace,
# where the workspace pod consumes it as AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY.
namespace: jupyter
syncOptions:
  - CreateNamespace=true
  - ServerSideApply=true
```

- [ ] **Step 4: Write the golden**

Create `tests/appset/golden/jupyter-aws-infrastructure.yaml`:

```yaml
destination:
  namespace: jupyter
  server: https://kubernetes.default.svc
project: default
source:
  path: base-apps/jupyter-aws-infrastructure
  repoURL: https://github.com/arigsela/kubernetes
  targetRevision: main
syncPolicy:
  automated:
    prune: true
    selfHeal: true
  syncOptions:
  - CreateNamespace=true
  - ServerSideApply=true
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
python3 -m pytest tests/appset/ -q
```

Expected: PASS.

- [ ] **Step 6: Write the bucket manifests**

Create `base-apps/jupyter-aws-infrastructure/s3-bucket.yaml`:

```yaml
# Scratch bucket for the JupyterLab workspace. Deliberately holds nothing
# anything else depends on: this is the entire AWS blast radius of a pod that
# runs arbitrary Python. Nothing else may be granted access to it, and it must
# not become a dependency of another app.
apiVersion: s3.aws.upbound.io/v1beta1
kind: Bucket
metadata:
  name: asela-jupyter-scratch
  labels:
    app: jupyter
    component: storage
    managed-by: crossplane
spec:
  forProvider:
    region: us-east-1
    tags:
      # S3 tag VALUES allow only letters, digits, spaces and + - = . _ : / @ —
      # NOT parentheses or commas, which fail with InvalidTag and leave the
      # bucket stuck Ready=False.
      Name: "Jupyter workspace scratch bucket"
      Environment: "homelab"
      ManagedBy: "Crossplane"
      Purpose: "Interactive notebook scratch storage"
      CostCenter: "platform"
  providerConfigRef:
    name: default
---
# Block ALL public access. Scratch or not, nothing here is public.
apiVersion: s3.aws.upbound.io/v1beta1
kind: BucketPublicAccessBlock
metadata:
  name: asela-jupyter-scratch-pab
  labels:
    app: jupyter
    managed-by: crossplane
spec:
  forProvider:
    region: us-east-1
    bucketSelector:
      matchLabels:
        app: jupyter
        component: storage
    blockPublicAcls: true
    blockPublicPolicy: true
    ignorePublicAcls: true
    restrictPublicBuckets: true
  providerConfigRef:
    name: default
---
# Expire scratch objects after 90 days. This is scratch by contract; an
# unbounded bucket becomes a de facto data store people rely on, which is
# exactly what the blast-radius argument above forbids.
apiVersion: s3.aws.upbound.io/v1beta1
kind: BucketLifecycleConfiguration
metadata:
  name: asela-jupyter-scratch-lifecycle
  labels:
    app: jupyter
    managed-by: crossplane
spec:
  forProvider:
    region: us-east-1
    bucketSelector:
      matchLabels:
        app: jupyter
        component: storage
    rule:
      - id: expire-scratch
        status: Enabled
        filter:
          - prefix: ""
        expiration:
          - days: 90
  providerConfigRef:
    name: default
```

**Note:** no `BucketVersioning` here, unlike `agent-audit-aws-infrastructure`. Versioning there exists to make an audit trail tamper-evident. This bucket is scratch with a 90-day expiry; versioning would only retain deleted scratch objects and their storage cost.

- [ ] **Step 7: Write the IAM manifests**

Create `base-apps/jupyter-aws-infrastructure/iam-user.yaml`:

```yaml
# IAM user for the JupyterLab workspace. Dedicated service account, one workload.
apiVersion: iam.aws.upbound.io/v1beta1
kind: User
metadata:
  name: jupyter-s3-user
  labels:
    app: jupyter
    component: iam-user
    managed-by: crossplane
spec:
  forProvider:
    path: /serviceaccounts/
    tags:
      Name: "Jupyter Workspace Service Account"
      Purpose: "Jupyter-S3-Scratch"
      ManagedBy: "Crossplane"
      Environment: "homelab"
  providerConfigRef:
    name: default
```

Create `base-apps/jupyter-aws-infrastructure/iam-policy.yaml`:

```yaml
# Read/write scoped to ONE bucket. This policy is the AWS half of the
# blast-radius argument in the design (§3.3): a fully compromised notebook
# kernel gets this bucket and nothing else in the account.
#
# ListBucket is on the bucket ARN; object actions are on the /* ARN. Both are
# needed — boto3's list_objects_v2 fails without the former.
apiVersion: iam.aws.upbound.io/v1beta1
kind: Policy
metadata:
  name: jupyter-s3-scratch
  labels:
    app: jupyter
    component: iam-policy
    managed-by: crossplane
spec:
  forProvider:
    description: "Read/write access to the Jupyter scratch bucket only"
    policy: |
      {
        "Version": "2012-10-17",
        "Statement": [
          {
            "Sid": "JupyterScratchList",
            "Effect": "Allow",
            "Action": ["s3:ListBucket"],
            "Resource": "arn:aws:s3:::asela-jupyter-scratch"
          },
          {
            "Sid": "JupyterScratchObjects",
            "Effect": "Allow",
            "Action": [
              "s3:GetObject",
              "s3:PutObject",
              "s3:DeleteObject"
            ],
            "Resource": "arn:aws:s3:::asela-jupyter-scratch/*"
          }
        ]
      }
    tags:
      Name: "Jupyter Scratch Bucket Policy"
      ManagedBy: "Crossplane"
  providerConfigRef:
    name: default
```

Create `base-apps/jupyter-aws-infrastructure/iam-policy-attachment.yaml`:

```yaml
apiVersion: iam.aws.upbound.io/v1beta1
kind: UserPolicyAttachment
metadata:
  name: jupyter-s3-user-policy
  labels:
    app: jupyter
    component: iam-policy-attachment
    managed-by: crossplane
spec:
  forProvider:
    policyArnSelector:
      matchLabels:
        app: jupyter
        component: iam-policy
    userRef:
      name: jupyter-s3-user
  providerConfigRef:
    name: default
```

Create `base-apps/jupyter-aws-infrastructure/access-key.yaml`:

```yaml
# Crossplane mints the access key and writes it to a k8s Secret directly — no
# manual key creation, no Vault round-trip.
apiVersion: iam.aws.upbound.io/v1beta1
kind: AccessKey
metadata:
  name: jupyter-s3-key
  labels:
    app: jupyter
    component: access-key
    managed-by: crossplane
spec:
  forProvider:
    userSelector:
      matchLabels:
        app: jupyter
        component: iam-user
  # provider-aws-iam writes the connection secret with these keys:
  #   username          -> the AWS access key ID (AKIA...)
  #   attribute.secret  -> the AWS secret access key
  # There is NO attribute.id key. Confirmed against the live
  # agent-audit-s3-creds and argo-workflows-s3-creds secrets.
  writeConnectionSecretToRef:
    name: jupyter-s3-creds
    namespace: jupyter
  providerConfigRef:
    name: default
```

- [ ] **Step 8: Re-run the full appset suite**

```bash
python3 -m pytest tests/appset/ -q
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add appsets/managed-apps/jupyter-aws-infrastructure.yaml \
        tests/appset/golden/jupyter-aws-infrastructure.yaml \
        tests/appset/test_managed_apps.py \
        base-apps/jupyter-aws-infrastructure/
git commit -m "feat(jupyter): S3 scratch bucket and scoped IAM user

Read/write on exactly one bucket with a 90-day expiry. This is the whole
AWS blast radius of a pod that runs arbitrary Python, so nothing else may
be granted access to it."
```

---

### Task 2: Out-of-band prerequisites (Vault, DNS, notebooks repo)

Nothing here is in git, and nothing later works without it. ESO will report `SecretSyncedError` and the pod will never start if the Vault path is missing, so this precedes the workload.

**Files:** none — this task produces cluster and account state.

**Interfaces:**
- Produces: Vault KV `k8s-secrets/jupyter` with properties `token` and `github-token`. Task 3's `ExternalSecret` consumes both by exactly those names.
- Produces: Vault Kubernetes auth role `jupyter`. Task 3's `SecretStore` references it by exactly that name.
- Produces: DNS `jupyter.arigsela.com` → the WAN address. Task 4 depends on it.

- [ ] **Step 1: Generate the Jupyter token**

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Save the output; it goes into Vault next and into your browser on first login.

- [ ] **Step 2: Create a GitHub PAT scoped to the notebooks repo**

Create `arigsela/notebooks` on GitHub (private, initialised with a README). Then create a **fine-grained** PAT scoped to *only* that repository, with `Contents: Read and write`. Broad-scope classic tokens are not acceptable here — this credential lives in a pod that runs arbitrary code.

- [ ] **Step 3: Write both secrets to Vault**

Vault CLI access is OIDC via Dex. If `vault status` fails from the laptop, port-forward first — the ingress is ClusterIP and unreachable directly:

```bash
kubectl -n vault port-forward svc/vault 8200:8200 &
export VAULT_ADDR=http://127.0.0.1:8200
vault login -method=oidc
```

Then:

```bash
vault kv put k8s-secrets/jupyter \
  token='<the token from Step 1>' \
  github-token='<the PAT from Step 2>'
```

- [ ] **Step 4: Verify the write**

```bash
vault kv get -format=json k8s-secrets/jupyter | jq -r '.data.data | keys[]'
```

Expected output, exactly these two lines:

```
github-token
token
```

- [ ] **Step 5: Create the Vault Kubernetes auth role**

The role name MUST equal the namespace — the repo's convention, and `secret-store.yaml` in Task 3 hardcodes it.

```bash
vault write auth/kubernetes/role/jupyter \
  bound_service_account_names=default \
  bound_service_account_namespaces=jupyter \
  policies=jupyter \
  ttl=1h
```

If a `jupyter` policy does not exist yet, create it first:

```bash
vault policy write jupyter - <<'EOF'
path "k8s-secrets/data/jupyter" {
  capabilities = ["read"]
}
path "k8s-secrets/metadata/jupyter" {
  capabilities = ["read", "list"]
}
EOF
```

- [ ] **Step 6: Create the Route 53 A record**

The 21 existing records are hand-edited via the AWS API, **not** Terraform-managed — do not look for a `.tf` file to change. Create `jupyter.arigsela.com` as an A record pointing at the current WAN address, which is the single source of truth in the allow-list annotation:

```bash
grep 'arigsela.com/wan-ip' base-apps/istio-ingress/authorizationpolicy.yaml
```

Use exactly that address.

- [ ] **Step 7: Verify DNS resolves**

```bash
dig +short jupyter.arigsela.com
```

Expected: the same address printed in Step 6. If it differs, stop — Task 4's TLS issuance will fail.

- [ ] **Step 8: No commit**

This task produces no git changes. Record completion in the PR description instead.

---

### Task 3: The workspace — Application, workload, storage, secrets, isolation

The pod, running and reachable in-cluster via port-forward. No public ingress yet; that is Task 4, so a reviewer can reject the security posture here without touching the exposure decision.

**Files:**
- Create: `base-apps/jupyter.yaml`
- Create: `base-apps/jupyter/{pvc,deployments,services,secret-store,external-secret,network-policy}.yaml`

**Interfaces:**
- Consumes: `jupyter-s3-creds` Secret from Task 1 (keys `username`, `attribute.secret`).
- Consumes: Vault path and role from Task 2.
- Produces: `Service/jupyter` in namespace `jupyter`, ClusterIP, port `8888`, selector `app=jupyter`. Task 4's `HTTPRoute` targets it by exactly that name and port.

- [ ] **Step 1: Resolve the image digest**

The repo pins upstream images. Resolve the current digest and use it verbatim in Step 3:

```bash
docker buildx imagetools inspect quay.io/jupyter/scipy-notebook:latest \
  --format '{{.Manifest.Digest}}'
```

Expected output shape: `sha256:` followed by 64 hex characters. Record it.

- [ ] **Step 2: Write the Argo CD Application**

Create `base-apps/jupyter.yaml`. The `directory.exclude` is mandatory — `catalog-info.yaml` is a Backstage entity, not a Kubernetes manifest, and Argo CD will fail the sync trying to apply it. `scripts/validate-agent-docs.py` enforces its presence in CI.

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  finalizers:
    - resources-finalizer.argocd.argoproj.io
  name: jupyter
  namespace: argo-cd
spec:
  project: default
  source:
    repoURL: https://github.com/arigsela/kubernetes
    targetRevision: main
    path: base-apps/jupyter
    directory:
      # catalog-info.yaml is a Backstage entity and mkdocs.yml is TechDocs
      # config; neither is a Kubernetes manifest. Argo CD would fail sync
      # trying to apply them.
      exclude: '{catalog-info.yaml,mkdocs.yml}'
  destination:
    server: https://kubernetes.default.svc
    namespace: jupyter
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
```

- [ ] **Step 3: Write the PVC**

Create `base-apps/jupyter/pvc.yaml`:

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: jupyter-pvc
  namespace: jupyter
  annotations:
    # local-path reclaims Delete, so an Argo prune of this PVC destroys the
    # data with it. Opt out of pruning: removing this volume must be a
    # deliberate act, not a side effect of a manifest rename.
    argocd.argoproj.io/sync-options: Prune=false
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: local-path
  resources:
    requests:
      storage: 20Gi
```

- [ ] **Step 4: Write the SecretStore and ExternalSecret**

Create `base-apps/jupyter/secret-store.yaml`:

```yaml
apiVersion: external-secrets.io/v1
kind: SecretStore
metadata:
  name: vault-backend
  namespace: jupyter
spec:
  provider:
    vault:
      server: "http://vault.vault.svc.cluster.local:8200"
      path: "k8s-secrets"
      version: "v2"
      auth:
        kubernetes:
          mountPath: "kubernetes"
          role: "jupyter"
          serviceAccountRef:
            name: "default"
```

Create `base-apps/jupyter/external-secret.yaml`:

```yaml
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: jupyter-secrets
  namespace: jupyter
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: vault-backend
    kind: SecretStore
  target:
    name: jupyter-secrets
    creationPolicy: Owner
  data:
    # The Jupyter server token. The SAME credential authenticates the browser
    # UI and Claude Code's /api/kernels calls — see design §3.2. Rotating it
    # logs out both.
    - secretKey: token
      remoteRef:
        key: jupyter
        property: token
    # Fine-grained PAT scoped to arigsela/notebooks only.
    - secretKey: github-token
      remoteRef:
        key: jupyter
        property: github-token
```

- [ ] **Step 5: Write the NetworkPolicy**

Create `base-apps/jupyter/network-policy.yaml`. This is the load-bearing control of the whole design — read design §3.3 before changing anything here.

```yaml
# THIS IS THE ISOLATION BOUNDARY for a pod that runs arbitrary Python.
#
# Requirements gathering established the notebooks need local compute, one S3
# bucket, and PyPI — and NOTHING in-cluster. That constraint is what makes a
# long-lived arbitrary-code pod tolerable, so it is enforced here rather than
# left as a habit.
#
# The RFC1918 exclusions also cover the Kubernetes API server (192.168.0.100),
# which is belt-and-braces: the pod additionally has no ServiceAccount token
# (automountServiceAccountToken: false in deployments.yaml). Either control
# alone would do; both together mean a single misconfiguration is not enough.
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: jupyter
  namespace: jupyter
spec:
  podSelector:
    matchLabels:
      app: jupyter
  policyTypes:
    - Ingress
    - Egress

  ingress:
    # Only the ingress Gateway may reach the notebook server.
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: istio-ingress
      ports:
        - protocol: TCP
          port: 8888

  egress:
    # DNS. Deliberately unrestricted by destination: kube-dns lives in the
    # service CIDR, which the RFC1918 exclusion below would otherwise block.
    - ports:
        - protocol: UDP
          port: 53
        - protocol: TCP
          port: 53

    # HTTPS to the public internet ONLY — S3, PyPI, GitHub. Every private
    # range is excluded, so PostgreSQL, Vault, Loki, Ollama and the
    # Kubernetes API are all unreachable from a notebook kernel.
    - to:
        - ipBlock:
            cidr: 0.0.0.0/0
            except:
              - 10.0.0.0/8
              - 172.16.0.0/12
              - 192.168.0.0/16
      ports:
        - protocol: TCP
          port: 443
        - protocol: TCP
          port: 80
```

- [ ] **Step 6: Write the Service**

Create `base-apps/jupyter/services.yaml`:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: jupyter
  namespace: jupyter
spec:
  type: ClusterIP
  ports:
    - port: 8888
      targetPort: 8888
      protocol: TCP
      name: http
  selector:
    app: jupyter
```

- [ ] **Step 7: Write the Deployment**

Create `base-apps/jupyter/deployments.yaml`, substituting the digest from Step 1.

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: jupyter
  namespace: jupyter
spec:
  replicas: 1
  strategy:
    # local-path is ReadWriteOnce, so a rolling update would deadlock: the new
    # pod cannot mount the volume until the old one releases it.
    type: Recreate
  selector:
    matchLabels:
      app: jupyter
  template:
    metadata:
      labels:
        app: jupyter
    spec:
      nodeSelector:
        node.kubernetes.io/workload: application
      # NO SERVICEACCOUNT TOKEN. This pod executes arbitrary Python; with a
      # token it would be a general-purpose cluster client. See design §3.3.
      automountServiceAccountToken: false
      securityContext:
        # scipy-notebook runs as jovyan (1000) in group users (100). fsGroup
        # MUST be 100 or the mounted home is unwritable and the server exits.
        runAsUser: 1000
        runAsGroup: 100
        fsGroup: 100
        runAsNonRoot: true
      containers:
        - name: jupyter
          image: quay.io/jupyter/scipy-notebook@sha256:REPLACE_WITH_DIGEST_FROM_STEP_1
          env:
            # Jupyter's native token auth. Same credential for the browser and
            # for Claude Code's /api/kernels calls.
            - name: JUPYTER_TOKEN
              valueFrom:
                secretKeyRef:
                  name: jupyter-secrets
                  key: token
            - name: AWS_ACCESS_KEY_ID
              valueFrom:
                secretKeyRef:
                  name: jupyter-s3-creds
                  key: username
            - name: AWS_SECRET_ACCESS_KEY
              valueFrom:
                secretKeyRef:
                  name: jupyter-s3-creds
                  key: attribute.secret
            - name: AWS_DEFAULT_REGION
              value: "us-east-1"
            - name: JUPYTER_SCRATCH_BUCKET
              value: "asela-jupyter-scratch"
          args:
            - "start-notebook.py"
            # Bind all interfaces inside the pod.
            - "--ServerApp.ip=0.0.0.0"
            # Without this the server refuses connections whose Host header is
            # not localhost, which is every request arriving via the Gateway.
            - "--ServerApp.allow_remote_access=True"
            # The websocket origin check compares against the request Host.
            # Behind the Gateway that is the public hostname, so it must be
            # named explicitly or /api/kernels websockets are rejected with 403
            # while plain HTTP still works — a confusing half-broken state.
            - "--ServerApp.allow_origin=https://jupyter.arigsela.com"
            - "--ServerApp.root_dir=/home/jovyan"
          ports:
            - containerPort: 8888
              name: http
          resources:
            requests:
              memory: "1Gi"
              cpu: "500m"
            limits:
              # A runaway cell must not evict neighbours on the node.
              memory: "4Gi"
              cpu: "2000m"
          livenessProbe:
            httpGet:
              path: /api
              port: 8888
            initialDelaySeconds: 60
            periodSeconds: 20
            timeoutSeconds: 5
            failureThreshold: 6
          readinessProbe:
            httpGet:
              path: /api
              port: 8888
            initialDelaySeconds: 30
            periodSeconds: 10
            timeoutSeconds: 5
            failureThreshold: 6
          volumeMounts:
            # The WHOLE home, not the conventional work/ subdirectory. This is
            # what makes ~/.local persistent, so `pip install --user` survives
            # restarts without a custom image. See design §3.4.
            - name: jupyter-home
              mountPath: /home/jovyan
            # The GitHub PAT, read at git-invocation time by the credential
            # helper configured in the notebooks repo bootstrap. Mounted OUTSIDE
            # the home on purpose: writing it into ~/.git-credentials would
            # persist the credential onto the PVC, where it would survive
            # rotation in Vault and outlive the secret it came from.
            - name: git-credentials
              mountPath: /etc/jupyter-secrets
              readOnly: true
      volumes:
        - name: jupyter-home
          persistentVolumeClaim:
            claimName: jupyter-pvc
        - name: git-credentials
          secret:
            secretName: jupyter-secrets
            items:
              - key: github-token
                path: github-token
            defaultMode: 0400
```

- [ ] **Step 8: Verify the manifests parse and carry the intended posture**

```bash
for f in base-apps/jupyter/*.yaml base-apps/jupyter.yaml; do
  python3 -c "import sys,yaml; list(yaml.safe_load_all(open(sys.argv[1])))" "$f" \
    && echo "ok $f" || { echo "FAIL $f"; exit 1; }
done
grep -q 'automountServiceAccountToken: false' base-apps/jupyter/deployments.yaml \
  && echo "ok: no SA token"
grep -q 'fsGroup: 100' base-apps/jupyter/deployments.yaml && echo "ok: fsGroup"
```

Expected: an `ok` line per file, plus `ok: no SA token` and `ok: fsGroup`.

- [ ] **Step 9: Commit and push so Argo CD can sync**

```bash
git add base-apps/jupyter.yaml base-apps/jupyter/
git commit -m "feat(jupyter): workspace deployment, storage, secrets, isolation

No ServiceAccount token and a NetworkPolicy blocking all RFC1918 egress:
the pod runs arbitrary Python, so its reach is bounded structurally rather
than by trusting the workload. PVC mounts the whole home so pip --user
persists without a custom image."
git push -u origin feature/jupyter-notebooks
```

- [ ] **Step 10: Verify the pod runs**

Argo CD syncs from `main`, so until this branch merges, sync manually against the branch or wait for merge. Once synced:

```bash
kubectl -n jupyter get externalsecret jupyter-secrets
kubectl -n jupyter get pods -l app=jupyter
```

Expected: the ExternalSecret shows `SecretSynced`, and the pod reaches `Running` / `1/1`. If the ExternalSecret shows `SecretSyncedError`, Task 2 Step 5's Vault role is wrong or missing.

- [ ] **Step 11: Verify the server answers in-cluster**

```bash
kubectl -n jupyter port-forward svc/jupyter 8888:8888 &
TOKEN=$(kubectl -n jupyter get secret jupyter-secrets -o jsonpath='{.data.token}' | base64 -d)
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8888/api
curl -s -H "Authorization: token $TOKEN" http://127.0.0.1:8888/api/kernelspecs | head -c 200
```

Expected: `200` from `/api`, and a JSON body naming the `python3` kernelspec.

---

### Task 4: Public exposure — TLS, Gateway listener, route, allow-list

Separated from Task 3 so the exposure decision can be reviewed on its own. Every host added to the Gateway is denied until named in the AuthorizationPolicy, so the allow-list edit is not optional.

**Files:**
- Create: `base-apps/jupyter/{certificate,reference-grant,httproute}.yaml`
- Modify: `base-apps/istio-ingress/gateway.yaml`
- Modify: `base-apps/istio-ingress/authorizationpolicy.yaml`

**Interfaces:**
- Consumes: `Service/jupyter:8888` from Task 3.
- Produces: Secret `jupyter-tls` in namespace `jupyter`, referenced by the Gateway listener `https-jupyter`.

- [ ] **Step 1: Write the Certificate**

Create `base-apps/jupyter/certificate.yaml`:

```yaml
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: jupyter-tls
  namespace: jupyter
spec:
  secretName: jupyter-tls
  dnsNames:
    - jupyter.arigsela.com
  issuerRef:
    # NOT letsencrypt-prod: its only solver is http01.ingress.class=nginx,
    # which nothing has satisfied since the Istio cutover. DNS-01 via Route 53
    # is the only working issuer for new hosts.
    name: letsencrypt-route53
    kind: ClusterIssuer
```

- [ ] **Step 2: Write the ReferenceGrant**

The Gateway is in `istio-ingress`; this Secret is in `jupyter`. Gateway API forbids that cross-namespace read without an explicit grant, and the symptom of omitting it is a listener stuck without a certificate.

Create `base-apps/jupyter/reference-grant.yaml`:

```yaml
# Lets the ingress Gateway in istio-ingress read this namespace's TLS secret.
apiVersion: gateway.networking.k8s.io/v1beta1
kind: ReferenceGrant
metadata:
  name: gateway-to-jupyter-tls
  namespace: jupyter
spec:
  from:
    - group: gateway.networking.k8s.io
      kind: Gateway
      namespace: istio-ingress
  to:
    - group: ""
      kind: Secret
      name: jupyter-tls
```

- [ ] **Step 3: Add the Gateway listener**

In `base-apps/istio-ingress/gateway.yaml`, add this listener to the `listeners:` list, immediately after the `https-n8n` block, matching the surrounding indentation exactly:

```yaml
    - name: https-jupyter
      protocol: HTTPS
      port: 443
      hostname: jupyter.arigsela.com
      tls:
        mode: Terminate
        certificateRefs:
          - kind: Secret
            name: jupyter-tls
            namespace: jupyter
      allowedRoutes:
        namespaces:
          from: All
```

- [ ] **Step 4: Add the allow-list rule**

In `base-apps/istio-ingress/authorizationpolicy.yaml`, add this rule to `spec.rules`, after the n8n admin-UI rule. Copy the four addresses **verbatim from an existing restricted rule in the same file** rather than from this plan — the WAN address rotates, and the authoritative value is the `arigsela.com/wan-ip` annotation at the top of that file.

```yaml
    # jupyter - restricted. The kernel executes arbitrary Python and holds S3
    # credentials, so this allow-list and the Jupyter token are the two
    # controls standing between the LAN and remote code execution. There is
    # deliberately no public path here, unlike n8n's webhook carve-out.
    - to:
        - operation:
            hosts:
              - jupyter.arigsela.com
              - jupyter.arigsela.com:*
      from:
        - source:
            ipBlocks:
              - 76.97.4.210/32
              - 170.85.56.189/32
              - 170.85.130.202/32
              - 104.28.177.82/32
```

- [ ] **Step 5: Write the HTTPRoute**

Create `base-apps/jupyter/httproute.yaml`:

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: jupyter
  namespace: jupyter
  annotations:
    gethomepage.dev/enabled: "true"
    gethomepage.dev/name: Jupyter
    gethomepage.dev/group: Development
    gethomepage.dev/icon: jupyter.png
    gethomepage.dev/description: Interactive Python notebooks
    gethomepage.dev/pod-selector: app=jupyter
spec:
  parentRefs:
    - name: main
      namespace: istio-ingress
      sectionName: https-jupyter
  hostnames:
    - jupyter.arigsela.com
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /
      backendRefs:
        - name: jupyter
          port: 8888
```

- [ ] **Step 6: Verify the WAN-IP consistency gate still passes**

The allow-list now contains a fourth occurrence of the WAN address. `rewrite_policy()` rewrites every line whose stripped form is exactly `- <ip>/32`, so rotation stays correct — this test proves it.

```bash
python3 -m pytest tests/wan_ip/ -q
```

Expected: PASS.

- [ ] **Step 7: Commit and push**

```bash
git add base-apps/jupyter/certificate.yaml \
        base-apps/jupyter/reference-grant.yaml \
        base-apps/jupyter/httproute.yaml \
        base-apps/istio-ingress/gateway.yaml \
        base-apps/istio-ingress/authorizationpolicy.yaml
git commit -m "feat(jupyter): expose jupyter.arigsela.com via the Istio Gateway

Restricted rule only, no public carve-out: the allow-list and the Jupyter
token are the two controls between the LAN and remote code execution.
letsencrypt-route53 because prod's http01/nginx solver is dead post-cutover."
git push
```

- [ ] **Step 8: Verify certificate issuance**

This is the most likely first failure.

```bash
kubectl -n jupyter get certificate jupyter-tls
```

Expected: `READY=True`. If it stays `False` for more than ~5 minutes:

```bash
kubectl -n jupyter describe certificate jupyter-tls
kubectl -n jupyter get challenges
```

A pending DNS-01 challenge usually means Task 2 Step 6's Route 53 record is missing or in the wrong hosted zone.

- [ ] **Step 9: Verify end-to-end HTTPS and the auth boundary**

```bash
curl -s -o /dev/null -w 'no-token: %{http_code}\n' https://jupyter.arigsela.com/api
curl -s -o /dev/null -w 'with-token: %{http_code}\n' \
  -H "Authorization: token $TOKEN" https://jupyter.arigsela.com/api
```

Expected: `no-token: 403` and `with-token: 200`. A `200` without a token means `JUPYTER_TOKEN` did not reach the container — stop and fix before proceeding.

---

### Task 5: agent-docs contract

Brings the app into the documentation framework CI enforces. Foldable into no other task: `validate-agent-docs.py` gates on it independently, and the generated files must be regenerated, never hand-written.

**Files:**
- Create: `base-apps/jupyter/{catalog-info.yaml,docs.md,runbook.md}`
- Modify: `scripts/agent-docs-scope.txt`
- Generated: `base-apps/jupyter/mkdocs.yml`, `base-apps/jupyter/docs/`, `base-apps/index.md`

- [ ] **Step 1: Add the app to the docs scope (the failing gate)**

Append `jupyter` as the last line of `scripts/agent-docs-scope.txt`.

- [ ] **Step 2: Run the validator to verify it fails**

```bash
python3 scripts/validate-agent-docs.py --repo-root .
```

Expected: FAIL, reporting `jupyter` is in scope but missing `docs.md`, `runbook.md`, and `catalog-info.yaml`.

- [ ] **Step 3: Write `catalog-info.yaml`**

```yaml
apiVersion: backstage.io/v1alpha1
kind: Component
metadata:
  name: jupyter
  namespace: jupyter
  annotations:
    agent-docs/path: docs.md
    backstage.io/kubernetes-label-selector: 'app=jupyter'
    backstage.io/kubernetes-namespace: jupyter
  tags: [python, notebooks, jupyter]
spec:
  type: service
  lifecycle: production
  owner: platform
  system: default/platform-tooling
  dependsOn: []
```

- [ ] **Step 4: Write `docs.md`**

```markdown
---
type: "Kubernetes App Guide"
title: "JupyterLab Workspace"
description: "Single-workspace JupyterLab for interactive Python, served to a browser and to Claude Code via /api/kernels."
app: jupyter
catalog_entity: jupyter
kind: docs
namespace: jupyter
last_reviewed: 2026-08-17
status: current
tags: [python, notebooks, jupyter]
sources:
  - base-apps/jupyter/deployments.yaml
  - base-apps/jupyter/network-policy.yaml
  - base-apps/jupyter/external-secret.yaml
  - base-apps/jupyter-aws-infrastructure/iam-policy.yaml
---

# JupyterLab Workspace

## What it is
Interactive Python in the cluster at `jupyter.arigsela.com`. It serves two clients that are the same principal: a human in a browser, and Claude Code on the operator's laptop calling `/api/kernels`. Batch Python belongs in Argo Workflows; this is for exploration.

## Architecture & data flow
One `Deployment` of the upstream `quay.io/jupyter/scipy-notebook` image, digest-pinned, behind the `main` Istio Gateway. Jupyter Server serves the Lab UI and the kernel API from the same process on port 8888, and both clients present the same token — there is deliberately no second authentication path.

State is split three ways. Notebooks live in `arigsela/notebooks` on GitHub. The 20Gi `local-path` PVC (`pvc.yaml`) mounts the **whole home** at `/home/jovyan`, holding scratch data and `~/.local`. Bulk data lives in S3 `asela-jupyter-scratch`, provisioned by `base-apps/jupyter-aws-infrastructure/` with an IAM user scoped to that one bucket.

## Where config lives
- Workload and security posture: `deployments.yaml`
- Isolation: `network-policy.yaml`
- Secrets: `external-secret.yaml` / `secret-store.yaml`, from Vault `k8s-secrets/jupyter` (`token`, `github-token`), Vault role `jupyter`
- AWS: `base-apps/jupyter-aws-infrastructure/`, connection Secret `jupyter-s3-creds`
- Exposure: `httproute.yaml`, `certificate.yaml`, `reference-grant.yaml`, plus the `https-jupyter` listener in `base-apps/istio-ingress/gateway.yaml` and the restricted rule in `authorizationpolicy.yaml`

## Gotchas & tribal knowledge
- **The PVC mounts the whole home, not `work/`.** This is deliberate: it makes `~/.local` persistent so `pip install --user -r requirements.txt` survives restarts without a custom image. It also shadows whatever the image ships in `/home/jovyan`.
- **`fsGroup` must be `100`.** The image runs as `jovyan` (1000) in group `users` (100). A wrong `fsGroup` leaves the mounted home unwritable and the server exits at startup.
- **`--ServerApp.allow_origin` must name the public hostname.** Omit it and plain HTTP works while `/api/kernels` websockets are rejected with 403 — a half-broken state that looks like an auth bug.
- **The pod has no ServiceAccount token and cannot reach any RFC1918 address.** This is the design's central control, not an oversight. Anything needing in-cluster access does not belong in a notebook — see `docs/superpowers/specs/2026-08-17-jupyter-notebooks-design.md` §3.3.
- **`strategy: Recreate`.** `local-path` is ReadWriteOnce, so a rolling update deadlocks on the volume.
- **Rotating the token logs out both clients**, because both use it.
- **The GitHub PAT is mounted at `/etc/jupyter-secrets/github-token`, outside the home, and read by a git credential helper at invocation time.** It is deliberately never written to `~/.git-credentials`: the home is the PVC, so a copy there would survive rotation in Vault and outlive the secret it came from.
```

- [ ] **Step 5: Write `runbook.md`**

```markdown
---
type: "Kubernetes App Runbook"
title: "JupyterLab Workspace — Runbook"
description: "Operational runbook for jupyter: failure modes, checks, and fixes."
app: jupyter
catalog_entity: jupyter
kind: runbook
namespace: jupyter
last_reviewed: 2026-08-17
status: current
tags: [python, notebooks, jupyter]
sources:
  - base-apps/jupyter/deployments.yaml
  - base-apps/jupyter/network-policy.yaml
---

# JupyterLab Workspace — Runbook

## Failure modes

### Symptom: pod CrashLoopBackOff, logs show a permission error on /home/jovyan
- **Check:** `kubectl -n jupyter get deploy jupyter -o jsonpath='{.spec.template.spec.securityContext}'`
- **Fix:** `fsGroup` must be `100` and `runAsUser` `1000`. Any other value leaves the mounted home unwritable.

### Symptom: browser loads JupyterLab but notebooks will not start a kernel; console shows a 403 on the websocket
- **Check:** `kubectl -n jupyter get deploy jupyter -o yaml | grep allow_origin`
- **Fix:** `--ServerApp.allow_origin` must be exactly `https://jupyter.arigsela.com`. Plain HTTP endpoints work without it, which makes this look like an auth problem rather than an origin-check problem.

### Symptom: 403 from every request, including with a valid token
- **Check:** `grep -A12 'jupyter.arigsela.com' base-apps/istio-ingress/authorizationpolicy.yaml`
- **Fix:** the gateway allow-list denies by default. If the WAN address rotated, the Route 53 record and this file must move together — see `base-apps/wan-ip-monitor/runbook.md`.

### Symptom: ExternalSecret shows SecretSyncedError
- **Check:** `kubectl -n jupyter describe externalsecret jupyter-secrets`
- **Fix:** confirm the Vault role exists and is bound to this namespace: `vault read auth/kubernetes/role/jupyter`. The role name must equal the namespace.

### Symptom: boto3 calls fail with AccessDenied
- **Check:** `kubectl -n jupyter get secret jupyter-s3-creds -o jsonpath='{.data}' | jq keys`
- **Fix:** keys are `username` and `attribute.secret` — there is no `attribute.id`. The IAM policy grants only `asela-jupyter-scratch`; any other bucket is denied by design, not by mistake.

### Symptom: pod Pending after a node reboot
- **Check:** `kubectl -n jupyter describe pvc jupyter-pvc`
- **Fix:** `local-path` pins the volume to one node. If that node is gone the PVC cannot bind. Nothing irreplaceable is on it: delete the PVC, let it rebind, then re-clone the notebooks repo and re-run `pip install --user -r requirements.txt`.

## How-to

### Deploy / update
Commit to `main`; Argo CD syncs. Never `kubectl apply`.

### Rotate the Jupyter token
`vault kv patch k8s-secrets/jupyter token=<new>`, then `kubectl -n jupyter rollout restart deploy/jupyter`. ESO refreshes hourly, but the pod reads the token only at startup. This logs out the browser and Claude Code together.

### Install a package permanently
Add it to `requirements.txt` in `arigsela/notebooks`, then from a JupyterLab terminal: `pip install --user -r ~/work/notebooks/requirements.txt`. It persists because `~/.local` is on the PVC.
```

- [ ] **Step 6: Generate the TechDocs scaffolding and the index**

Never hand-write `mkdocs.yml` or `docs/` — they are generated copies and CI fails on drift.

```bash
python3 scripts/gen-techdocs.py --repo-root .
python3 scripts/gen-okf.py --repo-root .
```

- [ ] **Step 7: Run every documentation gate**

```bash
python3 scripts/validate-agent-docs.py --repo-root .
python3 scripts/gen-okf.py --check
python3 scripts/gen-techdocs.py --check
```

Expected: all three pass. `validate-agent-docs.py` should now report 22 apps in scope, up from 21.

- [ ] **Step 8: Commit**

```bash
git add base-apps/jupyter/catalog-info.yaml base-apps/jupyter/docs.md \
        base-apps/jupyter/runbook.md base-apps/jupyter/mkdocs.yml \
        base-apps/jupyter/docs/ scripts/agent-docs-scope.txt base-apps/index.md
git commit -m "docs(jupyter): agent-docs contract

Records the three things that will bite an operator: fsGroup 100, the
allow_origin flag whose absence breaks only websockets, and that the pod's
inability to reach the cluster is the design working."
git push
```

---

### Task 6: Acceptance — prove the isolation, then bootstrap the workspace

The design's central claim is that a compromised kernel reaches one S3 bucket and the internet. Step 3 is the pass/fail gate on the entire design; the bootstrap follows only if it holds.

**Files:** none in this repo. Produces content in `arigsela/notebooks`.

- [ ] **Step 1: Confirm the notebook API executes code**

This is the acceptance test for the agent half. From the laptop:

```bash
TOKEN=$(kubectl -n jupyter get secret jupyter-secrets -o jsonpath='{.data.token}' | base64 -d)
curl -s -X POST -H "Authorization: token $TOKEN" \
  -H 'Content-Type: application/json' -d '{"name":"python3"}' \
  https://jupyter.arigsela.com/api/kernels
```

Expected: JSON containing an `id` and `"execution_state"`. Record the kernel id.

- [ ] **Step 2: Confirm S3 access works and is bounded**

Open a terminal in JupyterLab (`File → New → Terminal`) and run:

```bash
pip install --user boto3
python3 -c "
import boto3
s3 = boto3.client('s3')
s3.put_object(Bucket='asela-jupyter-scratch', Key='hello.txt', Body=b'ok')
print('write ok:', s3.get_object(Bucket='asela-jupyter-scratch', Key='hello.txt')['Body'].read())
try:
    s3.list_objects_v2(Bucket='asela-terraform-states')
    print('FAIL: reached the terraform state bucket')
except Exception as e:
    print('correctly denied:', type(e).__name__)
"
```

Expected: `write ok: b'ok'` followed by `correctly denied: ClientError`. Reaching the Terraform state bucket is a **hard failure** — fix `iam-policy.yaml` before continuing.

- [ ] **Step 3: The isolation negative test — PASS/FAIL ON THE WHOLE DESIGN**

In the same terminal:

```bash
python3 -c "
import socket
for host, port, label in [
    ('postgresql.postgresql.svc.cluster.local', 5432, 'PostgreSQL'),
    ('vault.vault.svc.cluster.local', 8200, 'Vault'),
    ('192.168.0.100', 6443, 'Kubernetes API'),
]:
    s = socket.socket(); s.settimeout(5)
    try:
        s.connect((host, port)); print(f'FAIL: reached {label}')
    except Exception as e:
        print(f'correctly blocked: {label} ({type(e).__name__})')
    finally:
        s.close()
"
ls /var/run/secrets/kubernetes.io/ 2>&1 || echo "correctly absent: no SA token"
```

Expected: three `correctly blocked:` lines and `correctly absent: no SA token`.

**If any line reports FAIL, the NetworkPolicy is not being enforced** — k3s's policy controller may not be applying egress rules alongside `istio-cni`/ztunnel, which the design flagged as the one unproven assumption (§3.3). Do not treat the deployment as complete. Record the finding, and either fix enforcement or fall back to an Istio `AuthorizationPolicy` with an egress waypoint before using the workspace for anything.

- [ ] **Step 4: Bootstrap the notebooks repo**

In the JupyterLab terminal. The credential helper reads the mounted secret at
each git invocation, so the PAT is never written to the PVC and picks up a Vault
rotation on the next pod restart with no further action:

```bash
git config --global credential.helper \
  '!f() { echo username=x-access-token; echo "password=$(cat /etc/jupyter-secrets/github-token)"; }; f'
git config --global user.email "arigsela@gmail.com"
git config --global user.name "Ari sela"
mkdir -p ~/work && cd ~/work && git clone https://github.com/arigsela/notebooks.git
```

Expected: the clone succeeds without prompting for credentials. `~/.gitconfig`
persists on the PVC, so this is a one-time setup; the token itself is not stored
there.

- [ ] **Step 5: Seed `requirements.txt` and confirm persistence**

```bash
cd ~/work/notebooks
printf 'boto3>=1.34\ns3fs>=2024.6\npandas>=2.2\n' > requirements.txt
pip install --user -r requirements.txt
git add requirements.txt && git commit -m "chore: initial requirements" && git push
```

Then restart the pod and confirm the packages survived — this is what the whole-home mount decision buys:

```bash
kubectl -n jupyter rollout restart deploy/jupyter
kubectl -n jupyter rollout status deploy/jupyter
kubectl -n jupyter exec deploy/jupyter -- python3 -c "import boto3; print('boto3 persisted', boto3.__version__)"
```

Expected: `boto3 persisted <version>`. If the import fails, the PVC is not mounted at `/home/jovyan` and §3.4's decision is not in effect.

- [ ] **Step 6: Open the PR**

```bash
gh pr create --title "feat: JupyterLab workspace" \
  --body "$(cat <<'EOF'
Deploys a single-workspace JupyterLab at jupyter.arigsela.com, serving a
browser and Claude Code's /api/kernels calls with one token.

Design: docs/superpowers/specs/2026-08-17-jupyter-notebooks-design.md
Plan:   docs/superpowers/plans/2026-08-17-jupyter-notebooks.md

The pod runs arbitrary Python, so its reach is bounded structurally: no
ServiceAccount token, a NetworkPolicy blocking all RFC1918 egress, and IAM
scoped to one throwaway bucket. Task 6 Step 3 verifies all three in-cluster.

Out-of-band steps completed: Vault k8s-secrets/jupyter + role, Route 53 A
record, arigsela/notebooks repo.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01MadyPGpsD2PihB58sQtnEF
EOF
)"
```

---

## Rollback

Delete `base-apps/jupyter.yaml` and `base-apps/jupyter/`, revert the two `istio-ingress` edits, and remove `jupyter` from `scripts/agent-docs-scope.txt`. The PVC carries `Prune=false`, so it survives and must be deleted by hand if you want the data gone.

Leave `base-apps/jupyter-aws-infrastructure/` alone unless you also want the bucket destroyed — it is a separate Application precisely so the two lifecycles are independent.
