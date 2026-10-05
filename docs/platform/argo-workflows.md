# Argo Workflows

**Status:** living. Reviewed 2026-09-30 against `base-apps/argo-workflows.yaml`, `base-apps/argo-workflows/`, `base-apps/argo-workflow-tasks/`, `base-apps/argo-workflows-aws-infrastructure/` and `base-apps/istio-ingress/authorizationpolicy.yaml`. Design of the image scan: `docs/superpowers/specs/2026-08-17-image-vulnerability-scanning-design.md`.

## What it is
Argo Workflows runs DAG/step workflows as pods in namespace `argo-workflows`. Its one production job is the **weekly container-image vulnerability scan**. Four Argo CD Applications make it up:

| Application | Source | What |
|---|---|---|
| `argo-workflows` | `base-apps/argo-workflows.yaml` (hand-written) | Helm chart `argo-workflows` **2.0.1** (app **v4.1.1**) from `https://argoproj.github.io/argo-helm`: controller, server, CRDs |
| `argo-workflows-config` | `base-apps/argo-workflows/` (managed-apps ApplicationSet) | `Certificate` `argo-workflows-tls`, `HTTPRoute`, `ReferenceGrant` |
| `argo-workflow-tasks` | `base-apps/argo-workflow-tasks/` (managed-apps) | WorkflowTemplates and CronWorkflow, ConfigMap `cve-report`, ServiceAccount and RBAC |
| `argo-workflows-aws-infrastructure` | `base-apps/argo-workflows-aws-infrastructure/` (managed-apps) | Crossplane: bucket `asela-argo-workflows-artifacts` (us-east-1), IAM user `argo-workflows-s3-user`, policy (List/Get/Put/Delete on that bucket), and an `AccessKey` whose connection secret is `argo-workflows-s3-creds` |

## Architecture & data flow
- **Controller:** watches only `argo-workflows` (`controller.workflowNamespaces`). It and the **server** are pinned to `k3s-control-01` (`node.kubernetes.io/workload: infrastructure` plus the control-plane toleration). The Application uses `ServerSideApply=true`.
- **CRDs:** with `crds.full: true` (the chart default, kept for OpenAPI validation), the chart installs CRDs from a `crdinstaller` image in a Job annotated `helm.sh/hook: pre-install,pre-upgrade`. Argo CD runs it as **PreSync on every sync**. Its resources are bounded under `crds.upgradeJob`.
- **Artifacts and logs:**
  - `artifactRepository.s3` points at `asela-argo-workflows-artifacts`, with key format `{{workflow.namespace}}/{{workflow.name}}/{{pod.name}}` and `archiveLogs: true`, so pod logs outlive their pods.
  - Credentials are `argo-workflows-s3-creds`: key `username` holds the **access key ID**, and `attribute.secret` holds the secret key.
  - Crossplane writes that Secret (see `docs/platform/crossplane.md`).
- **UI and API:** `https://argo-workflows.arigsela.com`:
  - Gateway `istio-ingress/main`, listener `https-argo-workflows`;
  - HTTPRoute → Service `argo-workflows-server:2746`;
  - certificate from `letsencrypt-route53`, served cross-namespace via the ReferenceGrant.
- **Access control:** the server runs `authModes: [server]`, meaning **no login**. Every UI/API request acts with the server's own ServiceAccount. The only control is the IP allow-list rule for `argo-workflows.arigsela.com` in `base-apps/istio-ingress/authorizationpolicy.yaml` (four `/32`s: the home WAN address plus three remote addresses). The WAF doesn't cover this host.
- **Workflow identity:** every template runs as ServiceAccount `argo-workflow` (`serviceaccount.yaml`). The chart binds it, for `workflowtaskresults`, but doesn't create it. ClusterRole `argo-workflow-pod-reader` grants cluster-wide `get/list/watch` on **pods only**.

### The weekly image scan (`image-scan.yaml`)
**Schedule:** CronWorkflow `image-scan`, `0 4 * * 0` (Sunday 04:00 UTC), `concurrencyPolicy: Forbid`, `startingDeadlineSeconds: 3600`, 4 successful and 4 failed runs kept.

**WorkflowTemplate settings:** `activeDeadlineSeconds: 5400`, `parallelism: 8`, a TTL of 90 days, `podGC: OnWorkflowCompletion`.

**The DAG:**
1. **`trivy-server` and `discover` run in parallel.**
   - `trivy-server` is a daemon (`aquasec/trivy:0.74.0`, pinned by digest in both Trivy steps since the March 2026 Trivy supply-chain incident; DB in a 4Gi emptyDir, readiness-gated) that holds the vulnerability DB for the whole run.
   - `discover` (`python:3.12-slim`) lists every pod's containers **and** initContainers through the API and prints a JSON array of distinct images. That was about 75–79 at the design's first runs.
2. **`scan`** fans out one pod per image: `trivy image --server ... --scanners vuln --timeout 30m`. It retries twice on error. Registry creds come from the namespace's `ecr-registry` dockerconfigjson, which `base-apps/ecr-auth/` keeps fresh. The report is JSON, and it is optional, so a failed scan leaves no file.
3. **`report`** (aggregate) runs once the scans finish, **even if some failed** (`depends: scan.Succeeded || scan.Failed || scan.Errored`). It gets `discover`'s image list, and counts any image with no report as unscanned. Before 2026-10, one failed scan made Argo skip this step entirely. It pip-installs `boto3` (a failure is tolerated) and runs `render.py` from ConfigMap `cve-report` (`configmap-cve-report.yaml`, the only copy; unit tests in `tests/cve_report/`, run in CI). It:
   - dedupes findings, and flags **actionable** findings: CRITICAL/HIGH, with a published fix, in images starting `852893458518.dkr.ecr.` (the ones we build);
   - downloads CISA's KEV catalog and FIRST's EPSS scores (workflow parameters `kev-url`, `epss-url`) and marks every finding with `kev` and `epss`. This is best effort: an unreachable feed leaves them `null` and changes nothing else. The policy that uses them is `docs/platform/vulnerability-management.md`;
   - writes the Argo artifact `full-report.json`;
   - publishes `cve-reports/<YYYY-MM-DD>.json/.html` **then** `cve-reports/latest.json/.html` to the bucket, so `latest` never points at a partial report;
   - computes a delta against the previous dated report;
   - **only if there are actionable findings, unscanned images, or actively exploited (KEV) fixable findings in any image**, POSTs a summary to `http://n8n.n8n.svc.cluster.local:5678/webhook/image-scan-report`. The n8n workflow *Image Vulnerability Scan Report* (`base-apps/n8n/workflows-configmap.yaml`) posts it to Slack `#oncall-alerts`.
4. **Exit status:** the run is red **only when an image couldn't be scanned**. Findings alone don't make it red (changed 2026-08-18).

**Viewing the report:** the bucket is private. Open it with `aws s3 presign s3://asela-argo-workflows-artifacts/cve-reports/latest.html --expires-in 3600`. The bucket has no lifecycle, so dated reports accumulate as history.

### Other templates
- **`cluster-health-check`** (WorkflowTemplate, not scheduled). **Broken:** it uses `bitnami/kubectl:1.30`, which no longer pulls (Bitnami withdrew its public catalog). It also wants node read, and the SA only has pods.
- **`hello-world-dag`, `artifact-example`:** demos (`alpine:3.19`). `artifact-example` is a quick check that artifact upload works.

## Where config lives
- **Chart version and values** (controller, server, artifact repository, auth mode): `base-apps/argo-workflows.yaml`.
- **Route, certificate, ReferenceGrant:** `base-apps/argo-workflows/`. **Gateway listener:** `base-apps/istio-ingress/gateway.yaml`. **Allow-list:** `base-apps/istio-ingress/authorizationpolicy.yaml`.
- **Templates, schedule, report script, RBAC:** `base-apps/argo-workflow-tasks/`.
- **Bucket, IAM, access key:** `base-apps/argo-workflows-aws-infrastructure/`.
- **Slack delivery:** `base-apps/n8n/workflows-configmap.yaml` (`image-scan-report.json`).

## Gotchas & tribal knowledge
- **No login.** Whoever reaches the server gets the server ServiceAccount's permissions, and can submit workflows that run as `argo-workflow`, which has cluster-wide pod read. From outside, the allow-list is the only barrier. **In-cluster, `argo-workflows-server:2746` is reachable from any pod**: there is no NetworkPolicy in `argo-workflows`. Never add a host rule without a `from:` clause.
- **Silence in Slack is ambiguous.** A clean week posts nothing, and so does a run that never started or died before `report`. There is no `onExit` handler, so hitting `activeDeadlineSeconds`, a failed `discover`, or a controller that was down at 04:00 for over an hour (`startingDeadlineSeconds`) all post nothing. If a Sunday passes quietly, look at the last run.
- **`Forbid` plus a wedged run blocks every following week.** `activeDeadlineSeconds` (90 min, versus about 34–40 min measured) exists to turn a hang into a failure.
- **Only our ECR images alert, with one exception.** An upstream CVE (Istio, Vault) lands in the S3 report and nobody is told (design §8, a deliberate trade-off). The exception, added 2026-10: a fixable CRITICAL/HIGH that is in CISA KEV alerts from any image, because exploitation is happening now.
- **The `trivy-server` daemon is a single point of failure.** If it dies, every scan fails. That's tolerated because `report` treats a missing report as unscanned, names the image, alerts, and turns the run red. Don't "simplify" that away.
- **A `crdinstaller` Job appears on every sync of `argo-workflows`.** That's expected. If it can't schedule or lacks RBAC, the sync stalls in PreSync and the controller is never upgraded. Look at the Job, not the chart.
- **`argo-workflow` is shared by every workflow here.** Widen it only for a workflow that demonstrably needs more, and prefer a separate ServiceAccount.
- **Stale comments to ignore:**
  - `image-scan.yaml`'s `podGC` comment says the run "fails by design whenever there is anything actionable". That stopped being true on 2026-08-18. `podGC: OnWorkflowCompletion` is still right, because logs are archived.
  - Its `ecr-registry` comment says "refreshed hourly". The ecr-auth CronJob runs `*/15`.
  - `base-apps/argo-workflows-aws-infrastructure/access-key.yaml` claims the key ID is under `attribute.id`. It's `username`, which is what the chart config and the aggregate step read.

## Runbook
### Symptom: the image scan didn't run this week
- **Check:**
  ```bash
  kubectl -n argo-workflows get cronworkflow image-scan -o yaml   # suspended? status.lastScheduledTime
  kubectl -n argo-workflows get workflows --sort-by=.metadata.creationTimestamp
  kubectl -n argo-workflows get pods                              # controller + server Running?
  ```
  With `Forbid`, a still-Running earlier run blocks the next one. A controller that was down at 04:00 for more than an hour skips the week.
- **Fix:** terminate the wedged run (UI or `argo terminate <wf> -n argo-workflows`), fix the controller, then run it by hand:
  `argo submit --from workflowtemplate/image-scan -n argo-workflows --watch`.

### Symptom: the image-scan run is red, or Slack/S3 didn't get the report
- **Check:** open the run in the UI. Pod logs are archived, so they're readable after podGC. The `report` step prints `scanned N images, M failed`, the `published s3://...` lines, and `posted to n8n`, or a `WARNING:` naming what failed.
- **Fix, by cause:**
  - **All scans failed:** `trivy-server` died or never became Ready. Check its pod log and the 4Gi DB volume.
  - **Some images unscanned:** the report names them (`<image>: no report (scan failed)`); read that image's scan log. If it ends in `context deadline exceeded`, the scan hit Trivy's 30-minute `--timeout`. `ollama/ollama` took 12–14 minutes and timed out at the old 15-minute limit. Otherwise a registry pull failed. For ECR images, check that `kubectl -n argo-workflows get secret ecr-registry` exists and is fresh (`base-apps/ecr-auth/`). For others, the image may be gone upstream. A scan pod evicted for exceeding its 4Gi `emptyDir` means an unusually large image.
  - **`report` never ran:** the workflow hit `activeDeadlineSeconds` or `discover` failed. Nothing was posted. Re-run by hand once fixed.
  - **`WARNING: S3 publish failed`:** credentials (next symptom), or `boto3` couldn't be installed (PyPI). The Argo artifact `full-report.json` is still there.
  - **`WARNING: n8n post failed`:** the n8n webhook `image-scan-report` isn't registered (404). Check the workflow in n8n. It's imported at pod start, so bump `checksum/workflows` in `base-apps/n8n/deployments.yaml` after editing it.

### Symptom: artifacts or logs not uploading (steps fail saving outputs, logs missing in the UI)
- **Check:**
  ```bash
  kubectl -n argo-workflows get secret argo-workflows-s3-creds -o jsonpath='{.data}' | python3 -c 'import json,sys; print(sorted(json.load(sys.stdin)))'
  # expect ['attribute.secret', 'username']
  kubectl get accesskeys.iam.aws.upbound.io argo-workflows-s3-key users.iam.aws.upbound.io argo-workflows-s3-user \
    userpolicyattachments.iam.aws.upbound.io argo-workflows-s3-user-policy
  ```
- **Fix:**
  - **Secret missing, or an MR not `SYNCED/READY`:** fix it on the Crossplane side (`docs/platform/crossplane.md`). If every MR is failing, suspect Crossplane's root credential `crossplane-system/aws-secret`.
  - **A test after fixing:** `argo submit --from workflowtemplate/artifact-example -n argo-workflows --watch`.
  - **Don't point the chart at `attribute.id`.** It doesn't exist.

### Symptom: the UI returns 403
- **Check:** is your public IP one of the four `/32`s in the `argo-workflows.arigsela.com` rule of `base-apps/istio-ingress/authorizationpolicy.yaml`? A 403 is the Gateway's allow-list refusing you. Every protected host 403ing at once usually means the home WAN address rotated.
- **Fix:** see `base-apps/istio-ingress/runbook.md` ("Every host 403s or times out at once") and `base-apps/wan-ip-monitor/runbook.md` (it opens the allow-list PR after a rotation). For break-glass access, `kubectl -n argo-workflows port-forward svc/argo-workflows-server 2746:2746` and browse `http://localhost:2746`. A 404, a TLS error or a 503 is a route, listener or certificate problem instead: see the same istio-ingress runbook.

### Upgrading Argo Workflows
1. Read the release notes for every chart and app version crossed. Check `base-apps/argo-workflow-tasks/` for removed fields. 4.0 removed the Python SDK, the singular `mutex`/`semaphore`/`schedule` fields (the CronWorkflow already uses `schedules:`), `podPriority` and `INFORMER_WRITE_BACK`.
2. Bump `targetRevision` in `base-apps/argo-workflows.yaml`. Keep `crds.full` and the `crds.upgradeJob` resources.
3. After the sync:
   - the `crdinstaller` Job completed;
   - the controller and server pods are Ready;
   - `argo submit --from workflowtemplate/hello-world-dag -n argo-workflows --watch` succeeds;
   - optionally, submit `image-scan` by hand.

   If Argo CD reports `field not declared in schema` on the workflow apps, that's the stale-schema issue in `base-apps/argo-cd/runbook.md`.
