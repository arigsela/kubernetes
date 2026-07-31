---
type: "Kubernetes App Runbook"
title: "istio-ingress Runbook"
description: "Failure modes for the north-south Gateway: certless listeners, unreachable hosts, and the traps found replacing ingress-nginx"
app: istio-ingress
catalog_entity: istio-ingress
kind: runbook
namespace: istio-ingress
last_reviewed: 2026-07-31
status: current
tags: [ingress, gateway-api, istio]
sources:
  - base-apps/istio-ingress/gateway.yaml
  - base-apps/istio-ingress/gateway-options.yaml
  - base-apps/istio-ingress/authorizationpolicy.yaml
  - scripts/hop-verify.sh
---

# istio-ingress Runbook

## First: what to check, in order

```bash
kubectl -n istio-ingress get gateway main                 # Programmed=True?
kubectl -n istio-ingress get pods                          # 1/1 Running?
kubectl -n istio-ingress logs deploy/main-istio --tail=50  # Envoy's own view
scripts/hop-verify.sh gate                                 # the four gate checks
```

`hop-verify`'s `check_ingress` asserts the Gateway is Programmed, its data plane
has ready pods, **every** listener resolved its `certificateRef`, and the
AuthorizationPolicy exists. That last one matters: an ingress that serves but
enforces nothing is worse than one that is down.

## A host returns 404 through the Gateway

The request reached Envoy but matched no route. **403 means the opposite** — it
routed fine and the AuthorizationPolicy refused it. That distinction is the
fastest triage available here; do not confuse them.

```bash
kubectl -n istio-ingress get gateway main -o json | jq '.status.listeners[]
  | {name, attachedRoutes, conditions: [.conditions[] | {type, status}]}'
kubectl -n <app-ns> get httproute -o yaml    # Accepted? ResolvedRefs?
```

Usual causes: the `sectionName` in the HTTPRoute does not match a listener name;
the hostname on the route does not match the listener's; or the backend Service
name is wrong. **Backend names are not app names** — `coroot`'s Service is
`coroot-coroot`, not `coroot`. Read the Service, do not infer it.

## A listener serves the wrong certificate, or none

Almost always a missing or mis-scoped `ReferenceGrant`. Gateway API requires an
explicit grant for any cross-namespace `certificateRef`, and **without it the
listener comes up silently certless rather than erroring**.

```bash
kubectl -n istio-ingress get gateway main -o json \
  | jq '.status.listeners[] | select(.conditions[]
        | select(.type=="ResolvedRefs" and .status!="True")) | .name'
kubectl -n <app-ns> get referencegrant
```

The grant lives in the namespace that **owns the secret**, and its `from` must
name `Gateway` in `istio-ingress`.

## Everything is 403, including from an allow-listed address

Check what source address Envoy actually saw — do not assume:

```bash
kubectl -n istio-ingress logs deploy/main-istio --tail=20 | grep rbac_access_denied
```

The access log records the downstream address. If it shows a node or pod address
rather than the real client, `externalTrafficPolicy` has reverted to `Cluster`
and every client now looks like the node.

**Do not "fix" that by adding `10.0.0.0/8` to the allow-list.** That is the
failure this design exists to avoid: the node address is inside that range, so
the rule would then admit the entire internet while every host still returned
200. Fix `externalTrafficPolicy` instead.

## Changing gateway-options.yaml appears to do nothing

**istiod does not re-reconcile the generated Deployment when the
`parametersRef` ConfigMap changes.** The ConfigMap will hold the new values while
the Deployment keeps the old ones, indefinitely.

```bash
kubectl -n istio-ingress get cm gateway-options -o yaml      # new values
kubectl -n istio-ingress get deploy main-istio -o yaml       # old values
kubectl -n istio-ingress delete deploy main-istio            # istiod rebuilds it
```

Deleting the Deployment is the supported way to force it. istiod recreates it
within seconds, applying the current ConfigMap.

## The gateway pod is Pending on "didn't have free ports"

Something else holds the hostPorts klipper wants. Check what:

```bash
kubectl get pods -A -o json | jq -r '.items[]
  | select(.spec.nodeName=="k3s-control-01")
  | .metadata.name as $n | .spec.containers[].ports[]?
  | select(.hostPort) | "\($n) \(.hostPort)"'
```

Only `k3s-control-01` carries `node.kubernetes.io/workload: infrastructure`, so
the other two nodes will always be rejected on the selector — that is intended,
since public DNS resolves to that node.

## Do not give this Gateway hostNetwork

It cannot bind `:80`/`:443` if you do, and the failure is confusing: the Gateway
reports `Accepted=True` with **every listener green** while no Deployment exists
at all, and the real reason appears only in istiod's log.

```
Deployment.apps "main-istio" is invalid: spec.template.spec.securityContext
.sysctls[0].name: Invalid value: "net.ipv4.ip_unprivileged_port_start":
may not be specified when 'hostNetwork' is true
```

Istio permits low-port binding via that sysctl; Kubernetes forbids the sysctl
under hostNetwork; and Istio's image has no file capability to fall back on, so
`NET_BIND_SERVICE` is inert. See `docs.md`. This was tried, twice, and cost an
outage.

## Adding a host

Four things, together — never a listener without a rule:

1. listener in `gateway.yaml` (hostname + `certificateRefs`)
2. `ReferenceGrant` in the app namespace
3. `HTTPRoute` in the app namespace (`sectionName` = listener name)
4. rule in `authorizationpolicy.yaml`, **or** the host is refused

Then verify by observation, not by reading YAML:

```bash
curl -o /dev/null -w '%{http_code}\n' https://<host>/     # from an allow-listed address
# and from one that is NOT allow-listed — must be 403
```

## Gotchas that cost time

- **Argo app names are not directory names.** `base-apps/kagent/` is owned by the
  app `kagent-secrets`; `base-apps/chores-tracker-backend/` by
  `chores-tracker-backend`, not `chores-tracker`. Refreshing the wrong name does
  nothing and looks like the change failed.
- **The child Application's own spec is owned by `master-app`.** Changing
  `targetRevision` in git and refreshing the child re-syncs it from its *old*
  spec. Refresh `master-app`.
- **Strategic merge patches replace list fields.** Restate `drop: [ALL]` when
  adding a capability, or the container silently gets every capability.
- **`RequestRedirect` preserves the request port** unless `port` is set
  explicitly.
- **Istio defaults the generated Service to `LoadBalancer`**, which makes klipper
  spawn `svclb` DaemonSets squatting hostPorts on every node.
- **Resource names change between Istio versions.** The ztunnel DaemonSet was
  `ztunnel` at 1.24, `istio-ztunnel` at 1.25, and `ztunnel` again at 1.26. Read
  live names; do not hardcode.
