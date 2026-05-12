# Getting started with vcluster `sandbox-1`

This is the hands-on tutorial: get a working kubeconfig for the always-on `sandbox-1` vcluster, deploy a hello-world app inside it, and reach it from your browser via a host-side ingress.

If you just want the reference / advanced flows, see [`README.md`](./README.md) instead.

## What you'll have at the end

- A kubeconfig at `~/.kube/vcluster-sandbox-1.config` pointed at `https://sandbox-1.vcluster.arigsela.com`
- A shell helper so you can `vctl get pods` to talk to the vcluster (instead of `KUBECONFIG=… kubectl ...`)
- An nginx hello-world Deployment + Service running inside the vcluster
- A working URL `http://hello.vcluster.arigsela.com` (LAN-only) returning the page

Expected wall-clock: **10 minutes**.

---

## 1. One-time prerequisites

### Install the vcluster CLI

```bash
brew install loft-sh/tap/vcluster
vcluster version
# Expected: vcluster version 0.34.0 (or newer)
```

### Add LAN DNS entries

The cluster's nginx-ingress runs on the infrastructure node at `10.0.1.50`. You need this host to resolve to that IP from your workstation. The simplest way:

```bash
sudo sh -c 'cat >> /etc/hosts <<EOF
10.0.1.50  sandbox-1.vcluster.arigsela.com
10.0.1.50  hello.vcluster.arigsela.com
EOF'
```

Verify:

```bash
getent hosts sandbox-1.vcluster.arigsela.com  # Linux
# or
grep vcluster.arigsela.com /etc/hosts          # macOS
```

> **Why `/etc/hosts` and not real DNS?** External `*.arigsela.com` traffic goes through Cloudflare Tunnel which terminates TLS. We need direct LAN access to the cluster's nginx-ingress for these test workloads. Long-term, add a wildcard A record at your LAN DNS resolver (Pi-hole, AdGuard, router) for `*.vcluster.arigsela.com → 10.0.1.50`.

---

## 2. Generate a kubeconfig for sandbox-1

The vcluster CLI writes a kubeconfig with a few quirks we need to handle: it embeds the vcluster's *internal* CA (but nginx serves a Let's Encrypt cert, so kubectl rejects the chain) and it defaults to client-cert auth (which doesn't survive TLS termination at nginx). The recipe below uses a service-account bearer token and strips the embedded CA so kubectl falls back to the OS CA bundle.

```bash
vcluster connect vcluster --namespace vcluster-sandbox-1 \
  --server https://sandbox-1.vcluster.arigsela.com \
  --service-account admin --cluster-role cluster-admin \
  --print 2>/dev/null \
  | python3 -c "import sys,yaml; d=yaml.safe_load(sys.stdin); [c['cluster'].pop('certificate-authority-data',None) for c in d['clusters']]; yaml.safe_dump(d, sys.stdout)" \
  > ~/.kube/vcluster-sandbox-1.config
```

> **Why `vcluster` and not `sandbox-1` as the name?** The vcluster CLI uses the Helm release name as the instance identifier, and our `Application` deploys the chart with `releaseName: vcluster`. The directory and namespace are named `sandbox-1`; the release inside is named `vcluster`. Cosmetic mismatch, harmless.

Quick smoke test — talk to the vcluster:

```bash
KUBECONFIG=~/.kube/vcluster-sandbox-1.config kubectl cluster-info
# Expected: Kubernetes control plane is running at https://sandbox-1.vcluster.arigsela.com

KUBECONFIG=~/.kube/vcluster-sandbox-1.config kubectl get nodes
# Expected: one synthetic node (the host's worker, visible inside the vcluster)
```

---

## 3. Make it ergonomic: the `vctl` shell helper

Typing `KUBECONFIG=~/.kube/vcluster-sandbox-1.config kubectl …` every time is tedious. Add this to your `~/.zshrc` (or `~/.bashrc`):

```bash
# vcluster sandbox-1 kubectl wrapper
alias vctl='KUBECONFIG=$HOME/.kube/vcluster-sandbox-1.config kubectl'
```

Reload your shell (`exec zsh`) and verify:

```bash
vctl get ns
# Expected: default, kube-node-lease, kube-public, kube-system
```

From here on, **`vctl ...` talks to the vcluster** and plain `kubectl ...` talks to the host. The rest of this guide uses `vctl`.

If you ever forget which context you're in, `vctl config current-context` returns the vcluster's context name, and `kubectl config current-context` returns yours.

---

## 4. Deploy hello-world inside the vcluster

Everything in this section runs inside `sandbox-1`. The host has no idea these resources exist (except for the pods, which get synced down so they can actually run).

### 4a. Deployment

```bash
vctl create deployment hello --image=nginx --port=80
vctl rollout status deployment/hello --timeout=60s
```

What just happened:

1. `vctl` created a Deployment via the vcluster API
2. vcluster's controller-manager created a ReplicaSet, then a Pod, all stored in the vcluster's sqlite
3. The **syncer** noticed the Pod and copied a rewritten version into the host `vcluster-sandbox-1` namespace
4. Host kube-scheduler placed the real container on a real node

Peek at both views:

```bash
vctl get pods                                # vcluster view
kubectl -n vcluster-sandbox-1 get pods       # host view (real pod, renamed)
```

You'll see `hello-...` inside the vcluster and `hello-..-x-default-x-vcluster` on the host — same pod, two API representations.

### 4b. Service

```bash
vctl expose deployment hello --port=80 --target-port=80 --name=hello
vctl get svc hello
# Expected: ClusterIP assigned, port 80/TCP
```

Same story — the Service is synced down to the host (real ClusterIP, real kube-proxy rules), but the API view lives in the vcluster.

### 4c. Smoke test before adding an ingress

Easiest sanity check: `vctl port-forward` directly to the Service.

```bash
vctl port-forward svc/hello 8080:80 &
PORT_FWD_PID=$!
sleep 2
curl -sS http://localhost:8080/ | head -3
# Expected: <!DOCTYPE html><html><head>… (nginx welcome page)
kill $PORT_FWD_PID
```

If this works, the Deployment and Service are healthy. If it doesn't, the ingress in the next step won't help — debug here first.

---

## 5. Expose via an ingress

Here's the subtle part: in our deployment, **vcluster ingresses are not synced to the host** (we set `sync.toHost.ingresses.enabled: false`). That means a `Kind: Ingress` created inside the vcluster has no effect — nginx-ingress lives on the host and only watches host objects.

So for `http://hello.vcluster.arigsela.com` to work, we create the Ingress on the **host** side, pointing at the synced Service (which IS on the host, with the rewritten name).

First, find what the synced Service is called on the host:

```bash
kubectl -n vcluster-sandbox-1 get svc | grep hello
# Example output:
# hello-x-default-x-vcluster   ClusterIP   10.43.x.x   <none>   80/TCP   2m
```

Note that name — it's `<service>-x-<vcluster-namespace>-x-vcluster` and that's the backend the Ingress needs to reference.

### 5a. Create the host-side ingress

```bash
cat <<EOF | kubectl apply -f -
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: hello
  namespace: vcluster-sandbox-1
spec:
  ingressClassName: nginx
  rules:
    - host: hello.vcluster.arigsela.com
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: hello-x-default-x-vcluster   # the synced service name (verify with kubectl get svc above)
                port:
                  number: 80
EOF
```

### 5b. Verify

```bash
curl -sS http://hello.vcluster.arigsela.com/ | head -3
# Expected: <!DOCTYPE html><html><head>...
```

Open `http://hello.vcluster.arigsela.com` in a browser — you'll see the nginx welcome page.

> **HTTP vs HTTPS:** This ingress is plain HTTP for simplicity. To add TLS, drop a cert-manager `Certificate` for `hello.vcluster.arigsela.com` in the same namespace and add a `tls:` block to the Ingress — same pattern as `base-apps/vcluster-sandbox-1/`.

---

## 6. Clean up

```bash
# Remove the host-side ingress
kubectl -n vcluster-sandbox-1 delete ingress hello

# Remove the vcluster-side resources
vctl delete deployment hello
vctl delete service hello
```

The host-side synced pod disappears within a few seconds when the Deployment is deleted inside the vcluster.

To completely remove the kubeconfig:

```bash
rm ~/.kube/vcluster-sandbox-1.config
```

The `vctl` alias stays in your shell config — leave it for next time.

---

## What you can do from here

- **Install operators.** vcluster has its own CRDs and RBAC — `vctl apply -f <operator-manifest.yaml>` installs the operator inside the vcluster only. Nothing pollutes the host.
- **Break things deliberately.** This is what `sandbox-1` is for. Worst case, `kubectl -n vcluster-sandbox-1 rollout restart sts/vcluster` from the host rebuilds the vcluster control plane (sqlite is ephemeral, so all your in-vcluster state is gone — that's the point of ephemeral).
- **Create your own ad-hoc vclusters** for short-lived tests via `vcluster create`. See [`README.md`](./README.md) for the CLI workflow.

## Quick reference

| Command | What it does |
|---|---|
| `vctl get pods -A` | All pods inside the vcluster |
| `kubectl -n vcluster-sandbox-1 get pods` | The host's view of the vcluster's pods (real pods on real nodes) |
| `vctl logs deployment/hello` | Stream logs from your in-vcluster deployment |
| `vctl exec -it deploy/hello -- sh` | Shell into a pod (uses HTTP/1.1 upgrade through the nginx hop) |
| `vctl port-forward svc/hello 8080:80` | Local tunnel to a vcluster service |

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `x509: certificate signed by unknown authority` on `kubectl` | kubeconfig still has vcluster's internal CA | Re-run the section 2 command with the Python `pop('certificate-authority-data')` step |
| `User "system:anonymous" cannot ...` | kubeconfig is using client-cert auth | Re-run section 2 with `--service-account admin --cluster-role cluster-admin` |
| `vctl: command not found` | Alias not loaded | `exec zsh` or `source ~/.zshrc` |
| `curl: (6) Could not resolve host` | Missing `/etc/hosts` entry | See section 1 — add the lines for both `sandbox-1` and `hello` |
| Ingress 404 | Wrong backend service name | Run `kubectl -n vcluster-sandbox-1 get svc` to find the actual synced name |
| Ingress 503 | Synced pod is not Ready | `kubectl -n vcluster-sandbox-1 get pods` — debug from the host side |
