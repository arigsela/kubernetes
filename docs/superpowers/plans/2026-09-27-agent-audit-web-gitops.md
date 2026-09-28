# agent-audit-web GitOps Implementation Plan (Plan B: the kubernetes repo)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the `agent-audit-web` app (built by Plan A in `~/git/agent-audit-web`) everything it needs in the cluster: its identities and secrets, a SELECT-only database role, read-only S3 access, an ECR push role for its CI, a Dex login client, and the deployment behind the gateway. Then prove it is locked down.

**Architecture:** One pod in namespace `agent-audit` runs two containers. `oauth2-proxy` listens on `:4180` and logs you in through Dex (GitHub), allowing one email. The app listens on `127.0.0.1:8000`. The gateway's per-host IP allow-list sits in front. The app reads Postgres as `kagent_audit_web_ro` and reads S3 through a List/Get-only IAM user. All credentials are per consumer: a dedicated ESO ServiceAccount, SecretStore, Vault role and Vault key for each, as `index.md` requires. AWS resources go through Crossplane where a provider exists (IAM). ECR and the Route 53 record are one-time manual operator steps, like their existing siblings.

**Tech Stack:** Argo CD (app-of-apps), External Secrets Operator + Vault (Kubernetes auth), Crossplane `provider-aws-iam`, Istio Gateway API (`HTTPRoute`, `ReferenceGrant`), cert-manager (`letsencrypt-route53`), Dex, oauth2-proxy v7.15.4, Postgres.

**Spec:** `~/git/agent-audit-web/docs/superpowers/specs/2026-09-27-agent-audit-web-design.md`. Sections 9, 12 and 14 are the ones this plan implements. Read them first.

**Companion plan (Plan A, the app):** `~/git/agent-audit-web/docs/superpowers/plans/2026-09-27-agent-audit-web-app.md`. **Sync points:**
1. Plan A Task 19 cannot push the image until this plan's **Phase 1** (Tasks 1–5) is merged and synced.
2. This plan's **Phase 2** (Tasks 6–8) needs the `tag@digest` that Plan A Task 19 prints.

## Status and notes from Plan A (2026-09-27)

**Start here: Phase 1 (Tasks 1–5).** Plan A Tasks 1–18 are done in `~/git/agent-audit-web` (`main`, 169 unit and 24 integration tests, a read-only run against the real database and archive passed). Plan A Task 19 is **blocked on this plan's Phase 1**: on 2026-09-27 the ECR repository `agent-audit-web` (us-east-2) and the role `github-actions-agent-audit-web-ecr` did not exist yet. The GitHub repo `arigsela/agent-audit-web` does not exist yet either; Plan A Task 19 creates it after Phase 1.

**Phase 1 deviation (2026-09-27):** the S3 reader's `AccessKey` was taken out of Task 2 and now ships in Task 6. Aimed at namespace `agent-audit` before it existed, it left `agent-audit-aws-infrastructure` Degraded. No key had been created in AWS, so nothing was lost.

What Plan A's implementation means for this plan:
- **Task 2's trust subject had to change after the first release (2026-09-28).** The shipped `release.yml` runs on `push: tags: ["v*"]` with no `environment:`. But GitHub gives new repos the *immutable* OIDC subject, so the token's `sub` is `repo:arigsela@2475907/agent-audit-web@1391584729:ref:refs/tags/<tag>`, not `repo:arigsela/agent-audit-web:...`. The first `v0.1.0` release failed with `Not authorized to perform sts:AssumeRoleWithWebIdentity` until the trust policy used that form (PR `fix/agent-audit-web-oidc-subject`). The ids are user `arigsela` (2475907) and repo `arigsela/agent-audit-web` (1391584729). The push itself uses `docker/build-push-action` (`provenance: false`, no registry cache), so the six ECR actions in the push policy are enough.
- **The image CMD is final:** `uvicorn --factory agent_audit_web.app:create_app_from_env --host 127.0.0.1 --port 8000 --no-server-header --no-access-log`. Task 6 must not set `command:` or `args:` on the app container. Overriding them would drop the loopback bind and `--no-access-log`, and `agent_audit_web.app:app` does not exist.
- **Readiness:** the app's database check now gives up after 1 s, so `/readyz` answers well within the probe's 3 s even when Postgres is down and the archive keeps the pod Ready. The probes in Task 6 are fine as written.
- **Deep links use `days` and `until`** (for example `/?days=730`, `/calls?days=90&agent=...`), not `since`.
- **The alert link opens `/?days=730`**, not `/` (Task 7). The Findings page defaults to 30 days, and the only real findings are months old, so `/` would show "no ungated invocations".
- **oauth2-proxy request logging is off** (Task 6). Its request lines include the query string, so `?q=<search text>` would reach Loki for 30 days. Spec §8 allows only counts, ids and error classes in logs. Login events stay logged (`--auth-logging` defaults to on).
- **A known app issue to watch in Task 8:** if the S3 user can list the bucket but every `GetObject` fails (for example, a policy or KMS mistake), `/readyz` still says `"archive":"ok"` with zero archive records. Task 8 Step 2 checks the refresh log line for failed files so this cannot hide. The fix belongs in the app (`records/archive.py`), not here.

## Global Constraints

- **Every cluster change goes through a Git PR.** Use no `kubectl apply/edit/patch/delete`. Read-only `kubectl get/describe/logs` and the read-only `psql` query in Task 3 are fine.
- **Ask the operator before merging each PR.** The standing admin-merge permission covers the k8s-1.36 plan only. Terraform is not touched by this plan.
- **This repo is public.** Never commit the allowed email, a client secret, a cookie secret or a password. They live only in Vault.
- **Crossplane labels: every new AWS resource uses `app: agent-audit-web`, never `app: agent-audit`.** The existing exporter's `AccessKey` (`userSelector`) and `UserPolicyAttachment` (`policyArnSelector`) select by `app: agent-audit` + `component`, and would silently also match new resources that reuse those labels. New resources reference each other by name (`userRef`, `roleRef`, `policyArnRef`), not by selector.
- Names are fixed:

| Item | Name |
|---|---|
| Namespace | `agent-audit` |
| Host | `agent-audit.arigsela.com` |
| Argo Application | `agent-audit-web` |
| Dex client | `agent-audit` |
| Database role | `kagent_audit_web_ro` |
| Vault keys | `k8s-secrets/agent-audit-web`, `k8s-secrets/agent-audit-web-db` |
| Vault roles and policies | `agent-audit-web`, `agent-audit-web-db` |
| ESO service accounts | `eso-agent-audit-web`, `eso-agent-audit-web-db` |
| S3 reader | IAM user `agent-audit-web-s3-read` |
| CI role | IAM role `github-actions-agent-audit-web-ecr` |
| ECR repository | `agent-audit-web` (us-east-2) |

- Images are pinned `tag@digest`. oauth2-proxy is `quay.io/oauth2-proxy/oauth2-proxy:v7.15.4@sha256:b1b2021fe8f4004573e8d690dec6c7bb29cc44364572cf8510a05bf3a0ae2ded`.
- Workloads must pass the admission policies: `app.kubernetes.io/name` label, CPU and memory limits on every container, no privileged containers, no unpinned images. The ECR pull secret is injected automatically.
- Follow the agent-docs contract (`templates/agent-docs/README.md`) for the new app directory.
- Before each PR, run the validators that match the files touched (Task steps list them). CI runs them all.

## Review Focus

1. **Crossplane selector collision.** The new IAM user/policy must not be picked up by the existing `agent-audit-s3-key` / `agent-audit-s3-user-policy` selectors. Pinned by Task 2 Step 4, which reads both existing resources back after sync.
2. **Dex keeps serving its old config.** Dex reads its config only at start. If the `checksum/config` annotation is not bumped, login fails at Dex with `Unregistered redirect_uri`. Pinned by Task 4 Step 2, which recomputes the annotation, and Step 4, which checks discovery and the client.
3. **The email claim does not match the allow-list.** A correct login would then get 403 from oauth2-proxy. Pinned by Task 8 Step 3, which covers the log line and the fix.
4. **A brand-new namespace has no ECR pull secret yet.** `ecr-auth` syncs `ecr-registry` into every namespace every 15 minutes, so the first pod can sit in `ImagePullBackOff` for up to 15 minutes. Pinned by Task 6 Step 6, where waiting is the fix. Do not "fix" it with manifests.
5. **WAN-IP rotation.** The new host must be rotation-proof in both halves. Its allow-list rule uses the same four /32s as its siblings (checked by `tests/wan_ip`), and its A record is in `MANAGED_HOSTNAMES`. Pinned by Task 2 Step 2 and Task 6 Step 3.

---

## Phase 1: prerequisites (merge before Plan A Task 19)

### Task 1: Vault keys, policies and roles (operator, manual)

Vault changes need a human. The Vault MCP server's AppRole cannot write policies. Reach Vault from the laptop by port-forward and log in with OIDC through Dex.

- [ ] **Step 1: Log in**

```bash
kubectl port-forward -n vault svc/vault 8200:8200 >/dev/null 2>&1 &
export VAULT_ADDR=http://127.0.0.1:8200
vault login -method=oidc
```

- [ ] **Step 2: Generate and store the secrets (the operator types the email)**

```bash
CLIENT_SECRET=$(openssl rand -hex 32)
COOKIE_SECRET=$(openssl rand -base64 32 | tr -- '+/' '-_')   # 32 random bytes, as oauth2-proxy expects
DB_PASSWORD=$(openssl rand -hex 24)
DB_NAME=$(vault kv get -mount=k8s-secrets -field=db-name kagent-audit-ro)   # the kagent database

vault kv put -mount=k8s-secrets agent-audit-web \
  oauth2-client-secret="$CLIENT_SECRET" \
  oauth2-cookie-secret="$COOKIE_SECRET" \
  allowed-emails="YOUR-GITHUB-PRIMARY-EMAIL"          # the operator's verified GitHub primary email
vault kv put -mount=k8s-secrets agent-audit-web-db \
  db-user=kagent_audit_web_ro db-password="$DB_PASSWORD" db-name="$DB_NAME"
# Dex must know the same client secret (its ExternalSecret reads key `dex`):
vault kv patch -mount=k8s-secrets dex agent-audit-client-secret="$CLIENT_SECRET"
unset CLIENT_SECRET COOKIE_SECRET DB_PASSWORD
```

- [ ] **Step 3: Policies and Kubernetes-auth roles (least privilege, one path each)**

```bash
vault policy write agent-audit-web - <<'EOF'
path "k8s-secrets/data/agent-audit-web" { capabilities = ["read"] }
EOF
vault policy write agent-audit-web-db - <<'EOF'
path "k8s-secrets/data/agent-audit-web-db" { capabilities = ["read"] }
EOF

# oauth2 secrets + allowed email: only the app namespace's ESO identity.
vault write auth/kubernetes/role/agent-audit-web \
  bound_service_account_names=eso-agent-audit-web \
  bound_service_account_namespaces=agent-audit \
  policies=agent-audit-web ttl=1h

# DB credential: the app (agent-audit) AND the role-creating init Job (postgresql).
# One role, two namespace bounds - the homelab-agent-db precedent.
vault write auth/kubernetes/role/agent-audit-web-db \
  bound_service_account_names=eso-agent-audit-web-db \
  bound_service_account_namespaces=agent-audit,postgresql \
  policies=agent-audit-web-db ttl=1h
```

- [ ] **Step 4: Verify without printing values**

```bash
vault kv get -format=json -mount=k8s-secrets agent-audit-web    | jq -c '.data.data | keys'
vault kv get -format=json -mount=k8s-secrets agent-audit-web-db | jq -c '.data.data | keys'
vault kv get -format=json -mount=k8s-secrets dex                | jq -c '.data.data | keys'
vault read -format=json auth/kubernetes/role/agent-audit-web-db | jq -c '.data.bound_service_account_namespaces'
```
Expected:
- `["allowed-emails","oauth2-client-secret","oauth2-cookie-secret"]`
- `["db-name","db-password","db-user"]`
- the `dex` keys now include `agent-audit-client-secret`
- `["agent-audit","postgresql"]`

---

### Task 2: AWS: ECR repository, DNS record, and Crossplane IAM (one PR)

**Files:**
- Create: `base-apps/agent-audit-aws-infrastructure/web-s3-read.yaml`, `base-apps/agent-audit-aws-infrastructure/web-ecr-push.yaml`
- Modify: `base-apps/wan-ip-monitor/cronjob.yaml` (`MANAGED_HOSTNAMES`)

- [ ] **Step 1: Operator: create the ECR repository and the A record (manual, one-time)**

Crossplane has no ECR provider here, and the arigsela.com A records are hand-managed. The WAN reconciler only updates records that already exist.

```bash
aws ecr create-repository --repository-name agent-audit-web --region us-east-2 \
  --image-tag-mutability IMMUTABLE --image-scanning-configuration scanOnPush=true

ZONE=$(aws route53 list-hosted-zones-by-name --dns-name arigsela.com --query 'HostedZones[0].Id' --output text)
WAN=$(grep 'arigsela.com/wan-ip:' base-apps/istio-ingress/authorizationpolicy.yaml | cut -d'"' -f2)
TTL=$(aws route53 list-resource-record-sets --hosted-zone-id "$ZONE" \
  --query "ResourceRecordSets[?Name=='chores.arigsela.com.' && Type=='A'].TTL | [0]" --output text)
echo "zone=$ZONE wan=$WAN ttl=$TTL"      # sanity-check all three before the next command
aws route53 change-resource-record-sets --hosted-zone-id "$ZONE" --change-batch "{
  \"Comment\": \"agent-audit-web (docs/superpowers/plans/2026-09-27-agent-audit-web-gitops.md)\",
  \"Changes\": [{\"Action\": \"CREATE\", \"ResourceRecordSet\": {
    \"Name\": \"agent-audit.arigsela.com\", \"Type\": \"A\", \"TTL\": $TTL,
    \"ResourceRecords\": [{\"Value\": \"$WAN\"}]}}]}"
```
Expected: the ECR call returns a `repositoryUri` ending in `/agent-audit-web`. The Route 53 call returns `"Status": "PENDING"`, and `dig +short agent-audit.arigsela.com` shows `$WAN` within a few minutes.

- [ ] **Step 2: Write the manifests**

`base-apps/agent-audit-aws-infrastructure/web-s3-read.yaml`:
```yaml
---
# agent-audit-web READS the durable agent action record: s3:ListBucket on the
# bucket and s3:GetObject on its objects. Nothing else - no Put, no Delete.
#
# LABELS: app: agent-audit-web, NEVER app: agent-audit. The exporter's AccessKey
# (userSelector) and UserPolicyAttachment (policyArnSelector) in this directory
# select by app: agent-audit + component; reusing those labels here would make
# them match these resources too. Cross-references below are by NAME.
#
# The records in the bucket are already redacted at extraction (scripts/agent-audit.py
# --export), and the app re-redacts on load. See the agent-audit-web spec, section 6.2.
#
# The AccessKey is NOT here. It writes its connection secret into namespace
# agent-audit, which only exists once the agent-audit-web app is deployed; aimed
# at a missing namespace it fails to reconcile and leaves this whole app Degraded.
# It arrives with the app (web-s3-read-key.yaml, plan Task 6).
apiVersion: iam.aws.upbound.io/v1beta1
kind: User
metadata:
  name: agent-audit-web-s3-read
  labels:
    app: agent-audit-web
    component: iam-user
    managed-by: crossplane
spec:
  forProvider:
    path: /serviceaccounts/
    tags:
      Name: "Agent Audit Web Reader"
      Purpose: "Agent-Audit-S3-Read"
      ManagedBy: "Crossplane"
      Environment: "homelab"
  providerConfigRef:
    name: default
---
apiVersion: iam.aws.upbound.io/v1beta1
kind: Policy
metadata:
  name: agent-audit-web-s3-read
  labels:
    app: agent-audit-web
    component: iam-policy
    managed-by: crossplane
spec:
  forProvider:
    description: "Read-only (List/Get) access to the agent audit record bucket"
    policy: |
      {
        "Version": "2012-10-17",
        "Statement": [
          {
            "Sid": "ListTheRecord",
            "Effect": "Allow",
            "Action": ["s3:ListBucket"],
            "Resource": "arn:aws:s3:::asela-agent-audit-record"
          },
          {
            "Sid": "ReadTheRecord",
            "Effect": "Allow",
            "Action": ["s3:GetObject"],
            "Resource": "arn:aws:s3:::asela-agent-audit-record/*"
          }
        ]
      }
    tags:
      Name: "Agent Audit Web S3 Read-Only Policy"
      ManagedBy: "Crossplane"
      Environment: "homelab"
  providerConfigRef:
    name: default
---
apiVersion: iam.aws.upbound.io/v1beta1
kind: UserPolicyAttachment
metadata:
  name: agent-audit-web-s3-read-policy
  labels:
    app: agent-audit-web
    component: iam-policy-attachment
    managed-by: crossplane
spec:
  forProvider:
    policyArnRef:
      name: agent-audit-web-s3-read
    userRef:
      name: agent-audit-web-s3-read
  providerConfigRef:
    name: default
```

`base-apps/agent-audit-aws-infrastructure/web-ecr-push.yaml`:
```yaml
---
# GitHub Actions in arigsela/agent-audit-web pushes its image to ECR through
# GitHub OIDC - no long-lived AWS keys in GitHub. Trust is limited to v* TAG
# builds of that one repo (its release.yml); the policy to that one repository.
# Same OIDC provider as github-actions-backstage-ecr, tighter subject.
apiVersion: iam.aws.upbound.io/v1beta1
kind: Role
metadata:
  name: github-actions-agent-audit-web-ecr
  labels:
    app: agent-audit-web
    component: ci-role
    managed-by: crossplane
spec:
  forProvider:
    description: "GitHub Actions (arigsela/agent-audit-web, v* tags) pushes agent-audit-web to ECR"
    maxSessionDuration: 3600
    assumeRolePolicy: |
      {
        "Version": "2012-10-17",
        "Statement": [
          {
            "Effect": "Allow",
            "Principal": {
              "Federated": "arn:aws:iam::852893458518:oidc-provider/token.actions.githubusercontent.com"
            },
            "Action": "sts:AssumeRoleWithWebIdentity",
            "Condition": {
              "StringEquals": {
                "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
              },
              "StringLike": {
                "token.actions.githubusercontent.com:sub": "repo:arigsela@2475907/agent-audit-web@1391584729:ref:refs/tags/v*"
              }
            }
          }
        ]
      }
    tags:
      ManagedBy: "Crossplane"
      Environment: "homelab"
  providerConfigRef:
    name: default
---
apiVersion: iam.aws.upbound.io/v1beta1
kind: Policy
metadata:
  name: agent-audit-web-ecr-push
  labels:
    app: agent-audit-web
    component: ci-policy
    managed-by: crossplane
spec:
  forProvider:
    description: "Push to the agent-audit-web ECR repository only"
    policy: |
      {
        "Version": "2012-10-17",
        "Statement": [
          {
            "Sid": "EcrLogin",
            "Effect": "Allow",
            "Action": ["ecr:GetAuthorizationToken"],
            "Resource": "*"
          },
          {
            "Sid": "PushThisRepositoryOnly",
            "Effect": "Allow",
            "Action": [
              "ecr:BatchCheckLayerAvailability",
              "ecr:BatchGetImage",
              "ecr:CompleteLayerUpload",
              "ecr:InitiateLayerUpload",
              "ecr:PutImage",
              "ecr:UploadLayerPart"
            ],
            "Resource": "arn:aws:ecr:us-east-2:852893458518:repository/agent-audit-web"
          }
        ]
      }
    tags:
      ManagedBy: "Crossplane"
      Environment: "homelab"
  providerConfigRef:
    name: default
---
apiVersion: iam.aws.upbound.io/v1beta1
kind: RolePolicyAttachment
metadata:
  name: github-actions-agent-audit-web-ecr-push
  labels:
    app: agent-audit-web
    component: ci-policy-attachment
    managed-by: crossplane
spec:
  forProvider:
    roleRef:
      name: github-actions-agent-audit-web-ecr
    policyArnRef:
      name: agent-audit-web-ecr-push
  providerConfigRef:
    name: default
```

In `base-apps/wan-ip-monitor/cronjob.yaml`, add `agent-audit.arigsela.com,` as the **first** entry of `MANAGED_HOSTNAMES`. It sorts before `agent.arigsela.com`. Also update the count in the comment above it: "now 21 again after jupyter.arigsela.com was added" becomes "…, now 22 after agent-audit.arigsela.com was added".

- [ ] **Step 3: Validate locally, open the PR, and ask the operator before merging**

```bash
python3 -c "import yaml,sys; [list(yaml.safe_load_all(open(f))) for f in sys.argv[1:]]; print('yaml ok')" \
  base-apps/agent-audit-aws-infrastructure/web-*.yaml base-apps/wan-ip-monitor/cronjob.yaml
python3 -m pytest tests/wan_ip/ tests/appset/ -q
```
Expected: `yaml ok`, and both test suites pass. The `managed-apps` golden file is unaffected, because the Application spec did not change.

Branch `feat/agent-audit-web-aws`, commit, push, and open a PR. The title is "agent-audit-web: S3 read user, ECR push role (Crossplane); DNS ownership". **Ask the operator before merging.**

- [ ] **Step 4: After sync, verify, including the selector-collision check (Review Focus 1)**

```bash
kubectl get user.iam.aws.upbound.io,policy.iam.aws.upbound.io,role.iam.aws.upbound.io \
  -l app=agent-audit-web -o custom-columns=KIND:.kind,NAME:.metadata.name,READY:.status.conditions[?(@.type==\"Ready\")].status
aws iam get-role --role-name github-actions-agent-audit-web-ecr --query 'Role.Arn' --output text
# The EXISTING exporter resources must still point only at their own user/policy:
kubectl get accesskey.iam.aws.upbound.io agent-audit-s3-key -o jsonpath='{.spec.forProvider.user}{"\n"}'
kubectl get userpolicyattachment.iam.aws.upbound.io agent-audit-s3-user-policy -o jsonpath='{.spec.forProvider.policyArn}{"\n"}'
```
Expected:
- every `READY` is `True` (there is no `AccessKey` yet; it arrives in Task 6);
- the role ARN `arn:aws:iam::852893458518:role/github-actions-agent-audit-web-ecr`;
- `agent-audit-s3-user`;
- an ARN ending in `:policy/agent-audit-s3-write`.

**If either of the last two shows an `agent-audit-web-*` name, STOP.** The selectors collided. Revert the PR.

---

### Task 3: The SELECT-only database role `kagent_audit_web_ro` (one PR)

This copies the `kagent-audit-ro` setup exactly (`base-apps/postgresql/*kagent-audit*`), with its own identity at every layer.

**Files (all in `base-apps/postgresql/`):**
- Create: `eso-agent-audit-web-db-serviceaccount.yaml`, `agent-audit-web-db-secret-store.yaml`, `external-secrets-agent-audit-web-db.yaml`, `init-agent-audit-web-role.yaml`

- [ ] **Step 1: Write the identity, store and secret**

`eso-agent-audit-web-db-serviceaccount.yaml`:
```yaml
---
# ESO identity for the agent-audit-web DB credential in this namespace (the init
# Job needs the password to create the role). Vault role agent-audit-web-db is
# bound to this SA here AND to its twin in namespace agent-audit.
apiVersion: v1
kind: ServiceAccount
metadata:
  name: eso-agent-audit-web-db
  namespace: postgresql
```

`agent-audit-web-db-secret-store.yaml`:
```yaml
---
apiVersion: external-secrets.io/v1
kind: SecretStore
metadata:
  name: vault-agent-audit-web-db
  namespace: postgresql
spec:
  provider:
    vault:
      server: http://vault.vault.svc.cluster.local:8200
      path: k8s-secrets
      version: v2
      auth:
        kubernetes:
          mountPath: kubernetes
          role: agent-audit-web-db
          serviceAccountRef:
            name: eso-agent-audit-web-db
```

`external-secrets-agent-audit-web-db.yaml`:
```yaml
---
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: agent-audit-web-db-credentials
  namespace: postgresql
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: vault-agent-audit-web-db
    kind: SecretStore
  target:
    name: agent-audit-web-db-credentials
    creationPolicy: Owner
  data:
    - secretKey: audit-user
      remoteRef:
        key: agent-audit-web-db
        property: db-user
    - secretKey: audit-password
      remoteRef:
        key: agent-audit-web-db
        property: db-password
    - secretKey: audit-database
      remoteRef:
        key: agent-audit-web-db
        property: db-name
```

- [ ] **Step 2: Derive the init Job from the existing one, so the SQL cannot drift**

```bash
cd base-apps/postgresql
sed -e 's/init-kagent-audit-role/init-agent-audit-web-role/g' \
    -e 's/kagent-audit-credentials/agent-audit-web-db-credentials/g' \
    -e "s/kagent audit role/agent-audit-web role/" \
    init-kagent-audit-role.yaml > init-agent-audit-web-role.yaml
diff init-kagent-audit-role.yaml init-agent-audit-web-role.yaml
cd -
```
Expected diff: only the Job name, the pod label, the three `secretKeyRef` names and the final `echo` line. **The SQL is identical.** Then replace the file's leading comment (everything above `apiVersion:`) with:

```yaml
---
# Creates the SELECT-only Postgres role agent-audit-web logs in as
# (kagent_audit_web_ro). Derived from init-kagent-audit-role.yaml with sed - keep
# the two in lockstep; the SQL (revoke-then-grant SELECT, NOINHERIT, default
# privileges) must stay identical.
#
# A separate role from kagent_audit_ro so each consumer's credential can be
# revoked on its own (one credential per consumer). Password from Vault via
# agent-audit-web-db-credentials; never in git. Idempotent Sync hook.
```

- [ ] **Step 3: Validate, open the PR, ask before merging, then prove the grants (read-only query)**

```bash
python3 -c "import yaml,sys; [list(yaml.safe_load_all(open(f))) for f in sys.argv[1:]]; print('yaml ok')" base-apps/postgresql/*agent-audit-web*.yaml
python3 scripts/validate-agent-identity.py --repo-root .
```
Expected: `yaml ok`, and the validator exits 0.

Branch `feat/agent-audit-web-db-role`, open the PR, and ask the operator before merging. After Argo syncs `postgresql`:

```bash
kubectl -n postgresql get externalsecret agent-audit-web-db-credentials   # STATUS SecretSynced
kubectl -n postgresql exec -i deploy/postgresql -- sh -c 'psql -U "$POSTGRES_USER" -d kagent -At' <<'SQL'
SELECT p, has_table_privilege('kagent_audit_web_ro', 'public.event', p)
  FROM unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE','TRUNCATE']) AS p;
SELECT 'CREATE on schema public', has_schema_privilege('kagent_audit_web_ro', 'public', 'CREATE');
SQL
```
Expected: `SELECT|t`, then `f` for INSERT, UPDATE, DELETE, TRUNCATE and CREATE. This asks Postgres about privileges and attempts no write. (If the kagent database has a different name, use the `db-name` from Task 1.)

---

### Task 4: Dex client `agent-audit` (one PR)

**Files:**
- Modify: `base-apps/dex/configmap.yaml`, `base-apps/dex/external-secret.yaml`, `base-apps/dex/deployment.yaml` (checksum)

- [ ] **Step 1: Add the client and its secret env**

In `base-apps/dex/configmap.yaml`, append to `staticClients`, after the `kubernetes` client and at the same indentation:

```yaml
      # agent-audit-web, added 2026-09-27
      # (docs/superpowers/plans/2026-09-27-agent-audit-web-gitops.md).
      # CONFIDENTIAL (secretEnv), unlike argocd/kubernetes above: the client is
      # oauth2-proxy, a server-side sidecar that can keep a secret - the same
      # reasoning as the vault client. It uses PKCE on top (S256).
      #
      # Dex admits ANY GitHub account (no orgs:), so this client grants nothing by
      # itself: oauth2-proxy's authenticated-emails-file, sourced from Vault and
      # never committed (this repo is public), is the allow-list.
      - id: agent-audit
        name: Agent audit
        secretEnv: AGENT_AUDIT_CLIENT_SECRET
        redirectURIs:
          - https://agent-audit.arigsela.com/oauth2/callback
```

In `base-apps/dex/external-secret.yaml`, add under `data:`:

```yaml
    - secretKey: AGENT_AUDIT_CLIENT_SECRET
      remoteRef:
        key: dex
        property: agent-audit-client-secret
```

Also update the header comment of `configmap.yaml`, which lists the substituted secrets (`$GITHUB_CLIENT_ID/$GITHUB_CLIENT_SECRET/$VAULT_CLIENT_SECRET`), so it includes `$AGENT_AUDIT_CLIENT_SECRET`.

- [ ] **Step 2: Bump the config checksum, or Dex keeps serving the old config (Review Focus 2)**

```bash
python3 -c "import hashlib,yaml; print(hashlib.sha256(yaml.safe_load(open('base-apps/dex/configmap.yaml'))['data']['config.yaml'].encode()).hexdigest()[:16])"
```
Put the printed value into `checksum/config:` in `base-apps/dex/deployment.yaml`. That command is the one documented above the annotation.

- [ ] **Step 3: Validate, open the PR, and ask before merging**

```bash
python3 -c "import yaml; d=yaml.safe_load(open('base-apps/dex/configmap.yaml')); c=yaml.safe_load(d['data']['config.yaml']); print([x['id'] for x in c['staticClients']])"
```
Expected: `['vault', 'argocd', 'kubernetes', 'agent-audit']`.

Branch `feat/agent-audit-web-dex-client`, open the PR, ask the operator before merging.

- [ ] **Step 4: After sync, verify**

```bash
kubectl -n dex get externalsecret -o custom-columns=NAME:.metadata.name,STATUS:.status.conditions[0].reason
kubectl -n dex rollout status deploy/dex --timeout=120s
kubectl -n dex logs deploy/dex --since=5m | grep -i -E "error|agent-audit" | head
curl -s https://dex.arigsela.com/.well-known/openid-configuration | jq -r .issuer
```
Expected: `SecretSynced`, a successful rollout, no config errors, and `https://dex.arigsela.com`. Dex logs no client list; the end-to-end login in Task 8 proves the client is registered.

---

### Task 5: Hand over to Plan A

- [ ] **Step 1: Tell the operator Phase 1 is done**

Plan A Task 19 can now push. It needs the ECR repository (Task 2 Step 1) and the role `github-actions-agent-audit-web-ecr` (Task 2 Step 4). **Wait for the image reference** Plan A prints, in the form `852893458518.dkr.ecr.us-east-2.amazonaws.com/agent-audit-web:v0.1.0@sha256:<64 hex>`. Phase 2 uses it verbatim.

---

## Phase 2: deploy (needs the image reference from Plan A Task 19)

### Task 6: The app, its route and its allow-list (one PR)

**Files:**
- Create: `base-apps/agent-audit-web.yaml`
- Create in `base-apps/agent-audit-web/`: `serviceaccounts.yaml`, `secret-stores.yaml`, `external-secrets.yaml`, `deployment.yaml`, `service.yaml`, `httproute.yaml`, `reference-grant.yaml`, `certificate.yaml`, `catalog-info.yaml`, `docs.md`, `runbook.md`, `mkdocs.yml`
- Create: `base-apps/agent-audit-aws-infrastructure/web-s3-read-key.yaml` (the S3 reader's AccessKey, moved here from Task 2)
- Modify: `base-apps/istio-ingress/gateway.yaml` (listener), `base-apps/istio-ingress/authorizationpolicy.yaml` (allow rule), `scripts/agent-docs-scope.txt`
- Generated: `base-apps/index.md` and `base-apps/agent-audit-web/docs/` (via `gen-okf.py` and `gen-techdocs.py`)

- [ ] **Step 1: The Argo Application**

`base-apps/agent-audit-web.yaml`:
```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  finalizers:
    - resources-finalizer.argocd.argoproj.io
  name: agent-audit-web
  namespace: argo-cd
spec:
  project: default
  source:
    repoURL: https://github.com/arigsela/kubernetes
    targetRevision: main
    path: base-apps/agent-audit-web
    directory:
      # Backstage entity + TechDocs config, not Kubernetes manifests.
      exclude: '{catalog-info.yaml,mkdocs.yml}'
  destination:
    server: https://kubernetes.default.svc
    namespace: agent-audit
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
```

- [ ] **Step 2: Identities, stores, secrets**

`base-apps/agent-audit-web/serviceaccounts.yaml`:
```yaml
---
# The workload identity. The app never calls the Kubernetes API: no token mounted.
apiVersion: v1
kind: ServiceAccount
metadata:
  name: agent-audit-web
  namespace: agent-audit
automountServiceAccountToken: false
---
# One ESO identity per Vault key (per-consumer credentials; see index.md).
apiVersion: v1
kind: ServiceAccount
metadata:
  name: eso-agent-audit-web
  namespace: agent-audit
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: eso-agent-audit-web-db
  namespace: agent-audit
```

`base-apps/agent-audit-web/secret-stores.yaml`:
```yaml
---
apiVersion: external-secrets.io/v1
kind: SecretStore
metadata:
  name: vault-agent-audit-web
  namespace: agent-audit
spec:
  provider:
    vault:
      server: http://vault.vault.svc.cluster.local:8200
      path: k8s-secrets
      version: v2
      auth:
        kubernetes:
          mountPath: kubernetes
          role: agent-audit-web
          serviceAccountRef:
            name: eso-agent-audit-web
---
apiVersion: external-secrets.io/v1
kind: SecretStore
metadata:
  name: vault-agent-audit-web-db
  namespace: agent-audit
spec:
  provider:
    vault:
      server: http://vault.vault.svc.cluster.local:8200
      path: k8s-secrets
      version: v2
      auth:
        kubernetes:
          mountPath: kubernetes
          role: agent-audit-web-db
          serviceAccountRef:
            name: eso-agent-audit-web-db
```

`base-apps/agent-audit-web/external-secrets.yaml`:
```yaml
---
# oauth2-proxy's client secret, cookie secret and the email allow-list. The email
# lives only in Vault: this repo is public.
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: agent-audit-web-oauth2
  namespace: agent-audit
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: vault-agent-audit-web
    kind: SecretStore
  target:
    name: agent-audit-web-oauth2
    creationPolicy: Owner
  data:
    - secretKey: client-secret
      remoteRef:
        key: agent-audit-web
        property: oauth2-client-secret
    - secretKey: cookie-secret
      remoteRef:
        key: agent-audit-web
        property: oauth2-cookie-secret
    - secretKey: allowed-emails
      remoteRef:
        key: agent-audit-web
        property: allowed-emails
---
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: agent-audit-web-db
  namespace: agent-audit
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: vault-agent-audit-web-db
    kind: SecretStore
  target:
    name: agent-audit-web-db
    creationPolicy: Owner
  data:
    - secretKey: db-user
      remoteRef:
        key: agent-audit-web-db
        property: db-user
    - secretKey: db-password
      remoteRef:
        key: agent-audit-web-db
        property: db-password
    - secretKey: db-name
      remoteRef:
        key: agent-audit-web-db
        property: db-name
```

`base-apps/agent-audit-aws-infrastructure/web-s3-read-key.yaml`:
```yaml
---
# The S3 reader's access key, written straight into the app's namespace as
# agent-audit-web-s3-creds (keys: username, attribute.secret). It ships in the SAME
# PR as the agent-audit-web Application that creates namespace agent-audit: aimed
# at a namespace that does not exist, it fails to reconcile and turns
# agent-audit-aws-infrastructure Degraded (that happened in Phase 1; see PR #635).
# The two apps sync independently, so a Degraded blip of one reconcile is possible
# here and clears on its own once the namespace exists.
apiVersion: iam.aws.upbound.io/v1beta1
kind: AccessKey
metadata:
  name: agent-audit-web-s3-read-key
  labels:
    app: agent-audit-web
    component: access-key
    managed-by: crossplane
spec:
  forProvider:
    userRef:
      name: agent-audit-web-s3-read
  writeConnectionSecretToRef:
    name: agent-audit-web-s3-creds
    namespace: agent-audit
  providerConfigRef:
    name: default
```

- [ ] **Step 3: Workload, service, route, certificate**

`base-apps/agent-audit-web/deployment.yaml`. Replace the app image with **the exact reference from Plan A Task 19**:
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: agent-audit-web
  namespace: agent-audit
  labels:
    app: agent-audit-web
    app.kubernetes.io/name: agent-audit-web
spec:
  replicas: 1
  selector:
    matchLabels:
      app: agent-audit-web
  template:
    metadata:
      labels:
        app: agent-audit-web
        app.kubernetes.io/name: agent-audit-web
    spec:
      serviceAccountName: agent-audit-web
      automountServiceAccountToken: false
      nodeSelector:
        node.kubernetes.io/workload: application
      securityContext:
        runAsNonRoot: true
        seccompProfile:
          type: RuntimeDefault
      containers:
        # The app binds 127.0.0.1:8000 (image CMD). The ONLY way in is oauth2-proxy
        # below, in the same pod. The Service targets 4180, never 8000.
        # No command:/args: here - the image CMD carries the loopback bind and
        # --no-access-log (request lines would put ?q= search text in Loki).
        - name: app
          image: 852893458518.dkr.ecr.us-east-2.amazonaws.com/agent-audit-web:v0.1.0@sha256:REPLACE-WITH-PLAN-A-TASK-19-DIGEST
          env:
            - name: AUDIT_DB_HOST
              value: postgresql.postgresql.svc.cluster.local
            - name: AUDIT_DB_PORT
              value: "5432"
            - name: AUDIT_DB_NAME
              valueFrom:
                secretKeyRef:
                  name: agent-audit-web-db
                  key: db-name
            - name: AUDIT_DB_USER
              valueFrom:
                secretKeyRef:
                  name: agent-audit-web-db
                  key: db-user
            - name: AUDIT_DB_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: agent-audit-web-db
                  key: db-password
            - name: AUDIT_S3_BUCKET
              value: asela-agent-audit-record
            - name: AUDIT_S3_REGION
              value: us-east-1
            # Crossplane AccessKey connection secret (agent-audit-aws-infrastructure/web-s3-read.yaml).
            - name: AWS_ACCESS_KEY_ID
              valueFrom:
                secretKeyRef:
                  name: agent-audit-web-s3-creds
                  key: username
            - name: AWS_SECRET_ACCESS_KEY
              valueFrom:
                secretKeyRef:
                  name: agent-audit-web-s3-creds
                  key: attribute.secret
          resources:
            requests:
              cpu: 50m
              memory: 128Mi
            limits:
              cpu: 500m
              memory: 384Mi
          # exec probes: the app listens on loopback, which kubelet cannot reach.
          startupProbe:
            exec:
              command: ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"]
            periodSeconds: 5
            # Spawning python for an exec probe takes 1.4-2.2 s on these nodes
            # (measured in the pod); the 1 s default made this probe unpassable.
            timeoutSeconds: 5
            failureThreshold: 24
          livenessProbe:
            exec:
              command: ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"]
            periodSeconds: 20
            timeoutSeconds: 5
            failureThreshold: 3
          readinessProbe:
            # /readyz is 200 while the database OR the archive works (503 raises).
            exec:
              command: ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=3)"]
            periodSeconds: 15
            timeoutSeconds: 5
            failureThreshold: 3
          securityContext:
            runAsUser: 10001
            runAsGroup: 10001
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop: ["ALL"]
          volumeMounts:
            - name: tmp
              mountPath: /tmp
        - name: oauth2-proxy
          image: quay.io/oauth2-proxy/oauth2-proxy:v7.15.4@sha256:b1b2021fe8f4004573e8d690dec6c7bb29cc44364572cf8510a05bf3a0ae2ded
          args:
            - --http-address=0.0.0.0:4180
            - --upstream=http://127.0.0.1:8000/
            - --provider=oidc
            - --provider-display-name=GitHub (via Dex)
            - --oidc-issuer-url=https://dex.arigsela.com
            - --client-id=agent-audit
            - --client-secret-file=/etc/oauth2-proxy/secrets/client-secret
            - --cookie-secret-file=/etc/oauth2-proxy/secrets/cookie-secret
            - --code-challenge-method=S256
            - --scope=openid email profile
            - --redirect-url=https://agent-audit.arigsela.com/oauth2/callback
            # THE access control: Dex admits any GitHub account. One email, from Vault.
            - --authenticated-emails-file=/etc/oauth2-proxy/secrets/allowed-emails
            - --cookie-name=_agent_audit
            - --cookie-secure=true
            - --cookie-samesite=lax
            - --cookie-expire=8h
            - --skip-provider-button=true
            - --silence-ping-logging=true
            # Request lines carry the query string (?q=<search text>) and would sit
            # in Loki for 30 days (spec section 8). Login events stay logged
            # (--auth-logging defaults to true).
            - --request-logging=false
          ports:
            - containerPort: 4180
              name: http
          resources:
            requests:
              cpu: 10m
              memory: 32Mi
            limits:
              cpu: 200m
              memory: 128Mi
          livenessProbe:
            httpGet:
              path: /ping
              port: 4180
            periodSeconds: 20
          readinessProbe:
            httpGet:
              path: /ping
              port: 4180
            periodSeconds: 10
          securityContext:
            runAsUser: 65532
            runAsGroup: 65532
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop: ["ALL"]
          volumeMounts:
            - name: oauth2-secrets
              mountPath: /etc/oauth2-proxy/secrets
              readOnly: true
      volumes:
        - name: tmp
          emptyDir:
            sizeLimit: 64Mi
        - name: oauth2-secrets
          secret:
            secretName: agent-audit-web-oauth2
            defaultMode: 0444
```

`base-apps/agent-audit-web/service.yaml`:
```yaml
apiVersion: v1
kind: Service
metadata:
  name: agent-audit-web
  namespace: agent-audit
  labels:
    app: agent-audit-web
    app.kubernetes.io/name: agent-audit-web
spec:
  type: ClusterIP
  selector:
    app: agent-audit-web
  ports:
    # oauth2-proxy. The app's own port (8000, loopback) is deliberately absent.
    - name: http
      port: 4180
      targetPort: 4180
      protocol: TCP
```

`base-apps/agent-audit-web/httproute.yaml`:
```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: agent-audit-web
  namespace: agent-audit
  annotations:
    gethomepage.dev/enabled: "true"
    gethomepage.dev/name: Agent audit
    gethomepage.dev/group: AI & Agents
    gethomepage.dev/icon: mdi-shield-search
    gethomepage.dev/description: Agent tool calls, findings and token use (redacted)
    gethomepage.dev/pod-selector: app=agent-audit-web
spec:
  parentRefs:
    - name: main
      namespace: istio-ingress
      sectionName: https-agent-audit
  hostnames:
    - agent-audit.arigsela.com
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /
      backendRefs:
        - name: agent-audit-web
          port: 4180
```

`base-apps/agent-audit-web/reference-grant.yaml`:
```yaml
apiVersion: gateway.networking.k8s.io/v1beta1
kind: ReferenceGrant
metadata:
  name: gateway-to-agent-audit-web-tls
  namespace: agent-audit
spec:
  from:
    - group: gateway.networking.k8s.io
      kind: Gateway
      namespace: istio-ingress
  to:
    - group: ""
      kind: Secret
      name: agent-audit-web-tls
```

`base-apps/agent-audit-web/certificate.yaml`:
```yaml
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: agent-audit-web-tls
  namespace: agent-audit
spec:
  secretName: agent-audit-web-tls
  dnsNames:
    - agent-audit.arigsela.com
  issuerRef:
    name: letsencrypt-route53
    kind: ClusterIssuer
```

- [ ] **Step 4: Gateway listener and the IP allow-list rule (Review Focus 5)**

In `base-apps/istio-ingress/gateway.yaml`, add a listener next to `https-chores`:
```yaml
    - name: https-agent-audit
      protocol: HTTPS
      port: 443
      hostname: agent-audit.arigsela.com
      tls:
        mode: Terminate
        certificateRefs:
          - kind: Secret
            name: agent-audit-web-tls
            namespace: agent-audit
      allowedRoutes:
        namespaces:
          from: All
```

In `base-apps/istio-ingress/authorizationpolicy.yaml`, add a rule. **Copy the `ipBlocks` list byte-for-byte from the `chores.arigsela.com` rule as it is in the file at that moment.** The first entry must be the address in the `arigsela.com/wan-ip` annotation.
```yaml
    # agent-audit - restricted. Serves full (best-effort redacted) agent tool
    # arguments. This allow-list is the first layer; oauth2-proxy + Dex with a
    # one-email allow-list is the second (base-apps/agent-audit-web/).
    - to:
        - operation:
            hosts:
              - agent-audit.arigsela.com
              - agent-audit.arigsela.com:*
      from:
        - source:
            ipBlocks:
              - 76.97.4.210/32      # = arigsela.com/wan-ip at time of writing; copy the live list
              - 170.85.56.189/32
              - 170.85.130.202/32
              - 104.28.177.82/32
```

- [ ] **Step 5: The agent-docs contract**

```bash
for f in catalog-info.yaml docs.md runbook.md; do cp templates/agent-docs/$f base-apps/agent-audit-web/$f; done
cp base-apps/donetick/mkdocs.yml base-apps/agent-audit-web/mkdocs.yml
sed -i.bak 's/^site_name: donetick$/site_name: agent-audit-web/' base-apps/agent-audit-web/mkdocs.yml && rm base-apps/agent-audit-web/mkdocs.yml.bak
echo agent-audit-web >> scripts/agent-docs-scope.txt
```

Fill in the templates with these facts:

- **`catalog-info.yaml`:** `name: agent-audit-web`, `namespace: agent-audit`, selector `app=agent-audit-web`, `system: default/platform-observability`, `type: service`, `lifecycle: production`, `owner: platform`, tags `[fastapi, audit, oauth2-proxy]`, and `dependsOn` of `resource:vault/vault`, `resource:postgresql/postgresql` and `component:dex/dex`. These are the refs as they exist today; `scripts/validate-catalog-refs.py` checks them.
- **`docs.md` frontmatter:** `title: "Agent Audit Web"`, `description: "Read-only web UI over the redacted agent action record (findings, calls, sessions, token trends)"`, `app: agent-audit-web`, `catalog_entity: agent-audit-web`, `namespace: agent-audit`, `last_reviewed: <today>`, tags `[audit, agents, fastapi, oauth2-proxy]`, and `sources:` listing every manifest in the directory, `../agent-audit-aws-infrastructure/web-s3-read.yaml`, `../postgresql/init-agent-audit-web-role.yaml` and `../dex/configmap.yaml`.
- **`docs.md` body:**
  - What it is: the spec §1–2 in one paragraph, with a link to the app repo `arigsela/agent-audit-web`.
  - Architecture: the spec §4 request-flow diagram.
  - Where config lives: the Vault keys and roles, the DB role and init Job, the Crossplane S3 reader, the Dex client, and the image, which is pinned in `deployment.yaml` and released by the app repo's `release.yml`.
  - Redaction: the app vendors `scripts/agent-audit.py`, so **changing that script means re-vendoring in the app repo**. The app repo's daily drift job opens an issue when it changes.
- **`runbook.md`** gets these failure modes, each as symptom → check → fix:
  1. **Dex says `Unregistered redirect_uri` or `invalid client`.** Check the `checksum/config` annotation and the Dex logs. Fix: the client is missing from the config, or the checksum was not bumped.
  2. **Login works but oauth2-proxy returns 403.** Check `kubectl -n agent-audit logs deploy/agent-audit-web -c oauth2-proxy | grep -i -E "permission|unauthorized|email"`. Fix: the email Dex got from GitHub (primary, verified) is not the value in Vault `agent-audit-web/allowed-emails`. Correct it in Vault, then force a refresh with `kubectl -n agent-audit annotate externalsecret agent-audit-web-oauth2 force-sync=$(date +%s) --overwrite` and wait for the pod to pick up the file. Kubelet refreshes secret volumes in about a minute.
  3. **Banner "Live database unavailable".** Check the app logs for `database query failed: <ErrorClass>`, the ExternalSecret `agent-audit-web-db`, and the init Job in `postgresql`. Fix: Vault key or role, or re-run the Sync hook by syncing the `postgresql` app.
  4. **Banner "Archive unavailable".** Check the app logs for `archive refresh failed: <ErrorClass>` and `kubectl get accesskey.iam.aws.upbound.io agent-audit-web-s3-read-key`. Fix: Crossplane key or policy.
  5. **`ImagePullBackOff` on a first deploy or a new namespace.** `ecr-auth` copies `ecr-registry` every 15 minutes. Wait.
  6. **Releasing a new version.** Tag `vX.Y.Z` in the app repo, then put the printed `tag@digest` into `deployment.yaml` in a PR.
  7. **Rotating the oauth2 client secret.** Update both `k8s-secrets/dex:agent-audit-client-secret` and `k8s-secrets/agent-audit-web:oauth2-client-secret` to the same new value. Force both ExternalSecrets to refresh. Restart Dex via a checksum bump PR, because Dex reads env only at start.
  8. **Revoking access fast.** Remove the email from Vault and force-sync as in item 2. For a full stop, delete the `gateway-allow` rule in a PR.

Then generate the derived files:
```bash
python3 scripts/gen-okf.py --repo-root .
python3 scripts/gen-techdocs.py --repo-root .
```

- [ ] **Step 6: Validate everything CI will check, then open the PR**

```bash
python3 -c "import yaml,sys; [list(yaml.safe_load_all(open(f))) for f in sys.argv[1:]]; print('yaml ok')" \
  base-apps/agent-audit-web.yaml base-apps/agent-audit-web/*.yaml base-apps/istio-ingress/*.yaml
grep -n "REPLACE" base-apps/agent-audit-web/*.yaml base-apps/agent-audit-web/*.md && echo "UNFILLED - STOP" || echo "no placeholders"
python3 scripts/validate-agent-docs.py --repo-root .
python3 scripts/validate-catalog-refs.py --repo-root .
python3 scripts/validate-waf-scope.py --repo-root .
python3 scripts/validate-agent-identity.py --repo-root .
python3 scripts/gen-okf.py --repo-root . --check
python3 scripts/gen-techdocs.py --repo-root . --check
python3 -m pytest tests/wan_ip/ tests/agent-docs/ tests/okf/ tests/catalog-refs/ tests/waf/ -q
```
Expected: `yaml ok`, `no placeholders`, and every validator and test passes. `validate-waf-scope.py` passes because the host has a `from:` clause, so it is not public.

Branch `feat/agent-audit-web-deploy`, open the PR, **ask the operator before merging**.

After the merge, the first pod may show `ImagePullBackOff` for up to 15 minutes until `ecr-auth` copies `ecr-registry` into the new namespace (Review Focus 4). Wait for it.

---

### Task 7: Wire the alert and the docs to the app (one small PR)

**Files:**
- Modify: `base-apps/logging/grafana-alerting.yaml`, `index.md`, `docs/adp-resources-and-observability.md`

- [ ] **Step 1: Edit**

In `base-apps/logging/grafana-alerting.yaml`, rule `agent-ungated-tool`, replace the `runbook:` annotation text with:
```yaml
              runbook: >-
                Open https://agent-audit.arigsela.com/?days=730 (Findings, the
                longest window - this alert covers all history) and click the
                finding to see its session - which agent, which tool, and with what
                (redacted) arguments, in order. Or run `scripts/agent-audit.py
                --ungated` against the kagent database (SELECT-only role).
                Arguments are deliberately absent from this alert.
```

In `index.md`, in the **Observability** bullet, after "checked by a scheduled job (…)", add: "and browsable at `agent-audit.arigsela.com` (`base-apps/agent-audit-web/`, app repo `arigsela/agent-audit-web`)". In the "Agent audit & eval" row of the where-to-look table, add `base-apps/agent-audit-web/`.

In `docs/adp-resources-and-observability.md`, add this row to the services table:
`| **Agent audit** | https://agent-audit.arigsela.com | The agent action record: findings, calls, session timelines, token trends (read-only, redacted, SSO). |`
Also add a sentence before the "Run the audit tool by hand" block: "For browsing, use https://agent-audit.arigsela.com; the CLI below is still the way to script it."

- [ ] **Step 2: Validate, open the PR, and ask before merging**

```bash
python3 -c "import yaml; list(yaml.safe_load_all(open('base-apps/logging/grafana-alerting.yaml'))); print('yaml ok')"
python3 scripts/gen-okf.py --repo-root . --check && python3 -m pytest tests/okf/ -q
```

---

### Task 8: End-to-end verification (spec success criteria 3, 4 and 6; the CPU check)

- [ ] **Step 1: Everything is running**

```bash
kubectl -n agent-audit get externalsecret,certificate,pods
kubectl -n agent-audit get secret agent-audit-web-s3-creds -o jsonpath='{.data}' | jq -c 'keys'
kubectl get accesskey.iam.aws.upbound.io agent-audit-web-s3-read-key -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}{"\n"}'
```
Expected:
- both ExternalSecrets `SecretSynced`;
- the Certificate `True`;
- the pod `2/2 Running`, which is **the image-on-a-real-node CPU check** (no AVX2 on these nodes);
- keys including `username` and `attribute.secret`;
- `True`.

- [ ] **Step 2: The app sees both sources**

```bash
kubectl -n agent-audit exec deploy/agent-audit-web -c app -- python -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/readyz').read().decode())"
kubectl -n agent-audit logs deploy/agent-audit-web -c app | grep -E "starting:|archive refresh" | head -3
```
Expected: `{"database":"ok","archive":"ok"}`. The logs show `starting: database=on archive=on gated_tools=N` and `archive refresh: N new files, 0 failed, N total, M skipped lines`, with N greater than 0.

**If the refresh line shows `0 new files, K failed` (or any `archive file ... failed:` warning), the archive is NOT really working, even though `/readyz` says `ok`.** That is a known app issue: a successful bucket listing marks the archive loaded. The usual cause is the S3 policy or KMS: check `agent-audit-web-s3-read` grants `s3:GetObject` on `arn:aws:s3:::asela-agent-audit-record/*`. The error class in the warning (`AccessDenied`, `NoSuchKey`, ...) says which.

- [ ] **Step 3: Login is enforced (success criterion 3; Review Focus 3)**

```bash
curl -s -o /dev/null -w "%{http_code} %{redirect_url}\n" https://agent-audit.arigsela.com/
```
Expected: `302` redirecting to `https://agent-audit.arigsela.com/oauth2/start?...` or straight to `https://dex.arigsela.com/auth?...`. It must **never** be a `200`.

Then, in a browser, open `https://agent-audit.arigsela.com/` and log in with GitHub. Expected: the Findings page. If you get oauth2-proxy's 403 page instead, follow runbook item 2.

Negative check: a different GitHub account (a private window, if one is available) must get 403 after the GitHub login. If no second account is available, run `kubectl -n agent-audit logs deploy/agent-audit-web -c oauth2-proxy | grep -c "Permission Denied"` after a deliberate failed attempt, or record this check as not done in the PR.

- [ ] **Step 4: The database role cannot write (success criterion 4)**

Re-run the privilege query from Task 3 Step 3. Expected: `SELECT|t`, and `f` for everything else.

- [ ] **Step 5: The pages, against real data**

In the browser:
- [ ] `/?days=120` shows the 14 `k8s_execute_command` findings from 2026-07-09.
- [ ] One of those findings opens its session.
- [ ] Some session is labelled **archive only** if any session has been deleted from kagent (success criterion 6). If none has, that is fine: note it.
- [ ] `/tokens` draws its chart, and the browser console shows no CSP errors.

- [ ] **Step 6: Record it**

Update `base-apps/agent-audit-web/docs.md` `last_reviewed:` if any fact changed during verification. In the PR or the operator's notes, record the results of Steps 1–5, including anything marked not done.
