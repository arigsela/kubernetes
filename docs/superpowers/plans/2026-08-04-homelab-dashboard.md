# Homelab Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy gethomepage/homepage at `home.arigsela.com` as a single dashboard listing every cluster app plus the WSL2-hosted Plex server, with live widgets for Plex, Grafana, Argo CD, and cluster resources.

**Architecture:** One Argo CD Application at `base-apps/homepage/`, following the `base-apps/donetick/` template. Link-only tiles are discovered at runtime from `gethomepage.dev/*` annotations on each app's existing `HTTPRoute`; widget-bearing tiles are defined statically in the ConfigMap because Homepage performs `{{HOMEPAGE_VAR_*}}` substitution only in config files, never in annotations. Argo CD's widget is driven by Prometheus rather than an Argo CD API token.

**Tech Stack:** Kubernetes (k3s), Argo CD, Istio Gateway API, External Secrets Operator + Vault, cert-manager, Prometheus, Terraform (OpenTofu), Helm.

**Source spec:** `docs/superpowers/specs/2026-08-04-homelab-dashboard-design.md`

## Global Constraints

- **Branch:** work in the isolated worktree at
  `/Users/arisela/git/kubernetes/.claude/worktrees/homelab-dashboard`, branch
  `worktree-homelab-dashboard`. **Commit, never push.**
- **LIVE VERIFICATION IS DEFERRED TO POST-MERGE.** Argo CD syncs `main` only, so
  nothing in this branch reaches the cluster until it is merged. Any step that
  needs a running Homepage — `kubectl -n homepage ...`, loading
  `https://home.arigsela.com`, checking tiles or widgets — **cannot be run during
  implementation.** Where a step says so, do the local validation, then record the
  live check as deferred in your report rather than attempting it or claiming it
  passed. Steps that touch infrastructure outside this branch (Prometheus queries,
  Vault reads, `curl` to the Plex host) are unaffected and DO run — those systems
  are live regardless of branch.
- **Local validation always runs and always gates a commit:** `yamllint`,
  `kubeconform`, and the Python validator suites work entirely offline. A task is
  not done until they pass.
- **Image:** `ghcr.io/gethomepage/homepage:v1.13.2`, pinned. Never `:latest`.
- **Hostname:** `home.arigsela.com`. Namespace: `homepage`. Vault role name must equal the namespace.
- **`HOMEPAGE_ALLOWED_HOSTS` is mandatory** (Homepage ≥ v1.0) and must contain both `$(MY_POD_IP):3000` (for the kubelet probe) and `home.arigsela.com`.
- **All 8 config files must exist** in the ConfigMap even when empty: `settings.yaml`, `services.yaml`, `widgets.yaml`, `kubernetes.yaml`, `bookmarks.yaml`, `docker.yaml`, `custom.css`, `custom.js`.
- **subPath mounts never receive ConfigMap updates.** Any `configmap.yaml` change REQUIRES bumping the `checksum/config` pod annotation, or the change syncs and does nothing.
- **Never put a credential in an annotation.** Annotations are plaintext in Git and get no variable substitution.
- **Do not add `homepage` to `scripts/agent-docs-scope.txt` until Task 8.** `validate-agent-docs.py` only checks in-scope apps, so earlier commits pass CI without the docs contract. Adding it early makes every intermediate commit fail CI.
- **Local validation before every commit:** `yamllint` (pinned 1.35.1 in CI) and `kubeconform` on changed manifests.

---

### Task 1: Enable Argo CD controller metrics

Argo CD's widget reads `argocd_app_info` from Prometheus. That metric does not exist today — the controller metrics Service is not created and no Service in `argo-cd` carries a scrape annotation. This task is first because everything downstream that displays Argo CD data depends on it, and because it is the only task that rolls a core cluster component.

**Files:**
- Modify: `terraform/roots/asela-cluster/argocd.tf:40-50` (the `controller` block)

**Interfaces:**
- Consumes: nothing
- Produces: the metric `argocd_app_info` in Prometheus, with labels `sync_status` and `health_status`, consumed by Task 6's `prometheusmetric` widget.

- [ ] **Step 1: Confirm the metric is absent (the failing test)**

```bash
kubectl -n logging port-forward svc/prometheus 9090:9090 >/dev/null 2>&1 &
sleep 3
curl -s 'http://localhost:9090/api/v1/query?query=count(argocd_app_info)' | python3 -m json.tool
```

Expected: `"result": []` — an empty result array. If this already returns data, metrics are somehow enabled; stop and re-read `argocd.tf` before continuing.

Leave the port-forward running; Step 5 reuses it.

- [ ] **Step 2: Add metrics to the controller block**

In `terraform/roots/asela-cluster/argocd.tf`, the `controller` block currently contains only `nodeSelector` and `tolerations`. Add a `metrics` key alongside them:

```hcl
    controller = {
      nodeSelector = {
        "node.kubernetes.io/workload" = "infrastructure"
      }
      tolerations = [
        {
          key    = "node-role.kubernetes.io/control-plane"
          effect = "NoSchedule"
        }
      ]
      # Creates the argocd-application-controller-metrics Service exposing
      # argocd_app_info on :8082. Annotated for the existing Prometheus
      # `kubernetes-service-endpoints` job, which keeps any Service labelled
      # prometheus.io/scrape=true and honors prometheus.io/port — so no
      # Prometheus config change is needed. Consumed by the Argo CD tile on
      # home.arigsela.com.
      metrics = {
        enabled = true
        service = {
          annotations = {
            "prometheus.io/scrape" = "true"
            "prometheus.io/port"   = "8082"
          }
        }
      }
    }
```

This goes in the `settings` map (which is `yamlencode`d by the module), **not** as a `set` block in `terraform/modules/argocd/helm.tf`. Helm's `set` syntax requires escaping the dots in annotation keys — see the existing `server.config.exec\\.enabled` line for how unpleasant that is.

- [ ] **Step 3: Plan and review**

```bash
cd terraform/roots/asela-cluster
terraform init
terraform plan -target=module.argocd
```

Expected: an in-place update to the `helm_release.argocd` resource. Read the diff and confirm it touches only `controller.metrics`. If the plan proposes replacing the release or changing the image tag, stop — the chart or RC pin has drifted and that is a separate problem.

- [ ] **Step 4: Apply**

```bash
terraform apply -target=module.argocd
```

This rolls the application-controller. That is safe — it is a reconciler with no in-flight user state — but Argo CD briefly stops syncing. Do not run this in the same window as other cluster changes.

- [ ] **Step 5: Verify the Service exists and the metric flows**

```bash
kubectl -n argo-cd get svc | grep metrics
```

Expected: a service named like `argo-cd-argocd-application-controller-metrics`.

Prometheus service discovery needs a scrape cycle. Wait, then query:

```bash
sleep 60
curl -s 'http://localhost:9090/api/v1/query?query=count(argocd_app_info)' | python3 -m json.tool
```

Expected: a non-empty `result` with a numeric value roughly matching your Application count.

- [ ] **Step 6: Capture the real label names**

```bash
curl -s 'http://localhost:9090/api/v1/query?query=argocd_app_info' \
  | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["data"]["result"][0]["metric"], indent=2))'
```

Expected: a label set including `sync_status` and `health_status`. **Write down the exact label names and observed values** (e.g. `Synced`, `OutOfSync`, `Healthy`, `Degraded`). Task 6 hardcodes these in PromQL and will silently render zeros if they differ.

Then stop the port-forward: `kill %1`

- [ ] **Step 7: Commit**

```bash
git add terraform/roots/asela-cluster/argocd.tf
git commit -m "argo-cd: expose controller metrics for Prometheus scraping

Creates the application-controller metrics Service and annotates it for
the existing kubernetes-service-endpoints scrape job, so argocd_app_info
becomes queryable. Consumed by the Argo CD tile on the new dashboard, and
makes Argo CD metrics available in Grafana generally."
```

---

### Task 2: Make Plex reachable from cluster pods

Plex runs inside WSL2, which sits behind a NAT'd virtual switch, so pods cannot reach it. This task is on the Windows box, not in Git. It is independent of every other task and can be done in parallel.

**Files:**
- Modify: `%UserProfile%\.wslconfig` on the Windows host (not in this repo)

**Interfaces:**
- Consumes: nothing
- Produces: `http://<WINDOWS_LAN_IP>:32400` reachable from inside the cluster. Task 6 hardcodes that URL.

- [ ] **Step 1: Record the Windows host's LAN IP and confirm the failure**

On the Windows box, in PowerShell:

```powershell
ipconfig | Select-String IPv4
```

Write down the LAN address (expect `10.0.1.x` based on this network's other hosts).

From your laptop, confirm it is currently unreachable:

```bash
curl -sS -m 5 -o /dev/null -w '%{http_code}\n' http://<WINDOWS_LAN_IP>:32400/identity
```

Expected: a timeout or connection refused. If this already returns `200`, Plex is already reachable — skip to Step 5 and just do the DHCP reservation.

- [ ] **Step 2: Enable WSL2 mirrored networking**

Requires Windows 11 22H2+ and WSL 2.0+. Check with `wsl --version`.

Edit (or create) `%UserProfile%\.wslconfig`:

```ini
[wsl2]
networkingMode=mirrored
```

**If on Windows 10 or WSL < 2.0**, mirrored mode is unavailable. Use the fallback instead — in an Administrator PowerShell:

```powershell
$wslIp = (wsl hostname -I).Trim().Split()[0]
netsh interface portproxy add v4tov4 listenport=32400 listenaddress=0.0.0.0 connectport=32400 connectaddress=$wslIp
New-NetFirewallRule -DisplayName "Plex 32400" -Direction Inbound -LocalPort 32400 -Protocol TCP -Action Allow
```

Note this fallback breaks on every reboot, because the WSL IP changes. It needs a Scheduled Task running the same two lines at logon. Record whichever path you took — it goes in `runbook.md` in Task 8.

- [ ] **Step 3: Restart WSL**

```powershell
wsl --shutdown
```

Then start your distro again and confirm Plex is running.

- [ ] **Step 4: Verify from your laptop**

```bash
curl -sS -m 5 -o /dev/null -w '%{http_code}\n' http://<WINDOWS_LAN_IP>:32400/identity
```

Expected: `200`.

- [ ] **Step 5: Set a DHCP reservation**

In your router's DHCP settings, reserve the current address for the Windows box's MAC. The Plex URL is a static IP in the ConfigMap; a lease change silently breaks the widget.

- [ ] **Step 6: Verify from inside the cluster**

This is the check that actually matters — pod egress to a LAN address, through the Istio ambient mesh.

```bash
kubectl run plex-probe --rm -it --restart=Never --image=curlimages/curl:8.10.1 -- \
  curl -sS -m 5 -o /dev/null -w '%{http_code}\n' http://<WINDOWS_LAN_IP>:32400/identity
```

Expected: `200`. If the laptop check passed but this fails, the problem is cluster egress rather than WSL — check for NetworkPolicies in the default namespace before proceeding.

No commit — nothing in this repo changed.

---

### Task 3: Provision Vault secrets

**Files:**
- Create: `scripts/provision-homepage-vault.sh`

**Interfaces:**
- Consumes: nothing
- Produces: Vault path `k8s-secrets/homepage` with properties `plex-token`, `grafana-user`, `grafana-password`; a `homepage` policy; a `homepage` kubernetes-auth role bound to the `default` ServiceAccount in namespace `homepage`. Task 4's `secret-store.yaml` and Task 6's `external-secrets.yaml` depend on these exact names.

- [ ] **Step 1: Create the Grafana Viewer user**

Grafana provisions datasources, dashboards, and alerting from config, but **not users** — this is a manual one-time step.

Log in to `https://grafana.arigsela.com` as admin, then **Administration → Users → New user**. Create:
- Username: `homepage`
- Password: generate a strong one and keep it for Step 3
- Then **Administration → Users → homepage → Organisations**: set the role to **Viewer**

Do not reuse the existing admin credentials. Homepage has no authentication of its own, so admin credentials would sit in the environment of an unauthenticated pod purely to render a dashboard count.

Verify the account works and is read-only:

```bash
curl -sS -u 'homepage:<PASSWORD>' https://grafana.arigsela.com/api/search | head -c 200
```

Expected: a JSON array of dashboards.

- [ ] **Step 2: Obtain the Plex token**

Sign in to Plex Web, open any library item → **⋮ → Get Info → View XML**. The opened URL ends with `&X-Plex-Token=<TOKEN>`. Copy that token.

Verify it against the reachable address from Task 2:

```bash
curl -sS "http://<WINDOWS_LAN_IP>:32400/library/sections?X-Plex-Token=<TOKEN>" | head -c 300
```

Expected: XML listing your libraries. A `401` means the token is wrong.

- [ ] **Step 3: Write the provisioning script**

Create `scripts/provision-homepage-vault.sh`, mirroring `scripts/provision-donetick-vault.sh`:

```sh
#!/bin/sh
# homepage — Vault provisioning (one-time, idempotent, safe to re-run)
#
# Creates the scoped Vault secret, policy, and kubernetes-auth role backing the
# ESO manifests in base-apps/homepage/:
#   - k8s-secrets/homepage  (props: plex-token, grafana-user, grafana-password)
#   - policy homepage       (reads only that one path)
#   - role   homepage       (default SA @ homepage namespace)
#
# SAFE TO RE-RUN: values are written only if absent. Nothing is silently
# rotated — rotating a value without restarting the pod leaves it holding a
# stale credential. See base-apps/homepage/runbook.md for rotation.
#
# How to run (inside the vault-0 pod, matching the donetick pattern):
#
#   kubectl -n vault cp scripts/provision-homepage-vault.sh vault-0:/tmp/prov.sh
#   kubectl -n vault exec -it vault-0 -- sh
#   PLEX_TOKEN=... GRAFANA_USER=homepage GRAFANA_PASSWORD=... sh /tmp/prov.sh
set -eu

: "${PLEX_TOKEN:?set PLEX_TOKEN}"
: "${GRAFANA_USER:?set GRAFANA_USER}"
: "${GRAFANA_PASSWORD:?set GRAFANA_PASSWORD}"

if vault kv get k8s-secrets/homepage >/dev/null 2>&1; then
  echo "k8s-secrets/homepage already exists — leaving values untouched."
else
  vault kv put k8s-secrets/homepage \
    plex-token="$PLEX_TOKEN" \
    grafana-user="$GRAFANA_USER" \
    grafana-password="$GRAFANA_PASSWORD"
  echo "wrote k8s-secrets/homepage"
fi

vault policy write homepage - <<'EOF'
path "k8s-secrets/data/homepage" {
  capabilities = ["read"]
}
path "k8s-secrets/metadata/homepage" {
  capabilities = ["read", "list"]
}
EOF
echo "wrote policy homepage"

vault write auth/kubernetes/role/homepage \
  bound_service_account_names=default \
  bound_service_account_namespaces=homepage \
  policies=homepage \
  ttl=24h
echo "wrote role homepage"
```

- [ ] **Step 4: Make it executable and run it**

```bash
chmod +x scripts/provision-homepage-vault.sh
kubectl -n vault cp scripts/provision-homepage-vault.sh vault-0:/tmp/prov.sh
kubectl -n vault exec -it vault-0 -- sh
# then, inside the pod:
PLEX_TOKEN='<TOKEN>' GRAFANA_USER='homepage' GRAFANA_PASSWORD='<PASSWORD>' sh /tmp/prov.sh
```

- [ ] **Step 5: Verify**

Still inside the vault-0 pod:

```sh
vault kv get k8s-secrets/homepage
vault read auth/kubernetes/role/homepage
```

Expected: three properties present, and a role bound to `default` @ `homepage`. Then `exit`.

- [ ] **Step 6: Commit**

The script is committed; the secret values are not.

```bash
git add scripts/provision-homepage-vault.sh
git commit -m "homepage: Vault provisioning script

Creates k8s-secrets/homepage (Plex token, Grafana viewer creds), a policy
scoped to that single path, and a kubernetes-auth role for the default SA
in the homepage namespace. Idempotent; never rotates existing values."
```

---

### Task 4: Deploy the core Homepage app

Deploys a working, reachable-by-port-forward Homepage with **no exposure and no widgets**. At the end of this task the dashboard renders with zero tiles — that is the correct outcome, since nothing is annotated until Task 7.

**Files:**
- Create: `base-apps/homepage.yaml`
- Create: `base-apps/homepage/configmap.yaml`
- Create: `base-apps/homepage/serviceaccount.yaml`
- Create: `base-apps/homepage/deployments.yaml`
- Create: `base-apps/homepage/services.yaml`
- Create: `base-apps/homepage/secret-store.yaml`

**Interfaces:**
- Consumes: the Vault role `homepage` from Task 3.
- Produces: Service `homepage:3000` in namespace `homepage`, consumed by Task 5's HTTPRoute. ConfigMap key `services.yaml`, extended by Task 6.

- [ ] **Step 1: Create the Argo CD Application**

`base-apps/homepage.yaml`:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  finalizers:
    - resources-finalizer.argocd.argoproj.io
  name: homepage
  namespace: argo-cd
spec:
  project: default
  source:
    repoURL: https://github.com/arigsela/kubernetes
    targetRevision: main
    path: base-apps/homepage
    directory:
      # Backstage entity + TechDocs config, not Kubernetes manifests.
      exclude: '{catalog-info.yaml,mkdocs.yml}'
  destination:
    server: https://kubernetes.default.svc
    namespace: homepage
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
```

The `directory.exclude` is required by `validate-agent-docs.py` from Task 8 onward, and is harmless before then.

- [ ] **Step 2: Create the ServiceAccount and RBAC**

`base-apps/homepage/serviceaccount.yaml`:

```yaml
# Homepage discovers tiles by reading HTTPRoutes cluster-wide, and reads pods
# and nodes for health dots and the cluster-resources widget. This is the one
# new privilege this app introduces: cluster-wide READ on pods and namespaces.
#
# Narrower than upstream's example manifest, which also grants ingresses
# (networking.k8s.io/extensions) and traefik.io ingressroutes. This cluster runs
# neither — all routing is Gateway API — so those rules are omitted.
apiVersion: v1
kind: ServiceAccount
metadata:
  name: homepage
  namespace: homepage
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: homepage
rules:
  - apiGroups: [""]
    resources: [namespaces, pods, nodes]
    verbs: [get, list]
  - apiGroups: [gateway.networking.k8s.io]
    resources: [httproutes, gateways]
    verbs: [get, list]
  - apiGroups: [metrics.k8s.io]
    resources: [nodes, pods]
    verbs: [get, list]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: homepage
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: homepage
subjects:
  - kind: ServiceAccount
    name: homepage
    namespace: homepage
```

- [ ] **Step 3: Create the ConfigMap**

`base-apps/homepage/configmap.yaml`. All eight keys must exist even when empty — Homepage expects the full set.

```yaml
# ALL EIGHT FILES MUST EXIST, even empty ones: Homepage expects the full set.
#
# These are mounted as individual subPath files (see deployments.yaml), and
# Kubernetes NEVER propagates ConfigMap updates into subPath mounts. Editing
# this file therefore syncs cleanly and changes NOTHING until the pod restarts.
# After any edit here, recompute the checksum in deployments.yaml:
#   shasum -a 256 base-apps/homepage/configmap.yaml | cut -c1-16
apiVersion: v1
kind: ConfigMap
metadata:
  name: homepage
  namespace: homepage
data:
  settings.yaml: |
    title: Homelab
    headerStyle: boxed
    layout:
      GitOps & Delivery:
        style: row
        columns: 3
      Automation:
        style: row
        columns: 2
      Observability:
        style: row
        columns: 2
      Platform:
        style: row
        columns: 3
      AI & Agents:
        style: row
        columns: 4
      Home:
        style: row
        columns: 3

  kubernetes.yaml: |
    # cluster: use the in-cluster ServiceAccount.
    # gateway: discover tiles from Gateway API HTTPRoute annotations.
    # ingress/traefik stay off — this cluster uses neither.
    mode: cluster
    gateway: true
    ingress: false

  services.yaml: ""

  widgets.yaml: |
    - kubernetes:
        cluster:
          show: true
          cpu: true
          memory: true
          showLabel: true
          label: cluster
        nodes:
          show: true
          cpu: true
          memory: true
          showLabel: true

  bookmarks.yaml: ""
  docker.yaml: ""
  custom.css: ""
  custom.js: ""
```

- [ ] **Step 4: Create the Service**

`base-apps/homepage/services.yaml`:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: homepage
  namespace: homepage
  labels:
    app: homepage
    app.kubernetes.io/name: homepage
spec:
  type: ClusterIP
  ports:
    - port: 3000
      targetPort: 3000
      protocol: TCP
      name: http
  selector:
    app: homepage
```

- [ ] **Step 5: Create the SecretStore**

`base-apps/homepage/secret-store.yaml`:

```yaml
apiVersion: external-secrets.io/v1
kind: SecretStore
metadata:
  name: vault-backend
  namespace: homepage
spec:
  provider:
    vault:
      server: "http://vault.vault.svc.cluster.local:8200"
      path: "k8s-secrets"
      version: "v2"
      auth:
        kubernetes:
          mountPath: "kubernetes"
          role: "homepage"
          serviceAccountRef:
            name: "default"
```

Note the Vault role authenticates as the **`default`** ServiceAccount (matching Task 3's role binding and the donetick pattern), while the pod runs as the **`homepage`** ServiceAccount for its Kubernetes API reads. These are deliberately different identities.

- [ ] **Step 6: Compute the ConfigMap checksum**

```bash
shasum -a 256 base-apps/homepage/configmap.yaml | cut -c1-16
```

Use the output in the next step.

- [ ] **Step 7: Create the Deployment**

`base-apps/homepage/deployments.yaml`. Replace `<CHECKSUM>` with the value from Step 6.

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: homepage
  namespace: homepage
  labels:
    app: homepage
    app.kubernetes.io/name: homepage
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app: homepage
  template:
    metadata:
      labels:
        app: homepage
        app.kubernetes.io/name: homepage
      annotations:
        # REQUIRED, not cosmetic. The config below is mounted via subPath, and
        # Kubernetes never propagates ConfigMap updates into subPath mounts. If
        # you edit configmap.yaml without bumping this, the change deploys and
        # does nothing. Recompute with:
        #   shasum -a 256 base-apps/homepage/configmap.yaml | cut -c1-16
        checksum/config: "<CHECKSUM>"
    spec:
      serviceAccountName: homepage
      nodeSelector:
        node.kubernetes.io/workload: application
      securityContext:
        runAsNonRoot: true
        runAsUser: 1000
        runAsGroup: 1000
      containers:
        - name: homepage
          image: ghcr.io/gethomepage/homepage:v1.13.2
          imagePullPolicy: IfNotPresent
          env:
            - name: MY_POD_IP
              valueFrom:
                fieldRef:
                  fieldPath: status.podIP
            # Mandatory since v1.0 — Homepage rejects requests for any Host not
            # listed here. The pod IP entry is what lets the kubelet probe pass;
            # dropping it makes the pod fail readiness with no obvious cause.
            - name: HOMEPAGE_ALLOWED_HOSTS
              value: "$(MY_POD_IP):3000,home.arigsela.com"
          ports:
            - containerPort: 3000
              name: http
          livenessProbe:
            httpGet:
              path: /
              port: 3000
            initialDelaySeconds: 15
            periodSeconds: 30
          readinessProbe:
            httpGet:
              path: /
              port: 3000
            initialDelaySeconds: 5
            periodSeconds: 10
          resources:
            requests:
              cpu: 50m
              memory: 128Mi
            limits:
              memory: 512Mi
          volumeMounts:
            - name: config
              mountPath: /app/config/settings.yaml
              subPath: settings.yaml
            - name: config
              mountPath: /app/config/services.yaml
              subPath: services.yaml
            - name: config
              mountPath: /app/config/widgets.yaml
              subPath: widgets.yaml
            - name: config
              mountPath: /app/config/kubernetes.yaml
              subPath: kubernetes.yaml
            - name: config
              mountPath: /app/config/bookmarks.yaml
              subPath: bookmarks.yaml
            - name: config
              mountPath: /app/config/docker.yaml
              subPath: docker.yaml
            - name: config
              mountPath: /app/config/custom.css
              subPath: custom.css
            - name: config
              mountPath: /app/config/custom.js
              subPath: custom.js
            - name: logs
              mountPath: /app/config/logs
      volumes:
        - name: config
          configMap:
            name: homepage
        - name: logs
          emptyDir: {}
```

- [ ] **Step 8: Validate locally**

```bash
yamllint base-apps/homepage/ base-apps/homepage.yaml
kubeconform -ignore-missing-schemas -summary base-apps/homepage/*.yaml base-apps/homepage.yaml
```

Expected: no yamllint errors; kubeconform reports 0 invalid. Fix anything reported before committing — CI runs both.

- [ ] **Step 9: Commit (do not push)**

```bash
git add base-apps/homepage.yaml base-apps/homepage/
git commit -m "homepage: deploy dashboard core (no exposure, no widgets yet)

Deployment, Service, ConfigMap, SecretStore, and a read-only ClusterRole
scoped to Gateway API rather than upstream's ingress/traefik rules.
Renders zero tiles until HTTPRoutes are annotated."
```

- [ ] **Step 10: Record the deferred live checks**

**DEFERRED — do not attempt.** Argo CD syncs `main`; this branch deploys nothing.
Record these in your report as deferred, to be run after merge:

```bash
# post-merge only
kubectl -n homepage get pods -w
# expect one pod reaching 1/1 Running. On CrashLoopBackOff check
#   kubectl -n homepage logs deploy/homepage
# — a non-root UID mismatch or a missing config file are the likely causes.

kubectl -n homepage port-forward deploy/homepage 3000:3000 >/dev/null 2>&1 &
sleep 3
curl -sS -o /dev/null -w '%{http_code}\n' -H 'Host: home.arigsela.com' http://localhost:3000/
kill %1
# expect 200. A 403 or empty response means HOMEPAGE_ALLOWED_HOSTS is wrong.
# In a browser the page should show the "Homelab" title and the cluster/node
# resource widget with NO service tiles — correct at this stage.
```

What you CAN verify now, and must: that `yamllint` and `kubeconform` pass (Step 8),
that the ConfigMap contains all eight keys, and that the `checksum/config` value in
`deployments.yaml` matches `shasum -a 256 base-apps/homepage/configmap.yaml | cut -c1-16`.

---

### Task 5: Expose the dashboard at home.arigsela.com

**Files:**
- Create: `base-apps/homepage/certificate.yaml`
- Create: `base-apps/homepage/reference-grant.yaml`
- Create: `base-apps/homepage/httproute.yaml`
- Modify: `base-apps/istio-ingress/gateway.yaml`
- Modify: `base-apps/istio-ingress/authorizationpolicy.yaml`

**Interfaces:**
- Consumes: Service `homepage:3000` from Task 4.
- Produces: `https://home.arigsela.com` serving Homepage, restricted to the existing allow-list.

- [ ] **Step 1: Create the Route 53 A record**

There is no external-dns in this repo — DNS records are created by hand.

In the AWS console (or CLI), add an **A record** for `home.arigsela.com` in the `arigsela.com` hosted zone, pointing at the same target as `chores.arigsela.com`. Check the existing record first so you match it exactly:

```bash
aws route53 list-resource-record-sets --hosted-zone-id <ZONE_ID> \
  --query "ResourceRecordSets[?Name=='chores.arigsela.com.']"
```

Verify:

```bash
dig +short home.arigsela.com
```

Expected: the same address as `dig +short chores.arigsela.com`.

- [ ] **Step 2: Create the Certificate**

`base-apps/homepage/certificate.yaml`:

```yaml
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: homepage-tls
  namespace: homepage
spec:
  secretName: homepage-tls
  dnsNames:
    - home.arigsela.com
  issuerRef:
    # Not letsencrypt-prod: its only solver is http01.ingress.class=nginx, which
    # nothing satisfies since the Istio cutover.
    name: letsencrypt-route53
    kind: ClusterIssuer
```

- [ ] **Step 3: Create the ReferenceGrant**

`base-apps/homepage/reference-grant.yaml`:

```yaml
# Lets the ingress Gateway in istio-ingress read this namespace's TLS secret.
apiVersion: gateway.networking.k8s.io/v1beta1
kind: ReferenceGrant
metadata:
  name: gateway-to-homepage-tls
  namespace: homepage
spec:
  from:
    - group: gateway.networking.k8s.io
      kind: Gateway
      namespace: istio-ingress
  to:
    - group: ""
      kind: Secret
      name: homepage-tls
```

- [ ] **Step 4: Create the HTTPRoute**

`base-apps/homepage/httproute.yaml`:

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: homepage
  namespace: homepage
spec:
  parentRefs:
    - name: main
      namespace: istio-ingress
      sectionName: https-homepage
  hostnames:
    - home.arigsela.com
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /
      backendRefs:
        - name: homepage
          port: 3000
```

This route deliberately carries **no** `gethomepage.dev/*` annotations — the dashboard does not need a tile pointing at itself.

- [ ] **Step 5: Add the Gateway listener**

In `base-apps/istio-ingress/gateway.yaml`, add to `spec.listeners` (place it after the `https-chores` listener to keep related entries together):

```yaml
    - name: https-homepage
      protocol: HTTPS
      port: 443
      hostname: home.arigsela.com
      tls:
        mode: Terminate
        certificateRefs:
          - kind: Secret
            name: homepage-tls
            namespace: homepage
      allowedRoutes:
        namespaces:
          from: All
```

- [ ] **Step 6: Add the AuthorizationPolicy rule**

**This must land in the same commit as Step 5.** The AuthorizationPolicy is deny-by-default for the Gateway it selects, so a listener with no matching rule fails closed — producing a 403 that presents as a routing bug.

In `base-apps/istio-ingress/authorizationpolicy.yaml`, add to `spec.rules`, copying the source list used by the other restricted hosts:

```yaml
    # homepage — restricted. The dashboard enumerates every service in the
    # homelab and Homepage itself ships no authentication (upstream states none
    # is planned), so the allow-list is the ONLY control here.
    - to:
        - operation:
            hosts:
              - home.arigsela.com
              - home.arigsela.com:*
      from:
        - source:
            ipBlocks:
              - 73.7.190.154/32
              - 170.85.56.189/32
              - 170.85.130.202/32
              - 104.28.177.82/32
```

- [ ] **Step 7: Validate locally**

```bash
yamllint base-apps/homepage/ base-apps/istio-ingress/gateway.yaml base-apps/istio-ingress/authorizationpolicy.yaml
kubeconform -ignore-missing-schemas -summary base-apps/homepage/*.yaml
```

Expected: clean.

- [ ] **Step 8: Commit**

```bash
git add base-apps/homepage/ base-apps/istio-ingress/gateway.yaml base-apps/istio-ingress/authorizationpolicy.yaml
git commit -m "homepage: expose at home.arigsela.com

Certificate, ReferenceGrant, HTTPRoute, plus the Gateway listener and the
AuthorizationPolicy allow rule — the last two together, since the policy is
deny-by-default and a listener without a rule fails closed."
```

Do not push.

- [ ] **Step 9: Record the deferred certificate and end-to-end checks**

**DEFERRED — do not attempt.** Both need the branch merged and synced. Record in
your report:

```bash
# post-merge only
kubectl -n homepage get certificate homepage-tls -w
# expect READY=True within a few minutes. The listener sits unprogrammed until
# then; expected and self-healing. If it stalls:
#   kubectl -n homepage describe certificaterequest
# — stale Route 53 credentials in the ESO-managed secret are the known failure.

curl -sS -o /dev/null -w '%{http_code}\n' https://home.arigsela.com/
# expect 200 from an allow-listed address, and 403 from anywhere else.
```

The allow-list denial check is the one that matters most and is easiest to skip —
carry it into your report explicitly so it is not lost.

What you CAN verify now, and must: that the Gateway listener's `certificateRefs`
name and namespace match the Certificate's `secretName` and namespace exactly, that
the HTTPRoute's `sectionName` matches the new listener's `name`, and that the
AuthorizationPolicy rule was added in the same commit as the listener.

- [ ] **Step 10: Note the post-merge allow-list test**

**DEFERRED.** From a non-allow-listed network (phone on cellular, or any VPN exit):

```bash
curl -sS -o /dev/null -w '%{http_code}\n' https://home.arigsela.com/
```

Expected: `403`. **Do not skip this** — an allow-list that silently admits everyone is the one failure mode that matters here, and it looks identical to success from your desk.

---

### Task 6: Add the four widgets

**Files:**
- Create: `base-apps/homepage/external-secrets.yaml`
- Modify: `base-apps/homepage/configmap.yaml` (the `services.yaml` key)
- Modify: `base-apps/homepage/deployments.yaml` (env vars + checksum)

**Interfaces:**
- Consumes: Vault path from Task 3; `argocd_app_info` from Task 1; Plex reachability from Task 2.
- Produces: three static service tiles with live data. Task 7 must not annotate Argo CD, Grafana, or Plex, to avoid duplicate tiles.

- [ ] **Step 1: Create the ExternalSecret**

`base-apps/homepage/external-secrets.yaml`:

```yaml
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: homepage-secrets
  namespace: homepage
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: vault-backend
    kind: SecretStore
  target:
    name: homepage-secrets
    creationPolicy: Owner
  data:
    - secretKey: plex-token
      remoteRef:
        key: homepage
        property: plex-token
    # A dedicated Grafana Viewer account, NOT the admin credentials in
    # base-apps/logging/grafana-admin-external-secret.yaml. Homepage is
    # unauthenticated; admin creds must not sit in its environment.
    - secretKey: grafana-user
      remoteRef:
        key: homepage
        property: grafana-user
    - secretKey: grafana-password
      remoteRef:
        key: homepage
        property: grafana-password
```

- [ ] **Step 2: Commit it separately, and record the deferred sync check**

Commit just this file first, so that after merge a Vault problem surfaces on its own rather than mixed into a config change:

```bash
git add base-apps/homepage/external-secrets.yaml
git commit -m "homepage: sync widget credentials from Vault"
```

**DEFERRED — do not attempt.** Record in your report:

```bash
# post-merge only
kubectl -n homepage get externalsecret homepage-secrets -w
# expect STATUS=SecretSynced. SecretSyncedError means the Vault role or policy
# from Task 3 is wrong — fix that before trusting any widget.
```

What you CAN verify now: that the three `remoteRef.property` values here
(`plex-token`, `grafana-user`, `grafana-password`) exactly match the keys written by
`scripts/provision-homepage-vault.sh`, and that `secretStoreRef.name` matches the
`SecretStore` created in Task 4 (`vault-backend`). A typo in either is the most
likely cause of the deferred check failing later.

- [ ] **Step 3: Add the widget env vars to the Deployment**

In `base-apps/homepage/deployments.yaml`, add to the container's `env` list, after `HOMEPAGE_ALLOWED_HOSTS`:

```yaml
            # Referenced as {{HOMEPAGE_VAR_*}} in configmap.yaml. Homepage
            # substitutes these in config files ONLY — never in annotations,
            # which is why every widget-bearing tile is static.
            - name: HOMEPAGE_VAR_PLEX_TOKEN
              valueFrom:
                secretKeyRef:
                  name: homepage-secrets
                  key: plex-token
            - name: HOMEPAGE_VAR_GRAFANA_USER
              valueFrom:
                secretKeyRef:
                  name: homepage-secrets
                  key: grafana-user
            - name: HOMEPAGE_VAR_GRAFANA_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: homepage-secrets
                  key: grafana-password
```

- [ ] **Step 4: Populate `services.yaml` in the ConfigMap**

In `base-apps/homepage/configmap.yaml`, replace `services.yaml: ""` with the block below. Substitute `<WINDOWS_LAN_IP>` from Task 2, and adjust the PromQL label values if Task 1 Step 6 showed different ones.

```yaml
  # Only widget-bearing tiles live here. Link-only tiles are discovered from
  # gethomepage.dev/* annotations on each app's own httproute.yaml. The split
  # exists because Homepage substitutes {{HOMEPAGE_VAR_*}} in config files but
  # not in annotations — a credential must never appear in an annotation.
  services.yaml: |
    - GitOps & Delivery:
        - Argo CD:
            href: https://argocd.arigsela.com
            description: GitOps continuous delivery
            icon: argo-cd.png
            widget:
              # Driven by Prometheus rather than an Argo CD API token: creating
              # an apiKey account means editing argocd-cm, and this repo's
              # Terraform module writes to the deprecated server.config.* path
              # the chart ignores. See templates/agent-docs/README.md.
              type: prometheusmetric
              url: http://prometheus.logging.svc.cluster.local:9090
              metrics:
                - label: Apps
                  query: count(argocd_app_info)
                - label: Synced
                  query: count(argocd_app_info{sync_status="Synced"})
                - label: OutOfSync
                  query: count(argocd_app_info{sync_status="OutOfSync"})
                - label: Degraded
                  query: count(argocd_app_info{health_status="Degraded"})

    - Observability:
        - Grafana:
            href: https://grafana.arigsela.com
            description: Dashboards and alerting
            icon: grafana.png
            widget:
              type: grafana
              version: 2
              url: http://grafana.logging.svc.cluster.local:3000
              username: "{{HOMEPAGE_VAR_GRAFANA_USER}}"
              password: "{{HOMEPAGE_VAR_GRAFANA_PASSWORD}}"

    - Home:
        - Plex:
            # Runs on the Windows/WSL2 box, not in the cluster. Reachable only
            # because of the WSL2 mirrored-networking change documented in
            # runbook.md. A DHCP lease change here silently blanks the widget.
            href: http://<WINDOWS_LAN_IP>:32400/web
            description: Media server
            icon: plex.png
            widget:
              type: plex
              url: http://<WINDOWS_LAN_IP>:32400
              key: "{{HOMEPAGE_VAR_PLEX_TOKEN}}"
```

- [ ] **Step 5: Confirm the Grafana in-cluster service name and port**

The widget uses the in-cluster address, not the public hostname, so it does not depend on the ingress allow-list.

```bash
kubectl -n logging get svc grafana -o jsonpath='{.metadata.name}:{.spec.ports[0].port}{"\n"}'
```

Expected: `grafana:3000`. If the port differs, correct the `url` in Step 4.

- [ ] **Step 6: Recompute the checksum**

```bash
shasum -a 256 base-apps/homepage/configmap.yaml | cut -c1-16
```

Update `checksum/config` in `base-apps/homepage/deployments.yaml`. **Skipping this means the widgets never appear** and everything looks correctly deployed.

- [ ] **Step 7: Validate and commit**

```bash
yamllint base-apps/homepage/
kubeconform -ignore-missing-schemas -summary base-apps/homepage/*.yaml
git add base-apps/homepage/configmap.yaml base-apps/homepage/deployments.yaml
git commit -m "homepage: add Plex, Grafana, and Argo CD widgets

Argo CD reads Prometheus instead of the Argo CD API, so it needs no token.
Grafana uses a dedicated Viewer account. Plex points at the WSL2 host."
```

Do not push.

- [ ] **Step 8: Verify the widget backends now, defer the rendering check**

Two of the three widget backends are live systems reachable from this machine
regardless of branch — **verify them now**, because a wrong URL or a dead credential
found here saves a debugging round after merge:

```bash
# Prometheus: the exact PromQL the Argo CD widget will run
kubectl -n logging port-forward svc/prometheus 9090:9090 >/dev/null 2>&1 &
sleep 3
for q in 'count(argocd_app_info)' \
         'count(argocd_app_info{sync_status="Synced"})' \
         'count(argocd_app_info{sync_status="OutOfSync"})' \
         'count(argocd_app_info{health_status="Degraded"})'; do
  printf '%s => ' "$q"
  curl -sG --data-urlencode "query=$q" http://localhost:9090/api/v1/query \
    | python3 -c 'import json,sys; r=json.load(sys.stdin)["data"]["result"]; print(r[0]["value"][1] if r else "EMPTY — label mismatch")'
done
kill %1

# Plex: the exact URL and token the widget will use
curl -sS -o /dev/null -w '%{http_code}\n' "http://<WINDOWS_LAN_IP>:32400/library/sections?X-Plex-Token=<TOKEN>"
```

Expected: four numbers (none `EMPTY`), and `200` from Plex. An `EMPTY` result means
the PromQL label values differ from Task 1 Step 6 — fix the query in `configmap.yaml`
now, not after merge.

**DEFERRED — do not attempt:** `kubectl -n homepage rollout status deploy/homepage`,
then opening `https://home.arigsela.com` and confirming tile by tile:

- **Argo CD** — four numbers; `Apps` should match `kubectl get applications -n argo-cd | wc -l` minus the header.
- **Grafana** — a dashboard count, not an error.
- **Plex** — library counts and stream count.
- **Cluster resources** — CPU/memory bars for the cluster and each node.

Any tile showing an error or blank stats: check `kubectl -n homepage logs deploy/homepage` for the specific upstream failure. The `runbook.md` table in Task 8 maps each symptom to its cause.

---

### Task 7: Annotate the 14 HTTPRoutes

This is where the dashboard becomes useful. Each annotation is additive and independent — a mistake degrades one tile, not the dashboard.

**Files (modify, one annotation block each):**

`base-apps/coroot/httproute.yaml`, `argo-rollouts/httproute.yaml`, `argo-workflows/httproute.yaml`, `atlantis/httproute.yaml`, `backstage/httproute.yaml`, `dex/httproute.yaml`, `kagent/httproute.yaml`, `kagent/httproute-mcp.yaml`, `oncall-agent/httproute.yaml`, `oncall-crewai/httproute.yaml`, `vault/httproute.yaml`, `weather-kitchen-frontend/httproute.yaml`, `n8n/httproute.yaml`, `donetick/httproute.yaml`

**Interfaces:**
- Consumes: the running Homepage from Task 5, with `gateway: true` already set.
- Produces: 14 discovered tiles. Adds nothing later tasks depend on.

- [ ] **Step 1: Confirm which weather-kitchen route owns the hostname**

`weather-kitchen.arigsela.com` is served by **two** HTTPRoutes (frontend and backend, path-split). Homepage discovers per-HTTPRoute, so annotating both produces two duplicate tiles.

```bash
grep -l 'weather-kitchen.arigsela.com' base-apps/weather-kitchen-*/httproute.yaml
```

Annotate **only** `base-apps/weather-kitchen-frontend/httproute.yaml`. This is the only duplicated hostname in the repo; `kagent` has two routes but they carry distinct hostnames and are correctly two tiles.

- [ ] **Step 2: Verify the pod-selector labels**

Label conventions are not uniform here, and a wrong selector renders a permanently-unhealthy tile — worse than no health dot at all.

```bash
for ns in coroot argo-rollouts argo-workflows atlantis backstage dex kagent \
          oncall-agent oncall-crewai vault weather-kitchen n8n donetick; do
  echo "--- $ns"
  kubectl get pods -n "$ns" --show-labels 2>/dev/null | head -3
done
```

Use the table in Step 3, but **correct any row this output contradicts**.

- [ ] **Step 3: Add the annotations**

For each file, add an `annotations:` block under `metadata:`. Example, for `base-apps/donetick/httproute.yaml`:

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: donetick
  namespace: donetick
  annotations:
    # Dashboard tile, discovered by homepage via gateway-api. href is derived
    # from the hostnames below, so the tile follows the host automatically.
    # NEVER add a credential here — annotations are plaintext and get no
    # {{HOMEPAGE_VAR_*}} substitution. Widget tiles live in
    # base-apps/homepage/configmap.yaml instead.
    gethomepage.dev/enabled: "true"
    gethomepage.dev/name: Donetick
    gethomepage.dev/group: Home
    gethomepage.dev/icon: mdi-checkbox-marked-circle-outline
    gethomepage.dev/description: Household chore tracker
    gethomepage.dev/pod-selector: app=donetick
```

Values for all 14 (put the explanatory comment on the first one you edit; the rest need only the annotations):

| File | name | group | icon | pod-selector |
|---|---|---|---|---|
| `coroot/httproute.yaml` | Coroot | Observability | `mdi-radar` | *(omit — ClickHouse pods carry neither label)* |
| `argo-rollouts/httproute.yaml` | Argo Rollouts | GitOps & Delivery | `mdi-rocket-launch` | `app.kubernetes.io/name=argo-rollouts` |
| `argo-workflows/httproute.yaml` | Argo Workflows | Automation | `mdi-sitemap` | `app=server` |
| `atlantis/httproute.yaml` | Atlantis | GitOps & Delivery | `mdi-terraform` | `app=atlantis` |
| `backstage/httproute.yaml` | Backstage | Platform | `backstage.png` | `app=backstage` |
| `dex/httproute.yaml` | Dex | Platform | `mdi-account-key` | `app=dex` |
| `kagent/httproute.yaml` | Kagent | AI & Agents | `mdi-robot` | *(omit — many differently-labelled agent pods)* |
| `kagent/httproute-mcp.yaml` | Kagent MCP | AI & Agents | `mdi-connection` | *(omit — same reason)* |
| `oncall-agent/httproute.yaml` | Oncall Agent | AI & Agents | `mdi-bell-alert` | `app=oncall-agent-api` |
| `oncall-crewai/httproute.yaml` | Oncall CrewAI | AI & Agents | `mdi-account-group` | `app=crewai-frontend` |
| `vault/httproute.yaml` | Vault | Platform | `vault.png` | `app.kubernetes.io/name=vault` |
| `weather-kitchen-frontend/httproute.yaml` | Weather Kitchen | Home | `mdi-weather-partly-cloudy` | `app=weather-kitchen-frontend` |
| `n8n/httproute.yaml` | n8n | Automation | `n8n.png` | `app=n8n` |
| `donetick/httproute.yaml` | Donetick | Home | `mdi-checkbox-marked-circle-outline` | `app=donetick` |

Each `description` should be one short phrase — e.g. Coroot "eBPF observability", Vault "Secrets management", Dex "OIDC identity provider", n8n "Workflow automation", Atlantis "Terraform PR automation", Backstage "Developer portal".

The four `.png` icons resolve against [dashboard-icons](https://github.com/homarr-labs/dashboard-icons). Confirm each exists:

```bash
for i in backstage vault n8n argo-cd grafana plex; do
  printf '%s: ' "$i"
  curl -s -o /dev/null -w '%{http_code}\n' \
    "https://raw.githubusercontent.com/homarr-labs/dashboard-icons/main/png/$i.png"
done
```

Any that return `404` should fall back to an `mdi-` icon.

- [ ] **Step 4: Validate**

```bash
yamllint base-apps/*/httproute*.yaml
kubeconform -ignore-missing-schemas -summary base-apps/*/httproute*.yaml
```

Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add base-apps/*/httproute*.yaml
git commit -m "base-apps: annotate HTTPRoutes for dashboard discovery

Adds gethomepage.dev/* annotations to the 14 link-only public hostnames.
Tile metadata sits beside the route that defines the URL, so a tile follows
its hostname automatically. weather-kitchen is annotated on the frontend
route only — both routes share the hostname and would otherwise duplicate."
```

Do not push.

- [ ] **Step 6: Audit the annotations locally, defer the rendering check**

**Verify now** — this catches the realistic mistakes without needing a cluster:

```bash
# Exactly 14 routes enabled, and NOT weather-kitchen-backend
grep -rl 'gethomepage.dev/enabled' base-apps/*/httproute*.yaml | sort
grep -rl 'gethomepage.dev/enabled' base-apps/weather-kitchen-backend/ && echo "ERROR: backend must not be annotated"

# Every group used must exist in the settings.yaml layout, or the tile renders
# in an unpredictable position or not at all
grep -h 'gethomepage.dev/group' base-apps/*/httproute*.yaml | sed 's/.*group: *//' | sort -u
# -A20, not -A12: the layout block is six groups x 3 lines and -A12 silently
# truncates it, which makes the comparison below pass against a partial list.
grep -A20 'layout:' base-apps/homepage/configmap.yaml | grep -E '^\s{6}\S' | sed 's/://;s/^ *//'
```

Expected: 14 files listed, no backend hit, and every group value appearing in the
layout list. A group name that is not in the layout is the single most common cause
of a "missing" tile.

**DEFERRED — do not attempt.** Annotations are read from the Kubernetes API per
request, unlike `configmap.yaml`, so post-merge the tiles must appear with **no pod
restart** — confirming that is the point of the check:

```bash
# post-merge only
kubectl -n homepage get pods   # note the AGE; it must NOT reset
```

Then reload `https://home.arigsela.com`: expect 17 tiles total (14 discovered + 3
static) across the six groups, each linking to the right hostname, with pod-health
dots on the ones carrying a `pod-selector`.

Any missing tile: check that file's `gethomepage.dev/enabled: "true"` and that its `group` exactly matches a key in `settings.yaml`'s `layout` — a group name that isn't in the layout renders in an unpredictable position or not at all.

---

### Task 8: Agent-docs contract

Adding `homepage` to `scripts/agent-docs-scope.txt` is what turns on enforcement in `validate-agent-docs.py`. Everything in this task must land in one commit or CI fails.

**Files:**
- Create: `base-apps/homepage/catalog-info.yaml`
- Create: `base-apps/homepage/docs.md`
- Create: `base-apps/homepage/runbook.md`
- Create: `base-apps/homepage/mkdocs.yml`
- Modify: `scripts/agent-docs-scope.txt`
- Generated: `base-apps/homepage/docs/index.md`, `base-apps/homepage/docs/runbook.md`, `base-apps/index.md`

**Interfaces:**
- Consumes: everything built in Tasks 1–7.
- Produces: CI-green docs. Nothing depends on this.

- [ ] **Step 1: Create `catalog-info.yaml`**

```yaml
apiVersion: backstage.io/v1alpha1
kind: Component
metadata:
  name: homepage
  namespace: homepage
  annotations:
    agent-docs/path: docs.md
    backstage.io/techdocs-ref: dir:.
    backstage.io/kubernetes-label-selector: 'app=homepage'
    backstage.io/kubernetes-namespace: homepage
  tags: [dashboard, gitops, self-hosted]
spec:
  type: service
  lifecycle: production
  owner: group:default/platform
  system: default/platform
  dependsOn:
    - resource:vault/vault
```

Confirm `system: default/platform` resolves — `validate-catalog-refs.py` fails on a dangling reference. If it does not exist, copy the `system` value from `base-apps/backstage/catalog-info.yaml`.

- [ ] **Step 2: Create `mkdocs.yml`**

```yaml
site_name: homepage
docs_dir: docs
nav:
  - Overview: index.md
  - Runbook: runbook.md
plugins:
  - techdocs-core
```

- [ ] **Step 3: Create `docs.md`**

Frontmatter must match the schema in `templates/agent-docs/README.md`; every path in `sources` must exist.

```markdown
---
type: "Kubernetes App Guide"
title: "homepage"
description: "Homelab dashboard listing every cluster app plus the WSL2-hosted Plex server, with live widgets for Plex, Grafana, Argo CD, and cluster resources."
app: homepage
catalog_entity: homepage
kind: docs
namespace: homepage
last_reviewed: 2026-08-04
status: current
tags: [dashboard, gitops, self-hosted]
sources:
  - base-apps/homepage/configmap.yaml
  - base-apps/homepage/deployments.yaml
  - base-apps/homepage/serviceaccount.yaml
  - base-apps/homepage/external-secrets.yaml
---
```

The body must cover, at minimum:

1. **The tile-sourcing rule, verbatim:** *Tiles with a widget are defined in `configmap.yaml`. Link-only tiles are defined by annotations on their own `httproute.yaml`. Never put a credential in an annotation — annotations are plaintext in Git, and Homepage performs `{{HOMEPAGE_VAR_*}}` substitution only in config files, never in annotations.*
2. **Adding a new app to the dashboard:** add the six `gethomepage.dev/*` annotations to its HTTPRoute; the tile appears with no restart and no change to this app.
3. **Why `checksum/config` exists:** subPath mounts never receive ConfigMap updates, so a config edit without a checksum bump deploys and does nothing.
4. **Why Argo CD uses `prometheusmetric`:** an apiKey account needs `argocd-cm`, and the Terraform module writes to the deprecated `server.config.*` path the chart ignores (see `templates/agent-docs/README.md`).
5. **Why Grafana has its own Viewer account:** Homepage is unauthenticated, so admin credentials must not sit in its environment.
6. **The external Plex dependency:** WSL2 mirrored networking plus a DHCP reservation, both outside Git.
7. **The RBAC note:** cluster-wide read on pods, namespaces, nodes, HTTPRoutes, and metrics — narrowed from upstream by dropping the Ingress and Traefik rules.

- [ ] **Step 4: Create `runbook.md`**

Same frontmatter but `type: "Kubernetes App Runbook"`, `kind: runbook`, and `title: "homepage runbook"`. The body is symptom → check → fix:

| Symptom | Check | Fix |
|---|---|---|
| Blank page, or requests rejected | `kubectl -n homepage exec deploy/homepage -- printenv HOMEPAGE_ALLOWED_HOSTS` | Must contain `home.arigsela.com` and `$(MY_POD_IP):3000`. Most likely first-boot failure. |
| Edited `configmap.yaml`, Argo synced, nothing changed | Pod `AGE` versus commit time | Bump `checksum/config`. subPath mounts never receive ConfigMap updates. |
| A new app's tile is missing | The `gethomepage.dev/*` annotations on its HTTPRoute | Add `enabled: "true"`; confirm `group` matches a `layout` key in `settings.yaml`. No restart needed. |
| Two tiles for one app | Whether two HTTPRoutes share the hostname | Annotate only the frontend route (this is why `weather-kitchen-backend` is unannotated). |
| Plex stats blank, link still works | `kubectl run plex-probe --rm -it --restart=Never --image=curlimages/curl:8.10.1 -- curl -m5 http://<WINDOWS_LAN_IP>:32400/identity` | WSL2 mirrored networking off after a Windows update, or the DHCP lease moved. |
| Argo CD tile blank or zeros | `count(argocd_app_info)` in Prometheus | `controller.metrics.enabled` reverted, or the metrics Service lost its scrape annotation. |
| Grafana tile errors | `curl -u '<user>:<pass>' https://grafana.arigsela.com/api/search` | Viewer account deleted or password changed; update Vault and restart the pod. |
| 403 on every host, not just this one | Your source IP against `authorizationpolicy.yaml` | ISP address moved off the allow-list. Affects all apps; the dashboard is just where you notice first. |

Include a **credential rotation** section: update Vault, wait for the ExternalSecret refresh (1h) or force it with `kubectl -n homepage annotate externalsecret homepage-secrets force-sync=$(date +%s) --overwrite`, then `kubectl -n homepage rollout restart deploy/homepage` — env vars are read once at startup.

- [ ] **Step 5: Add to scope and run the generators**

```bash
echo "homepage" >> scripts/agent-docs-scope.txt
python3 scripts/gen-okf.py --repo-root .
python3 scripts/gen-techdocs.py --repo-root .
```

Never hand-edit `base-apps/index.md` — it is generated from the `description:` frontmatter.

- [ ] **Step 6: Run the validators locally**

```bash
python3 -m pytest tests/agent-docs/ -q
python3 scripts/validate-agent-docs.py --repo-root .
python3 -m pytest tests/catalog-refs/ -q
python3 scripts/validate-catalog-refs.py --repo-root .
python3 -m pytest tests/techdocs/ -q
python3 scripts/gen-techdocs.py --repo-root . --check
```

Expected: all pass, and the validator reports one more app in scope than before. Fix anything reported — these are the exact commands CI runs.

- [ ] **Step 7: Commit**

```bash
git add base-apps/homepage/ base-apps/index.md scripts/agent-docs-scope.txt
git commit -m "homepage: agent-docs contract

catalog-info.yaml, docs.md, runbook.md, mkdocs.yml, and the scope entry that
turns on validation. Documents the tile-sourcing rule, why config changes need
a checksum bump, and the external WSL2 Plex dependency."
```

Do not push.

- [ ] **Step 8: Run the full CI suite locally**

CI runs on push, which is deferred — but every check it runs works offline, so run
the whole set here. This is the last task, so this is the branch's final gate:

```bash
git status --short          # must be empty; a generated file left uncommitted
                            # is exactly what breaks gen-techdocs --check in CI
yamllint base-apps/homepage/ base-apps/homepage.yaml
kubeconform -ignore-missing-schemas -summary base-apps/homepage/*.yaml base-apps/homepage.yaml
python3 -m pytest tests/agent-docs/ tests/catalog-refs/ tests/techdocs/ -q
python3 scripts/validate-agent-docs.py --repo-root .
python3 scripts/validate-catalog-refs.py --repo-root .
python3 scripts/gen-techdocs.py --repo-root . --check
```

Expected: all pass, `git status` clean, and the agent-docs validator reporting **20**
apps in scope (up from the baseline 19).

**DEFERRED:** `gh run list --limit 3` after merge, to confirm the Validate Manifests
workflow passes on the real commit.

---

## Post-merge verification

**Every live check in this plan collects here.** The branch is developed in an
isolated worktree and Argo CD syncs `main` only, so none of this can run during
implementation. Run the whole list after merging to `main` and pushing — in order,
because a failure early makes the later ones meaningless:

- [ ] Argo CD picked up the app: `kubectl -n argo-cd get app homepage` → Synced/Healthy
- [ ] Pod is up: `kubectl -n homepage get pods` → `1/1 Running`, no CrashLoopBackOff
- [ ] Certificate issued: `kubectl -n homepage get certificate homepage-tls` → `READY=True`
- [ ] Secrets synced: `kubectl -n homepage get externalsecret homepage-secrets` → `SecretSynced`
- [ ] `https://home.arigsela.com` returns `200` from an allow-listed address
- [ ] **and `403` from a non-allow-listed network** (phone on cellular) — the check that actually matters
- [ ] 17 tiles render across the six configured groups
- [ ] All four widgets show real numbers, not errors or zeros
- [ ] The pod has not restarted since the widgets landed: `kubectl -n homepage get pods` (AGE)
- [ ] CI green on `main`: `gh run list --limit 3`

If a widget is blank, the `runbook.md` table written in Task 8 maps each symptom to
its cause — that table exists precisely because these checks were deferred.

## Known follow-ups (deliberately out of scope)

- **Tautulli** would give real now-playing detail; the core Plex API only reports library counts and a stream count.
- **Coroot, Backstage, Vault, n8n, Atlantis, and Kagent have no Homepage widget** and are permanently link-only unless hand-built with the generic `customapi` widget.
- **The WSL2 `portproxy` fallback** (if used instead of mirrored networking) needs a Scheduled Task to survive reboots.
- **`CLAUDE.md` references `@AGENTS.md`, which does not exist** in the repo. Unrelated to this work, but noticed while reading the build/validation contract.
