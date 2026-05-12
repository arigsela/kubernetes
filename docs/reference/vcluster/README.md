# vcluster — homelab runbook

Ephemeral test clusters running inside the host cluster. Each vcluster is a full
Kubernetes API (k3s control plane + sqlite, no PVC) exposed on its own subdomain
of `*.vcluster.arigsela.com`.

A long-lived reference instance (`sandbox-1`) lives in `base-apps/vcluster-sandbox-1/`
and validates the pattern end-to-end. All other vclusters are created and torn
down via the `vcluster` CLI — they do **not** appear in `base-apps/`.

## Prerequisites

1. **CLI installed:** `brew install loft-sh/tap/vcluster` (this runbook tested with `vcluster 0.34.0`)
2. **LAN DNS:** `*.vcluster.arigsela.com` must resolve to `10.0.1.50` (the infrastructure node where nginx-ingress runs). For quick testing, `/etc/hosts` on the workstation works:
   ```
   10.0.1.50  sandbox-1.vcluster.arigsela.com
   ```
   Long-term, add a wildcard A record `*.vcluster.arigsela.com → 10.0.1.50` to your LAN resolver (Pi-hole, AdGuard, router).
3. **vault `cert-manager/route53`** must hold valid AWS credentials for the `cert-manager-route53` IAM user — DNS-01 challenges fail without it.

## Two access modes

### Mode A — Port-forward (fastest, ephemeral)

For one-off testing where you don't need a stable URL. The vcluster CLI sets up a local port-forward and exits when you're done.

```bash
# Create
vcluster create my-test --namespace vcluster-my-test \
  -f docs/reference/vcluster/values-template.yaml

# Connect (writes kubeconfig, opens port-forward in the foreground)
vcluster connect my-test --namespace vcluster-my-test
# ^^ keeps running; in another shell or `vcluster disconnect` to stop

# Tear down
vcluster delete my-test --namespace vcluster-my-test
```

### Mode B — Stable URL via nginx Ingress (recommended)

For anything lasting longer than a few minutes. Survives shell sessions and works from any machine on the LAN.

```bash
export NAME=my-test

# 1) Create the vcluster with the per-instance hostname SAN
vcluster create $NAME --namespace vcluster-$NAME \
  -f docs/reference/vcluster/values-template.yaml \
  --set controlPlane.proxy.extraSANs[0]=$NAME.vcluster.arigsela.com

# 2) Apply Certificate + Ingress (nginx terminates LE TLS, proxies HTTPS to vcluster)
envsubst < docs/reference/vcluster/certificate.tmpl.yaml | kubectl apply -f -
envsubst < docs/reference/vcluster/ingress.tmpl.yaml    | kubectl apply -f -

# 3) Wait for the cert to issue (~60-90s via DNS-01)
kubectl -n vcluster-$NAME wait certificate/vcluster-tls --for=condition=Ready --timeout=180s

# 4) Write a kubeconfig that points at the stable URL with a token-auth service account.
#    Token auth is REQUIRED because nginx terminates TLS — kubectl's client cert never
#    reaches the vcluster pod. The CA stripping is REQUIRED because vcluster embeds
#    its internal CA, but the LE-signed cert is the one on the wire.
vcluster connect vcluster --namespace vcluster-$NAME \
  --server https://$NAME.vcluster.arigsela.com \
  --service-account admin --cluster-role cluster-admin \
  --print 2>/dev/null \
  | python3 -c "import sys,yaml; d=yaml.safe_load(sys.stdin); [c['cluster'].pop('certificate-authority-data',None) for c in d['clusters']]; yaml.safe_dump(d, sys.stdout)" \
  > /tmp/vc-$NAME.kubeconfig

# 5) Use it
export KUBECONFIG=/tmp/vc-$NAME.kubeconfig
kubectl get nodes
kubectl create deployment hello --image=nginx
```

> **Note on the CLI name:** the vcluster CLI uses the **Helm release name** as the
> identifier — when you `vcluster create my-test`, the release name is `my-test`,
> so `vcluster connect my-test ...` works. The GitOps `sandbox-1` instance was
> deployed with `releaseName: vcluster` (the chart default), so it's `vcluster connect vcluster --namespace vcluster-sandbox-1` — name mismatch is harmless but cosmetically awkward.

### Connecting to `sandbox-1` (GitOps reference instance)

```bash
vcluster connect vcluster --namespace vcluster-sandbox-1 \
  --server https://sandbox-1.vcluster.arigsela.com \
  --service-account admin --cluster-role cluster-admin \
  --print 2>/dev/null \
  | python3 -c "import sys,yaml; d=yaml.safe_load(sys.stdin); [c['cluster'].pop('certificate-authority-data',None) for c in d['clusters']]; yaml.safe_dump(d, sys.stdout)" \
  > /tmp/vc-sandbox-1.kubeconfig

KUBECONFIG=/tmp/vc-sandbox-1.kubeconfig kubectl get nodes
```

## Sleep / wake (CLI-managed vclusters only)

For ad-hoc vclusters, you can pause to free resources without destroying state.

```bash
vcluster pause my-test --namespace vcluster-my-test
vcluster resume my-test --namespace vcluster-my-test
```

**Does NOT work for `sandbox-1`** — ArgoCD `selfHeal: true` will scale the StatefulSet back up immediately. The GitOps reference is always-on by design.

## Teardown

```bash
export NAME=my-test
vcluster delete $NAME --namespace vcluster-$NAME
kubectl delete ns vcluster-$NAME --wait=false  # only if you applied Mode B templates
rm -f /tmp/vc-$NAME.kubeconfig
```

## Troubleshooting

### `x509: certificate signed by unknown authority`
The kubeconfig still has vcluster's internal CA embedded but nginx is serving the LE cert. Re-generate the kubeconfig with the Python one-liner that strips `certificate-authority-data` (see Mode B step 4).

### `Forbidden: User "system:anonymous" cannot ...`
The kubeconfig is using client-cert auth, but nginx terminates TLS so the cert doesn't reach vcluster. Pass `--service-account admin --cluster-role cluster-admin` to `vcluster connect` to get a bearer-token kubeconfig instead.

### Certificate stuck in `Ready: False` for >2 minutes
Almost always a Route 53 DNS-01 problem.
```bash
kubectl -n vcluster-$NAME get challenge -o wide
# Look at the `reason` field for the AWS error
```
Common: `InvalidClientTokenId` → the access key in `vault://k8s-secrets/cert-manager/route53` is invalid or deleted from AWS. Mint a fresh key for the `cert-manager-route53` IAM user and update Vault.

### Ingress responds but kubectl times out
`kubectl exec` / `port-forward` / `logs -f` over the ingress requires the streaming-friendly annotations. They're present in `ingress.tmpl.yaml`:
- `proxy-http-version: "1.1"`
- `proxy-read-timeout: "3600"`
- `proxy-send-timeout: "3600"`

If you wrote your own Ingress, make sure these are set.

### vcluster pod won't start
Check Kyverno PolicyReports in the vcluster namespace. The Helm chart's Deployment fails the `require-labels` audit rule (no `app.kubernetes.io/name` label), but this is **Audit mode** and doesn't block. Anything other than that is a real issue worth investigating.

## Known limitations (v1)

- **No Vault/ESO inside vclusters.** Workloads needing secrets use test data or skip secret-dependent flows. Future work: wire ESO inside each vcluster pointing at the host Vault.
- **Istio ambient not enrolled.** vcluster namespaces deliberately don't have `istio.io/dataplane-mode: ambient`. Avoids ztunnel intercepting API server traffic during early debugging. Future: revisit if mesh observability inside vclusters is wanted.
- **Sleep/resume doesn't work for GitOps-managed vclusters.** ArgoCD's `selfHeal` undoes the pause.
- **Helm release name = CLI vcluster name.** Cosmetic — the GitOps `sandbox-1` is named `vcluster` from the CLI's perspective because the chart `releaseName` is `vcluster`. To get a cleaner name, deploy with `releaseName: sandbox-1` (will require recreating the Helm release).

## Files in this directory

| File | Purpose |
|---|---|
| `README.md` | This runbook |
| `values-template.yaml` | Default Helm values for `vcluster create -f` |
| `certificate.tmpl.yaml` | envsubst template — per-vcluster cert-manager Certificate |
| `ingress.tmpl.yaml` | envsubst template — per-vcluster nginx Ingress |

## Related

- Design spec: [`docs/superpowers/specs/2026-05-12-vcluster-design.md`](../../superpowers/specs/2026-05-12-vcluster-design.md)
- Implementation plan: [`docs/plans/vcluster-implementation-plan.md`](../../plans/vcluster-implementation-plan.md)
- GitOps reference instance: [`base-apps/vcluster-sandbox-1.yaml`](../../../base-apps/vcluster-sandbox-1.yaml) + [`base-apps/vcluster-sandbox-1/`](../../../base-apps/vcluster-sandbox-1/)
