# kubectl login through Dex (structured authentication config)

Plan: `docs/plans/k8s-136-features-implementation-plan.md`, Phase 5. The API server of the
single control plane (`k3s-control-01`) authenticates `kubectl` users with ID tokens from Dex
(`https://dex.arigsela.com`, client `kubernetes`), through a **structured authentication
configuration** file (`AuthenticationConfiguration`, GA in Kubernetes 1.34) instead of the
`--oidc-*` flags. The x509 admin kubeconfig stays as **break-glass**: it does not depend on
Dex or on this file.

## Files and how they get onto the node
| Source (git) | Node path | Change takes effect |
|---|---|---|
| `node-config/k3s-control-01/authn-config.yaml` | `/etc/rancher/k3s/authn-config.yaml` | `jwt` edits: **hot reload** (~1 min, no restart). `anonymous` edits: restart |
| `node-config/k3s-control-01/config.yaml.d/10-authn.yaml` | `/etc/rancher/k3s/config.yaml.d/10-authn.yaml` | k3s **restart** |

Install only with `scripts/install-authn-config.sh` (`--dry-run` first). It refuses a file
without `anonymous: {enabled: false}` or with an `--oidc-*` flag, then validates the files in
a throwaway `rancher/k3s` of the cluster's version (API must start, load the config, and deny
anonymous with 401). It then backs up the node's files to
`/etc/rancher/k3s/authn-backup/<timestamp>/`, installs by temp file and rename, and either
restarts k3s and waits for `/readyz` (rolling back automatically if it does not return), or
waits for the hot-reload counter. A broken file never reaches the node.

## Gotchas
- **The `anonymous` block is mandatory.** Given an authentication config file, k3s stops
  passing its default `--anonymous-auth=false` ("Not setting kube-apiserver 'anonymous-auth'
  flag due to user-provided 'authentication-config' file"), and the Kubernetes default is
  anonymous **enabled**. Without the block, `/version` and `/healthz` answer 200 to anyone
  (measured in a throwaway k3s v1.36.4; with the block: 401, as before).
- `--oidc-*` flags and `--authentication-config` are mutually exclusive: the API server will
  not start with both.
- The API server fetches Dex discovery and JWKS through the public hostname, i.e. through the
  **WAN IP allow-list** on the Dex route (`base-apps/istio-ingress/authorizationpolicy.yaml`),
  the same dependency Argo CD SSO has. If the WAN IP rotates and the allow-list is not
  updated, OIDC logins fail; the break-glass kubeconfig still works.
- A JWT edit that the API server rejects on hot reload is **ignored** (it keeps the previous
  config) and counted in
  `apiserver_authentication_config_controller_automatic_reloads_total{status="failure"}`.
- After every API-server restart, writes to read/write-class Agents are denied for a few
  seconds (the delegation policy's param informer; `admission-policies` runbook).

## Stages
- **Stage A (Task 5.3):** `anonymous: {enabled: false}` + `jwt: []` and the drop-in; one k3s
  restart (control-plane blip ~1-2 min; workloads keep running). Behaviour is unchanged: only
  the source of authentication config moves into the hot-reloadable file.
- **Stage B (Task 5.4):** add the Dex JWT authenticator by hot reload: issuer
  `https://dex.arigsela.com`, audience `kubernetes`, `claims.sub` pinned to the owner's GitHub
  identity (confirmed from a real token first), usernames `oidc:<login>`, no `system:` names.
- **RBAC (Task 5.5):** `oidc:arigsela` → `cluster-admin`; laptop context `homelab-oidc` via
  kubelogin (`kubectl oidc-login`).

## Checks
```bash
curl -sk -o /dev/null -w '%{http_code}\n' https://10.0.1.50:6443/api          # 401: anonymous denied
kubectl get --raw /metrics | grep apiserver_authentication_config_controller  # config loaded / reloads
kubectl get --raw /readyz
```

## Rollback
- **Remove the Dex issuer** (Stage B → A): set `jwt: []` in git and re-run the installer; hot
  reload, no restart. OIDC logins then fail; the admin kubeconfig works.
- **Remove the file altogether:** delete `/etc/rancher/k3s/config.yaml.d/10-authn.yaml` on the
  node and restart k3s (`sudo systemctl restart k3s`); k3s then passes
  `--anonymous-auth=false` again. The installer's backups are in
  `/etc/rancher/k3s/authn-backup/`.
- **The installer's own rollback:** if the API does not return within 240 s of a restart, it
  restores the backed-up files (or removes new ones) and restarts again.
