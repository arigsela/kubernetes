# CNPG managed-role reconciliation is inert on postgresql-cluster

**Status:** open
**Found:** 2026-08-03, deploying donetick (PR #525)
**Affects:** every role added to `spec.managed.roles` on `postgresql-cluster`
**Operator:** CloudNativePG 1.29.1 (chart `cloudnative-pg` 0.28.3)

## Symptom

A role added to `postgresql-cluster`'s `spec.managed.roles` is never created in
PostgreSQL. It appears in **no** `status.managedRolesStatus` bucket at all —
not `reconciled`, not `pending-reconciliation`, and critically not
`cannotReconcile`, which is where the operator records roles it has evaluated and
rejected. Nothing is logged by either the operator or the instance manager.

Downstream, anything depending on the role stalls with an accurate but misleading
error. For donetick that was a `Database` resource retrying every 30s:

```
while creating database "donetick": ERROR: role "donetick" does not exist (SQLSTATE 42704)
```

which reads like a `Database` problem and is not one.

## What is NOT the cause

Each of these was checked and ruled out, so nobody has to check them again:

| Hypothesis | Finding |
|---|---|
| Role missing from the applied spec | Present in the live object and in Argo's `last-applied-configuration` |
| Wrong Secret type | `kubernetes.io/basic-auth`, as CNPG requires |
| Wrong Secret keys | `username` (= role name) and `password`, both correct |
| Operator lacks RBAC on the Secret | The generated `Role/postgresql-cluster` lists `donetick-db-credentials` by name — the operator raised an `UpdatingRole` event to add it |
| Missing `cnpg.io/reload` label | Added; no effect. Note `postgresql-credentials`, whose `n8n` role DID reconcile, does not carry it either |
| Instance manager holding a stale spec | Instance restarted; role synchronizer re-initialized and still did nothing |
| Operator controller wedged | Operator restarted; no effect |
| No Cluster update event to drive the loop | Forced one by annotating the Cluster; no effect |
| Cluster unhealthy | All conditions `True`; `Ready`, archiving and backups working |

## Corroborating evidence

The one role that ever reconciled, `n8n`, carries a frozen status:

```
"passwordStatus":{"n8n":{"resourceVersion":"2789594","transactionID":2502}}
```

That `resourceVersion` is orders of magnitude below current. The synchronizer
logs `setting up RoleSynchronizer loop` at instance start and then never speaks
again, on either the old pod (up 6 days) or a freshly started one.

`chores_user` sits in `not-managed`, which is correct and expected — it was
created by hand and is not in the spec.

## Workaround in effect

The `donetick` role was created manually on 2026-08-03 from the password already
in `donetick-db-credentials`, so it matches Vault exactly:

```bash
P=$(kubectl -n postgresql get secret donetick-db-credentials -o jsonpath='{.data.password}' | base64 -d)
kubectl -n postgresql exec -i postgresql-cluster-2 -c postgres -- \
  psql -U postgres -c "ALTER ROLE donetick WITH PASSWORD '$P'"   # or CREATE ROLE ... LOGIN
```

The `spec.managed.roles` entry is deliberately **kept** in Git. If the reconciler
is ever fixed it will adopt the existing role rather than conflict with it, and
the declaration stays the source of truth in the meantime.

**The cost, which is the part that bites:** a Vault rotation no longer reaches
PostgreSQL on its own. `base-apps/donetick/runbook.md` documents the two-step
manual rotation this forces. Anyone adding another role to this cluster will hit
the same wall.

## Where to pick it up

- Compare against a scratch CNPG cluster on the same operator — if managed roles
  work there, the fault is specific to `postgresql-cluster`, which was adopted
  into GitOps on 2026-07-15 after running unmanaged since 2025-12-03 and may
  carry state the operator does not expect.
- Read the 1.29.1 role synchronizer source for the conditions under which it
  returns without recording status; a role landing in no bucket is the specific
  behaviour to explain.
- Check upstream issues for managed roles silently skipped in 1.28/1.29.
- A version bump is plausible but unverified — do not assume it fixes this.
