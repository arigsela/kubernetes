# Argo CD drift diagnosis (SPEC.md §T.42)

Eight applications sit permanently `OutOfSync` while reporting `Healthy`. §V.5 originally
required every app `Synced+Healthy` before each upgrade hop, which made every hop
unreachable. §V.47 revised that to *no **unexplained** drift* — so each app needs a
documented cause and a targeted `ignoreDifferences`, or it blocks.

This is that documentation, from evidence rather than theory. Diagnosed 2026-07-28.

**`ServerSideApply` is not the answer.** Five of these eight already have it enabled and drift
regardless (§R.19), and Argo's docs warn it *"has the potential to be destructive and might
lead to resources having to be recreated."*

> **Status 2026-07-28:** 8 drifting apps reduced to 2. Fixed: master-app,
> openshell-secrets, istio-base, istio-istiod, openshell, argo-rollouts.
> Remaining: kagent-secrets (needs an operator fix, not an ignore rule) and
> kyverno (cause identified, rule applied, controller still disagrees — see below).

## Summary

| App | Resource | Cause | Fix |
|---|---|---|---|
| istio-base | `ValidatingWebhookConfiguration/istiod-default-validator` | `caBundle` injected by istiod | `ignoreDifferences` |
| istio-istiod | `ValidatingWebhookConfiguration/istio-validator-istio-system` | same | `ignoreDifferences` |
| kagent-secrets | `ServiceAccount/kagent/homelab-agent` | operator-created child inherited Argo's tracking-id | exclude / fix kagent |
| master-app | `Application/openshell-secrets` | `recurse: false` normalised away | **delete the field** |
| master-app | `Application/kyverno` | Argo adds its own finalizers | `ignoreDifferences` |
| openshell-secrets | `ExternalSecret/openshell-jwt-keys` | ESO webhook defaults | `ignoreDifferences` |
| openshell | `StatefulSet/openshell` | Helm chart vs API defaulting | `ignoreDifferences` |
| argo-rollouts | 5 CRDs | co-managed with `k3s` | needs field-level diff |
| kyverno | 11 CRDs + 1 Job | co-managed; Job absent in cluster | needs field-level diff |

Only one is a genuine defect. The rest are controller behaviour that git cannot represent.

## The genuine bug — `recurse: false`

`base-apps/openshell-secrets.yaml` declares `spec.source.directory.recurse: false`. Argo
normalises an explicit `false` for a field that already defaults to false, so the live object
simply omits it. Git says `false`, live says nothing, and the diff never closes.

It is the only file in `base-apps/` carrying it, and the field is a no-op. **Delete it.**

## Controller-injected fields

**istio webhooks.** istiod writes a 1460-byte `caBundle` into both
`ValidatingWebhookConfiguration`s at runtime. Git ships them empty because the CA is
generated in-cluster. Nothing can reconcile this; it must be ignored.

```yaml
ignoreDifferences:
  - group: admissionregistration.k8s.io
    kind: ValidatingWebhookConfiguration
    jsonPointers: ["/webhooks/0/clientConfig/caBundle"]
```

**ExternalSecret defaults.** The ESO webhook stamps `conversionStrategy: Default`,
`decodingStrategy: None` and `metadataPolicy: None` onto every `remoteRef`, plus
`target.deletionPolicy: Retain`. Git omits all four.

Worth noting for §T.31: this is the ESO admission webhook actively rewriting stored objects,
the same mechanism behind the `v1beta1` → `v1` conversion drift in §R.7. Expect more of this
during the ESO migration, not less.

**Argo's own finalizers.** `Application/kyverno` drifts because Argo adds
`pre-delete-finalizer.argocd.argoproj.io` and `.../cleanup` to the live object while git
declares only `resources-finalizer.argocd.argoproj.io`. Argo is drifting against itself.
Either ignore `metadata.finalizers` or write the finalizers into git.

## The interesting one — kagent tracking-id inheritance

`ServiceAccount/kagent/homelab-agent` carries:

```
argocd.argoproj.io/tracking-id: kagent-secrets:kagent.dev/Agent:kagent/homelab-agent
ownerReferences: Agent/homelab-agent
```

The tracking-id names a **`kagent.dev/Agent`**, not a ServiceAccount. Argo applied the
`Agent` CR; the kagent operator then created this ServiceAccount as a child and **copied the
annotations across**, tracking-id included. Argo now believes it manages a resource it never
applied and that does not exist in git.

`ignoreDifferences` is the wrong tool — the resource shouldn't be tracked at all. Proper fixes,
in order of preference: stop kagent propagating `argocd.argoproj.io/*` annotations to
generated children; or add a resource exclusion. Argo normally ignores owner-referenced
children — the inherited annotation is what overrides that.

## Still open — the CRDs

`argo-rollouts` (5 CRDs) and `kyverno` (11 CRDs + 1 Job) need a field-level diff before a fix
is proposed. What is known:

- Both are co-managed by `argocd-controller` **and** `k3s`.
- Annotation size is not the cause — `rollouts.argoproj.io` is 61KB, well under the 262KB
  limit, so the usual large-CRD explanation does not apply here.
- kyverno already runs `ServerSideApply=true`, so its CRDs carry no last-applied annotation
  at all and still drift.
- The kyverno `Job/kyverno-migrate-resources` reports status `None` — present in git, absent
  from the cluster. Jobs are immutable and likely completed and were cleaned up; it may want
  a `Prune=false` or a hook annotation rather than an ignore rule.

Getting the exact field requires `argocd app diff`, and the CLI is not installed. That is the
next step.

## Bearing on the upgrade

None of these are failures — every affected app reports `Healthy` and is serving. But under
the original §V.5 they would each have blocked every hop, which is why the invariant was
wrong rather than the cluster.

With §V.47, the gate becomes: this document plus `ignoreDifferences` covering each cause. The
two CRD cases are the only ones still genuinely unexplained.


## Corrections to this document

Two of my earlier conclusions here were wrong and are worth recording, because the
mistakes were methodological rather than incidental.

**The istio rule ignored one field out of eight.** I diagnosed by observing that a
`caBundle` was present in the cluster and assumed it was the differing field.
Rendering the chart and diffing showed eight differences — `caBundle` and
`failurePolicy` written by istiod at runtime, plus six API-server defaults. The
first rule changed nothing.

**I reported kyverno's cause as `conversion`. It is `metadata.labels`.** My
comparison script normalised "absent" to `{}` before comparing, so a chart that
renders `labels: {}` against a cluster that has no labels key compared *equal* and
the real difference was invisible. The tool written to stop me guessing was itself
guessing.

`argocd app diff` answered it in one step. It needs no install:

```bash
kubectl config view --raw --minify > /tmp/kc      # then set contexts[].context.namespace
docker run --rm --network host -v /tmp/kcdir:/home/argocd/.kube:ro \
  -e KUBECONFIG=/home/argocd/.kube/config quay.io/argoproj/argocd:v3.4.5 \
  argocd app diff <app> --core
```

Reach for that before hand-rolling a diff.

## kyverno: cause known, resolution incomplete

kyverno 3.7.1 renders `metadata.labels: {}` on exactly 11 of its 22 CRDs. The API
server drops empty maps, so Argo compares `{}` against absent forever. Those 11 are
precisely the CRDs reported `OutOfSync`. argo-rollouts, whose chart gives its CRDs
real labels, needed no such rule and is `Synced`. This is an upstream chart bug.

The rule is applied and the **stable v3.4.5 CLI computes no diff at all**. The
controller — running **v3.5.0-rc2**, a release candidate — still reports the 11
`OutOfSync`.

It is not a blanket controller bug: argo-rollouts carries a CRD-scoped
`ignoreDifferences` on the same controller and is `Synced`. The untested variables
are that kyverno has `ServerSideApply=true` and that its rule targets
`.metadata.labels` rather than a `.spec` path. Next steps, cheapest first:

1. Swap `.metadata.labels` for a `jsonPointers: ["/metadata/labels"]` entry — a
   different code path in Argo.
2. Try the app without `ServerSideApply=true`.
3. Re-test after §T.5 moves Argo CD off the release candidate — worth doing before
   chasing this further, since a pre-release GitOps engine is a plausible cause and
   is a problem in its own right.

kyverno is `Healthy` throughout and admission is serving; this blocks §V.5's
literal reading, not the cluster.
