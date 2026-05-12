# vcluster Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status:** ✅ All phases complete (5/5) — see "Post-execution notes" at the bottom for deviations
**Last Updated:** 2026-05-12
**Spec:** [docs/superpowers/specs/2026-05-12-vcluster-design.md](../superpowers/specs/2026-05-12-vcluster-design.md)
**Runbook:** [docs/reference/vcluster/README.md](../reference/vcluster/README.md) — source of truth for actual usage

## Goal

Deploy [vcluster](https://www.vcluster.com/) OSS to the homelab cluster as a GitOps-managed platform for ephemeral Kubernetes test environments. Deliver one always-on reference vcluster (`sandbox-1`) and document the ad-hoc CLI workflow.

## Architecture (one-paragraph recap)

vcluster runs as a Helm chart in its own namespace per virtual cluster (k3s control plane + sqlite, no PVC). nginx-ingress with `ssl-passthrough` enabled routes API traffic on `*.vcluster.arigsela.com` directly into each vcluster pod, where TLS terminates against a cert-manager-issued certificate. The sample `sandbox-1` is a multi-source ArgoCD Application (Helm chart + git path for Certificate + Ingress); ad-hoc vclusters are created with the `vcluster` CLI using a shared values template in `docs/reference/vcluster/`.

## Tech Stack

- vcluster Helm chart **0.34.0** (https://charts.loft.sh)
- ArgoCD (multi-source Application — requires ≥ 2.6)
- cert-manager + existing `letsencrypt-route53` ClusterIssuer (DNS-01)
- nginx-ingress (`enable-ssl-passthrough` flag)
- vcluster CLI on the workstation

## Success Criteria

- [ ] `kubectl get applications -n argo-cd vcluster-sandbox-1` shows `Synced` / `Healthy`
- [ ] `kubectl get pods -n vcluster-sandbox-1` shows the vcluster pod `Running` (1/1)
- [ ] `vcluster connect sandbox-1 --namespace vcluster-sandbox-1 --server https://sandbox-1.vcluster.arigsela.com` succeeds **without** `--insecure-skip-tls-verify`
- [ ] Inside the vcluster: `kubectl get nodes` succeeds and `kubectl create deployment nginx --image=nginx` produces a synced pod in the host `vcluster-sandbox-1` namespace
- [ ] An ad-hoc CLI vcluster created with the values template works end-to-end and tears down cleanly
- [ ] `docs/reference/vcluster/README.md` exists and contains the full runbook

## Risks & Open Questions (must resolve during implementation)

1. **ArgoCD multi-source support** — verified in Phase 0 (Task 0.3). If < 2.6, fall back to two-Application pattern (see Task 2.1 fallback).
2. **LAN DNS for `*.vcluster.arigsela.com`** — must resolve to a node IP, not Cloudflare Tunnel. Verified in Phase 0 (Task 0.4).
3. **nginx-ingress restart blip** — Phase 1 includes a ~30s ingress unavailability when the controller restarts.
4. **vcluster cert SAN propagation** — `controlPlane.proxy.extraSANs` must end up in the vcluster's internal cert. Verified in Task 2.5; rollout restart is the documented fix if it doesn't propagate on first deploy.

**Note on TLS:** the original spec flagged "vcluster Helm values key for TLS cert injection" as the highest risk. On closer inspection, this risk is eliminated by the design itself: with ssl-passthrough, nginx is a TCP pass-through and the vcluster pod serves its own internal cert. `vcluster connect` writes the matching CA into the kubeconfig, so kubectl trusts the chain natively. The cert-manager Certificate is kept as a placeholder for future browser/curl use but is not required for kubectl. See Task 2.5 for verification details.

---

## Phase 0: Platform Prerequisites

Verify dependencies and install the workstation CLI before touching the cluster.

### Task 0.1: Install vcluster CLI on the workstation

**Files:** None (workstation install)

**Steps:**

- [ ] Install with Homebrew

  ```bash
  brew install loft-sh/tap/vcluster
  ```

- [ ] Verify the install

  ```bash
  vcluster version
  # Expected: vcluster version 0.34.x (or compatible)
  ```

### Task 0.2: Confirm cert-manager ClusterIssuer is reachable

**Files:** None (read-only check)

**Steps:**

- [ ] Verify `letsencrypt-route53` exists and is ready

  ```bash
  kubectl get clusterissuer letsencrypt-route53 -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}'
  # Expected: True
  ```

- [ ] Verify the Route 53 credentials secret is synced

  ```bash
  kubectl -n cert-manager get secret route53-credentials -o jsonpath='{.data}' | grep -q access && echo OK
  # Expected: OK
  ```

### Task 0.3: Confirm ArgoCD version supports multi-source

**Files:** None (read-only check)

**Steps:**

- [ ] Get ArgoCD server version

  ```bash
  kubectl -n argo-cd get deploy argocd-server -o jsonpath='{.spec.template.spec.containers[0].image}'
  # Look for v2.6.0+ in the tag; v2.10+ has multi-source GA
  ```

- [ ] If version < 2.6, switch to the **two-Application fallback** documented in Task 2.1 before proceeding to Phase 2.

### Task 0.4: Confirm LAN DNS plan for `*.vcluster.arigsela.com`

**Files:** None (config check — outside the repo)

**Steps:**

- [ ] Identify a node IP that nginx-ingress is listening on

  ```bash
  kubectl -n ingress-nginx get pods -l app.kubernetes.io/component=controller -o wide
  # Note one of the NODE IPs (e.g., 192.168.0.100). hostNetwork is true, so the
  # node IP is directly serving 80/443.
  ```

- [ ] Add a wildcard A record (or CNAME) for `*.vcluster.arigsela.com` to that node IP in your local DNS resolver (Pi-hole, AdGuard, router). **Quick alternative for first test:** add to `/etc/hosts`:

  ```
  192.168.0.100  sandbox-1.vcluster.arigsela.com
  ```

- [ ] Verify resolution from the workstation

  ```bash
  dig +short sandbox-1.vcluster.arigsela.com
  # Expected: the node IP from step 1
  ```

**Note:** Public DNS for `*.vcluster.arigsela.com` is **not** required for vcluster API access. It IS required for the cert-manager DNS-01 challenge, but that uses the existing `letsencrypt-route53` issuer which writes TXT records via Route 53 — no public A record needed for the subdomain itself.

---

## Phase 1: Enable ssl-passthrough in nginx-ingress

One-time platform change. Restarts the ingress controller.

### Task 1.1: Add enable-ssl-passthrough flag

**Files:** Modify: `base-apps/nginx-ingress/nginx-ingress-controller.yaml`

**Steps:**

- [ ] Edit `base-apps/nginx-ingress/nginx-ingress-controller.yaml`. Add `extraArgs` under `controller:` (keep all existing keys; example shows the new block in context):

  ```yaml
  controller:
    nodeSelector:
      node.kubernetes.io/workload: infrastructure
    tolerations:
    - key: node-role.kubernetes.io/control-plane
      effect: NoSchedule
    kind: DaemonSet
    hostNetwork: true
    extraArgs:
      enable-ssl-passthrough: "true"          # NEW
    service:
      type: ClusterIP
      ports:
        http: 80
        https: 443
    # ... rest of existing config unchanged ...
  ```

- [ ] Commit and push

  ```bash
  git add base-apps/nginx-ingress/nginx-ingress-controller.yaml
  git commit -m "feat(nginx-ingress): enable ssl-passthrough for vcluster API access"
  git push origin main
  ```

**Testing:**

- [ ] Wait for the K3s HelmController to reconcile (~30s) and the new DaemonSet pods to roll out

  ```bash
  kubectl -n ingress-nginx rollout status ds/ingress-nginx-controller --timeout=120s
  ```

- [ ] Confirm the flag is present on the running controller

  ```bash
  kubectl -n ingress-nginx get pod -l app.kubernetes.io/component=controller -o jsonpath='{.items[0].spec.containers[0].args}' | tr ',' '\n' | grep ssl-passthrough
  # Expected: --enable-ssl-passthrough=true
  ```

- [ ] Smoke test: an existing ingress (e.g. `https://atlantis.arigsela.com`) still responds 200/302

  ```bash
  curl -sk -o /dev/null -w "%{http_code}\n" https://atlantis.arigsela.com
  # Expected: a non-5xx response (200, 302, 401 — anything but a 5xx or timeout)
  ```

---

## Phase 2: Deploy the `sandbox-1` reference vcluster

GitOps-managed vcluster instance that validates the full pattern end-to-end.

### Task 2.1: Create the multi-source ArgoCD Application

**Files:** Create: `base-apps/vcluster-sandbox-1.yaml`

**Steps:**

- [ ] Write the Application manifest

  ```yaml
  apiVersion: argoproj.io/v1alpha1
  kind: Application
  metadata:
    name: vcluster-sandbox-1
    namespace: argo-cd
  spec:
    project: default
    destination:
      server: https://kubernetes.default.svc
      namespace: vcluster-sandbox-1
    sources:
      - repoURL: https://charts.loft.sh
        chart: vcluster
        targetRevision: 0.34.0
        helm:
          releaseName: vcluster
          valuesObject:
            controlPlane:
              backingStore:
                etcd:
                  embedded:
                    enabled: false
              statefulSet:
                persistence:
                  volumeClaim:
                    enabled: false
                resources:
                  limits:   { cpu: 1, memory: 512Mi }
                  requests: { cpu: 100m, memory: 256Mi }
              service:
                spec:
                  type: ClusterIP
              proxy:
                extraSANs:
                  - sandbox-1.vcluster.arigsela.com
            sync:
              toHost:
                ingresses:
                  enabled: false
              fromHost:
                storageClasses:
                  enabled: true
      - repoURL: https://github.com/arigsela/kubernetes
        targetRevision: main
        path: base-apps/vcluster-sandbox-1
    syncPolicy:
      automated:
        prune: true
        selfHeal: true
      syncOptions:
        - CreateNamespace=true
        - ServerSideApply=true
  ```

- [ ] **Fallback (if ArgoCD < 2.6):** Split into two Applications. `vcluster-sandbox-1.yaml` keeps only the Helm chart `source:` (singular) block; create a second `vcluster-sandbox-1-extras.yaml` pointing at the git path:

  ```yaml
  apiVersion: argoproj.io/v1alpha1
  kind: Application
  metadata:
    name: vcluster-sandbox-1-extras
    namespace: argo-cd
  spec:
    project: default
    source:
      repoURL: https://github.com/arigsela/kubernetes
      targetRevision: main
      path: base-apps/vcluster-sandbox-1
    destination:
      server: https://kubernetes.default.svc
      namespace: vcluster-sandbox-1
    syncPolicy:
      automated: { prune: true, selfHeal: true }
      syncOptions: [CreateNamespace=true]
  ```

  Do **not** commit both styles — pick one based on Task 0.3.

### Task 2.2: Create the Certificate resource

**Files:** Create: `base-apps/vcluster-sandbox-1/certificate.yaml`

**Steps:**

- [ ] Write the Certificate

  ```yaml
  apiVersion: cert-manager.io/v1
  kind: Certificate
  metadata:
    name: vcluster-tls
    namespace: vcluster-sandbox-1
  spec:
    secretName: vcluster-tls
    issuerRef:
      name: letsencrypt-route53
      kind: ClusterIssuer
    commonName: sandbox-1.vcluster.arigsela.com
    dnsNames:
      - sandbox-1.vcluster.arigsela.com
  ```

### Task 2.3: Create the Ingress resource

**Files:** Create: `base-apps/vcluster-sandbox-1/ingress.yaml`

**Steps:**

- [ ] Write the Ingress

  ```yaml
  apiVersion: networking.k8s.io/v1
  kind: Ingress
  metadata:
    name: vcluster
    namespace: vcluster-sandbox-1
    annotations:
      nginx.ingress.kubernetes.io/ssl-passthrough: "true"
      nginx.ingress.kubernetes.io/backend-protocol: "HTTPS"
  spec:
    ingressClassName: nginx
    rules:
      - host: sandbox-1.vcluster.arigsela.com
        http:
          paths:
            - path: /
              pathType: Prefix
              backend:
                service:
                  name: vcluster
                  port: { number: 443 }
  ```

### Task 2.4: Commit, push, and verify ArgoCD picks it up

**Steps:**

- [ ] Commit and push

  ```bash
  git add base-apps/vcluster-sandbox-1.yaml base-apps/vcluster-sandbox-1/
  git commit -m "feat(vcluster): deploy sandbox-1 reference vcluster"
  git push origin main
  ```

- [ ] Wait for ArgoCD to discover and sync

  ```bash
  # Poll until the Application appears (master-app runs every ~3min)
  until kubectl -n argo-cd get application vcluster-sandbox-1 >/dev/null 2>&1; do
    echo "Waiting for ArgoCD to discover the app..."; sleep 10
  done
  kubectl -n argo-cd get application vcluster-sandbox-1
  ```

- [ ] Wait for the vcluster pod to come up

  ```bash
  kubectl -n vcluster-sandbox-1 rollout status sts/vcluster --timeout=300s
  # Expected: statefulset rolling update complete
  ```

### Task 2.5: Verify TLS chain end-to-end

**Background:** With ssl-passthrough, nginx is a TCP proxy — it never presents the Let's Encrypt cert. The vcluster pod presents its own internal cert (signed by vcluster's internal CA, with SANs from `controlPlane.proxy.extraSANs`). `vcluster connect` extracts that CA from the in-cluster secret `vc-vcluster` and embeds it into `certificate-authority-data` in the kubeconfig it writes — so kubectl trusts the chain natively without any `--insecure-skip-tls-verify`.

The Let's Encrypt Certificate (Task 2.2) is **not used for kubectl**. It exists so the same URL works in a browser/curl (e.g. exploratory `curl https://sandbox-1.vcluster.arigsela.com/healthz`) if you decide to wire it into the vcluster pod later. For now, it's harmless deadweight in the namespace.

**Steps:**

- [ ] Verify what cert the endpoint presents (informational)

  ```bash
  echo | openssl s_client -connect sandbox-1.vcluster.arigsela.com:443 \
    -servername sandbox-1.vcluster.arigsela.com 2>/dev/null \
    | openssl x509 -noout -issuer -subject -ext subjectAltName
  ```

  Expected: issuer is the vcluster's internal CA (`CN=...vcluster-ca...`), SAN includes `sandbox-1.vcluster.arigsela.com`. **This is correct** — it's what kubectl-via-kubeconfig expects.

- [ ] Verify the cert-manager Certificate is Ready (for future browser/curl use)

  ```bash
  kubectl -n vcluster-sandbox-1 get certificate vcluster-tls -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}'
  # Expected: True
  ```

- [ ] If the SAN check above shows `sandbox-1.vcluster.arigsela.com` is **missing** from the vcluster cert: the `controlPlane.proxy.extraSANs` value didn't propagate. Restart the vcluster sts to force cert regeneration:

  ```bash
  kubectl -n vcluster-sandbox-1 rollout restart sts/vcluster
  kubectl -n vcluster-sandbox-1 rollout status sts/vcluster --timeout=180s
  ```

  Re-run the openssl check.

- [ ] **Optional (future enhancement, NOT required for kubectl):** to also make browser access trust the chain, mount the LE secret into the vcluster pod via `controlPlane.statefulSet.persistence.addVolumes` and configure vcluster's proxy to serve it. Skip for now — track as a follow-up if browser access becomes a real need.

### Task 2.6: End-to-end validation

**Steps:**

- [ ] Connect to the vcluster via the ingress URL

  ```bash
  vcluster connect sandbox-1 --namespace vcluster-sandbox-1 \
    --server https://sandbox-1.vcluster.arigsela.com \
    --update-current=false \
    --kube-config ./kubeconfig.yaml
  export KUBECONFIG=$(pwd)/kubeconfig.yaml
  ```

- [ ] Verify cluster identity

  ```bash
  kubectl cluster-info
  # Expected: Kubernetes control plane is running at https://sandbox-1.vcluster.arigsela.com
  kubectl get nodes
  # Expected: one synthetic node (the vcluster's view of the host)
  ```

- [ ] Deploy a workload inside the vcluster and verify host-side sync

  ```bash
  kubectl create deployment nginx --image=nginx
  kubectl rollout status deployment/nginx --timeout=60s
  ```

- [ ] In a separate terminal (host kubeconfig), confirm the pod is synced to the host namespace

  ```bash
  unset KUBECONFIG  # back to host
  kubectl -n vcluster-sandbox-1 get pods -l vcluster.loft.sh/managed-by=vcluster
  # Expected: one nginx-* pod Running
  ```

- [ ] Clean up the test workload from inside the vcluster

  ```bash
  export KUBECONFIG=$(pwd)/kubeconfig.yaml
  kubectl delete deployment nginx
  ```

---

## Phase 3: Kyverno & ECR interaction sanity check

Confirm existing platform policies behave correctly against vcluster-synced pods.

### Task 3.1: Verify Kyverno does not break the vcluster control plane

**Steps:**

- [ ] List Kyverno violations in the vcluster namespace

  ```bash
  kubectl -n vcluster-sandbox-1 get policyreport -o wide
  # Expected: any "fail" results are for Audit-mode policies (acceptable)
  ```

- [ ] Confirm vcluster pod has no failed admissions

  ```bash
  kubectl -n vcluster-sandbox-1 get events --sort-by=.lastTimestamp | tail -20
  # Expected: no "denied the request" or admission webhook errors related to Kyverno
  ```

### Task 3.2: Verify ECR pull-secret injection works for synced workloads (only if you push test images to ECR)

This is an optional verification — the host's `inject-ecr-pull-secret` policy fires on synced pods because they're real pods in the host namespace. Skip if you don't use ECR for test images.

**Steps:**

- [ ] Inside the vcluster, create a deployment using an ECR image and confirm it pulls successfully

  ```bash
  export KUBECONFIG=$(pwd)/kubeconfig.yaml
  kubectl create deployment ecr-test --image=<your-account>.dkr.ecr.<region>.amazonaws.com/<repo>:<tag>
  kubectl rollout status deployment/ecr-test --timeout=120s
  ```

- [ ] Confirm host-side pod has imagePullSecrets

  ```bash
  unset KUBECONFIG
  kubectl -n vcluster-sandbox-1 get pod -l vcluster.loft.sh/managed-by=vcluster -o jsonpath='{.items[0].spec.imagePullSecrets}'
  # Expected: [{"name":"ecr-registry"}]
  ```

- [ ] Clean up

  ```bash
  export KUBECONFIG=$(pwd)/kubeconfig.yaml
  kubectl delete deployment ecr-test
  ```

---

## Phase 4: Documentation — runbook & templates

Make the ad-hoc workflow self-serve.

### Task 4.1: Create the values template

**Files:** Create: `docs/reference/vcluster/values-template.yaml`

**Steps:**

- [ ] Write the template (identical to `sandbox-1` values minus the per-instance hostname SAN)

  ```yaml
  # docs/reference/vcluster/values-template.yaml
  # Default Helm values for ad-hoc vclusters. Pass with `vcluster create -f`.
  controlPlane:
    backingStore:
      etcd:
        embedded:
          enabled: false
    statefulSet:
      persistence:
        volumeClaim:
          enabled: false
      resources:
        limits:   { cpu: 1, memory: 512Mi }
        requests: { cpu: 100m, memory: 256Mi }
    service:
      spec:
        type: ClusterIP
    # For ingress-style access, add to extraSANs at create time:
    #   --set controlPlane.proxy.extraSANs[0]=<name>.vcluster.arigsela.com
  sync:
    toHost:
      ingresses:
        enabled: false
    fromHost:
      storageClasses:
        enabled: true
  ```

### Task 4.2: Create the Certificate/Ingress envsubst templates

**Files:**
- Create: `docs/reference/vcluster/certificate.tmpl.yaml`
- Create: `docs/reference/vcluster/ingress.tmpl.yaml`

**Steps:**

- [ ] `certificate.tmpl.yaml`:

  ```yaml
  apiVersion: cert-manager.io/v1
  kind: Certificate
  metadata:
    name: vcluster-tls
    namespace: vcluster-${NAME}
  spec:
    secretName: vcluster-tls
    issuerRef:
      name: letsencrypt-route53
      kind: ClusterIssuer
    commonName: ${NAME}.vcluster.arigsela.com
    dnsNames:
      - ${NAME}.vcluster.arigsela.com
  ```

- [ ] `ingress.tmpl.yaml`:

  ```yaml
  apiVersion: networking.k8s.io/v1
  kind: Ingress
  metadata:
    name: vcluster
    namespace: vcluster-${NAME}
    annotations:
      nginx.ingress.kubernetes.io/ssl-passthrough: "true"
      nginx.ingress.kubernetes.io/backend-protocol: "HTTPS"
  spec:
    ingressClassName: nginx
    rules:
      - host: ${NAME}.vcluster.arigsela.com
        http:
          paths:
            - path: /
              pathType: Prefix
              backend:
                service:
                  name: vcluster
                  port: { number: 443 }
  ```

### Task 4.3: Write the runbook

**Files:** Create: `docs/reference/vcluster/README.md`

**Steps:**

- [ ] Write the README with these sections, each with copy-pasteable commands:

  1. **What this is** — one paragraph: ephemeral test clusters, GitOps `sandbox-1` reference, CLI for ad-hoc
  2. **Prerequisites** — `vcluster` CLI, LAN DNS for `*.vcluster.arigsela.com`
  3. **Quick start: port-forward style** (fastest for one-off tests):
     ```bash
     vcluster create my-test --namespace vcluster-my-test \
       -f docs/reference/vcluster/values-template.yaml
     vcluster connect my-test --namespace vcluster-my-test
     # ... use kubectl ...
     vcluster delete my-test --namespace vcluster-my-test
     ```
  4. **Stable URL style** (lasts as long as the vcluster does):
     ```bash
     # Create with SAN baked in
     vcluster create my-test --namespace vcluster-my-test \
       -f docs/reference/vcluster/values-template.yaml \
       --set controlPlane.proxy.extraSANs[0]=my-test.vcluster.arigsela.com

     # Apply Certificate + Ingress
     NAME=my-test envsubst < docs/reference/vcluster/certificate.tmpl.yaml | kubectl apply -f -
     NAME=my-test envsubst < docs/reference/vcluster/ingress.tmpl.yaml | kubectl apply -f -

     # Wait for cert
     kubectl -n vcluster-my-test wait certificate/vcluster-tls --for=condition=Ready --timeout=120s

     # Connect via the stable URL
     vcluster connect my-test --namespace vcluster-my-test \
       --server https://my-test.vcluster.arigsela.com
     ```
  5. **Sleep/wake (CLI vclusters only — not for `sandbox-1`)**:
     ```bash
     vcluster pause my-test --namespace vcluster-my-test
     vcluster resume my-test --namespace vcluster-my-test
     ```
  6. **Teardown**:
     ```bash
     vcluster delete my-test --namespace vcluster-my-test
     kubectl delete ns vcluster-my-test   # if you applied the Cert/Ingress templates
     ```
  7. **Troubleshooting** — common failures:
     - `x509: certificate signed by unknown authority` → check cert wiring per Task 2.5
     - Ingress 404 → check `dig` resolves the hostname and `nginx-ingress` shows the host in its config
     - `vcluster create` times out → check `kubectl get events -n vcluster-<name>` for image pull / quota errors
  8. **Known limitations** — Vault/ESO not wired in, Istio ambient not enrolled, sleep doesn't work for GitOps-managed instances

### Task 4.4: Commit the docs

**Steps:**

- [ ] Commit

  ```bash
  git add docs/reference/vcluster/
  git commit -m "docs(vcluster): add ad-hoc workflow runbook and templates"
  git push origin main
  ```

---

## Phase 5: Ad-hoc workflow validation

Prove the CLI workflow works end-to-end so future use is muscle memory.

### Task 5.1: Create an ad-hoc vcluster following the runbook (stable-URL style)

**Steps:**

- [ ] Set `NAME` and create

  ```bash
  export NAME=adhoc-test
  vcluster create $NAME --namespace vcluster-$NAME \
    -f docs/reference/vcluster/values-template.yaml \
    --set controlPlane.proxy.extraSANs[0]=$NAME.vcluster.arigsela.com
  ```

- [ ] Apply Certificate + Ingress

  ```bash
  NAME=$NAME envsubst < docs/reference/vcluster/certificate.tmpl.yaml | kubectl apply -f -
  NAME=$NAME envsubst < docs/reference/vcluster/ingress.tmpl.yaml | kubectl apply -f -
  kubectl -n vcluster-$NAME wait certificate/vcluster-tls --for=condition=Ready --timeout=180s
  ```

- [ ] (Only needed if you don't have a wildcard DNS record) Add a hosts entry on the workstation

  ```
  192.168.0.100  adhoc-test.vcluster.arigsela.com
  ```

- [ ] Connect

  ```bash
  vcluster connect $NAME --namespace vcluster-$NAME \
    --server https://$NAME.vcluster.arigsela.com \
    --update-current=false \
    --kube-config kubeconfig-adhoc.yaml
  KUBECONFIG=./kubeconfig-adhoc.yaml kubectl get nodes
  # Expected: one synthetic node
  ```

### Task 5.2: Run a workload and verify

**Steps:**

- [ ] Deploy and check

  ```bash
  KUBECONFIG=./kubeconfig-adhoc.yaml kubectl create deployment hello --image=nginx
  KUBECONFIG=./kubeconfig-adhoc.yaml kubectl rollout status deploy/hello --timeout=60s
  kubectl -n vcluster-$NAME get pods -l vcluster.loft.sh/managed-by=vcluster
  # Expected: hello-* pod Running (host view)
  ```

### Task 5.3: Tear down cleanly

**Steps:**

- [ ] Delete

  ```bash
  vcluster delete $NAME --namespace vcluster-$NAME
  kubectl delete ns vcluster-$NAME --wait=false
  ```

- [ ] Verify gone

  ```bash
  kubectl get ns vcluster-$NAME 2>&1 | grep -E "NotFound|Terminating" && echo OK
  ```

### Task 5.4: Update plan status and PR

**Files:** Modify: `docs/plans/vcluster-implementation-plan.md`

**Steps:**

- [ ] Change `Status: Phase 0 (not started)` → `Status: All phases complete (5/5)` at the top of this file
- [ ] Update `Last Updated` to today's date
- [ ] Mark every `- [ ]` checkbox above as `- [x]` where the work has been done
- [ ] Commit and push

  ```bash
  git add docs/plans/vcluster-implementation-plan.md
  git commit -m "docs(vcluster): mark implementation plan complete"
  git push origin main
  ```

- [ ] Open a PR summarizing all phases (use `gh pr create` per repo PR template)

---

## Appendix A — Helpful one-liners

```bash
# List all vclusters across the host cluster
kubectl get sts -A -l app=vcluster

# Show a vcluster's view of its pods (after `vcluster connect`)
KUBECONFIG=./kubeconfig.yaml kubectl get pods -A

# Inspect the syncer logs (host-side)
kubectl -n vcluster-<name> logs sts/vcluster -c syncer --tail=100

# Force ArgoCD to re-sync sandbox-1
kubectl -n argo-cd patch app vcluster-sandbox-1 --type merge -p '{"operation":{"sync":{}}}'
```

## Appendix B — Rollback procedure

If something goes badly wrong with the platform changes (Phase 1):

1. Revert the commit that added `enable-ssl-passthrough`:
   ```bash
   git revert <commit-sha>
   git push origin main
   ```
2. Wait for the K3s HelmController to reconcile (~30s) and the DaemonSet to roll out.
3. Confirm ssl-passthrough is gone:
   ```bash
   kubectl -n ingress-nginx get pod -l app.kubernetes.io/component=controller -o jsonpath='{.items[0].spec.containers[0].args}' | grep -c ssl-passthrough
   # Expected: 0
   ```

To remove `sandbox-1` only:

1. `git rm base-apps/vcluster-sandbox-1.yaml` and `git rm -r base-apps/vcluster-sandbox-1/`
2. Commit and push — ArgoCD prune will delete the namespace and resources.

---

## Post-execution notes (2026-05-12)

The implementation completed end-to-end, but reality diverged from the plan in several material ways. Captured here for future planners.

### Major pivot: ssl-passthrough → nginx-terminated TLS

The plan's central design (per the spec) was nginx ssl-passthrough so the vcluster pod could serve a Let's Encrypt cert directly. **This broke the cluster in production.** With `--enable-ssl-passthrough=true`, ingress-nginx restructures into two nginx instances; the inner nginx saw all non-passthrough traffic with `client: 127.0.0.1`, breaking `whitelist-source-range` on `argocd-server`, `atlantis`, `backstage`, and any other ingress relying on real-client-IP enforcement. The fix would have been adding `use-proxy-protocol: "true"` to the ConfigMap, but that has its own blast radius (would break any path that delivers traffic without PROXY protocol).

**Pivoted to nginx-terminated TLS** (PR #264): nginx terminates the LE cert at the edge and proxies HTTPS to the vcluster service. The vcluster pod's internal cert is not on the wire seen by kubectl. This is actually the cleaner design and what the spec should have specified from the start.

Phase 1 was reverted (PR #263) and `enable-ssl-passthrough` is **not** enabled on the cluster.

### Surfaced pre-existing issue: Route 53 IAM credentials

The cert-manager Route 53 credentials in Vault (`k8s-secrets/cert-manager/route53`) pointed at an access key that did not exist in the AWS account. cert-manager had been silently failing DNS-01 challenges for any newly requested cert for an unknown period (the oncall-crewai cert had been stuck for 70 days). Existing valid certs kept working because renewal only happens close to expiration.

**Remediation:** created a scoped IAM user `cert-manager-route53` with least-privilege Route 53 access (only the arigsela.com hosted zone for record changes; cluster-wide for zone lookups), minted a new access key, updated Vault, force-resynced ESO. All previously stuck CertificateRequests were deleted and reissued successfully.

This problem was not in the original risk list because the existing cert validity check (Task 0.2) only verified the *secret existed*, not that the keys *worked*. Future runbooks should test against AWS, e.g. `aws sts get-caller-identity` from a pod using the credentials.

### Operational discoveries (now in the runbook)

1. **vcluster CLI uses Helm `releaseName` as the identifier**, not the chart metadata name. The GitOps `sandbox-1` was deployed with `releaseName: vcluster`, so it's `vcluster connect vcluster --namespace vcluster-sandbox-1`. Cosmetic; can be fixed by changing `releaseName` in the Application values and reinstalling.
2. **Modern CLI uses `--print`**; the original plan referenced deprecated `--update-current` and `--kube-config` flags.
3. **kubeconfig CA must be stripped.** `vcluster connect --print` always embeds the vcluster internal CA. With nginx-terminated TLS, the wire cert is LE-signed — kubectl uses the OS CA bundle only if `certificate-authority-data` is absent from the kubeconfig.
4. **Token-based auth is required.** Default client-cert auth doesn't survive TLS termination at nginx — the cert never reaches the backend, so kubectl is `system:anonymous`. Pass `--service-account admin --cluster-role cluster-admin` to `vcluster connect` to get a bearer-token kubeconfig.
5. **Streaming annotations** are needed for `kubectl exec` / `port-forward` / `logs -f` through the nginx hop. The runbook's `ingress.tmpl.yaml` includes them.

### What was actually delivered

| Phase | Plan PR(s) | Status |
|---|---|---|
| Phase 0 | (read-only) | ✅ done in session |
| Phase 1 | #261 enable, #263 revert | ✅ reverted by design — see pivot above |
| Phase 2 | #262 initial, #264 TLS redesign | ✅ vcluster-sandbox-1 deployed and validated end-to-end |
| Phase 3 | (read-only) | ✅ Kyverno/ECR confirmed clean |
| Phase 4 | #265 | ✅ `docs/reference/vcluster/` runbook + templates |
| Phase 5 | (CLI exercise) | ✅ ad-hoc `vcluster create/delete` lifecycle validated |
| Side-quest | (AWS + Vault, no PR) | ✅ created `cert-manager-route53` IAM user, rotated Vault credentials |

### Follow-ups not done in this iteration

- `oncall-crewai/chores-tracker-agent-tls` was reissued successfully after the credential fix but no other validation done.
- The cert-manager Certificate in `base-apps/vcluster-sandbox-1/` is currently unused (LE cert is on the wire via nginx; vcluster pod serves its own internal cert that kubectl never sees in this design). Could be removed for cleanliness, but it's harmless and useful for future browser/curl access if we choose to mount it into the vcluster pod.
- vcluster `releaseName: sandbox-1` rename for cleaner CLI UX.
- Wildcard DNS for `*.vcluster.arigsela.com → 10.0.1.50` at the LAN resolver (replacing `/etc/hosts`).
