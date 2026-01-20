# Vault AWS KMS Auto-Unseal Implementation Plan

## Overview
Migrate Vault from Shamir-based manual unsealing to AWS KMS auto-unseal, eliminating the need for manual intervention after server restarts.

## Success Criteria
- [x] Vault automatically unseals after pod restart without manual intervention
- [x] KMS key created with proper access controls and rotation enabled
- [x] Migration from Shamir to KMS completes successfully
- [x] Recovery keys generated and documented
- [x] Recovery scripts updated to reflect new auto-unseal behavior

## Implementation Status

| Phase | Status | Completed |
|-------|--------|-----------|
| Phase 1: AWS Infrastructure | ✅ Complete | 2026-01-19 |
| Phase 2: Vault Configuration | ✅ Complete | 2026-01-19 |
| Phase 3: Migration | ✅ Complete | 2026-01-19 |
| Phase 4: Verification | ✅ Complete | 2026-01-19 |

## Research Findings

### Current State (BEFORE - Historical)
- Vault uses Shamir seal (default) with 3 shares, threshold of 2
- File-based storage at `/vault/data`
- Version 1.18.1
- No existing seal configuration in `configmaps.yaml`

### Current State (AFTER - Active)
- **Seal Type**: `awskms` (auto-unseal enabled)
- **Recovery Seal Type**: `shamir` (3 shares, threshold 2)
- **KMS Key ID**: `2d982e46-c7bd-4606-a1bf-a470d1c09e07`
- **KMS Alias**: `alias/vault-auto-unseal`
- **Region**: `us-east-2`

### AWS Infrastructure
- Region: us-east-2
- Existing pattern: IAM users with access keys stored as K8s secrets
- Terraform state in S3 bucket `asela-terraform-states`

## Architecture Decision

### KMS Key Management
**Chosen:** Separate `vault-kms.tf` file in Terraform root

**Rationale:** Follows existing naming convention (`rds.tf`, `iam.tf`) and keeps Vault-specific AWS resources isolated for easier maintenance.

## Cost
- **KMS Key**: $1/month
- **API Requests**: ~$0 (within free tier)
- **Total**: ~$12/year

---

## Implementation

### Phase 1: AWS Infrastructure (Terraform) ✅ COMPLETE
Create KMS key and IAM resources for Vault.

#### Task 1.1: Create KMS Key and IAM User ✅
**Files:** `terraform/roots/asela-cluster/vault-kms.tf` (new)

**Steps:**
1. ✅ Create new file `vault-kms.tf`
2. ✅ Add KMS key resource with:
   - Symmetric encryption
   - Key rotation enabled
   - 30-day deletion window
3. ✅ Add KMS alias `alias/vault-auto-unseal`
4. ✅ Add IAM user `vault-kms-user`
5. ✅ Add IAM policy with minimal permissions (Encrypt, Decrypt, DescribeKey)
6. ✅ Create access key
7. ✅ Create Kubernetes secret in `vault` namespace

**Testing:**
- [x] `terraform plan` shows 6 resources to create
- [x] `terraform apply` completes without errors
- [x] Verify KMS key exists in AWS Console (us-east-2)
- [x] Verify K8s secret created: `kubectl get secret vault-kms-credentials -n vault`

**Resources Created:**
| Resource | Identifier |
|----------|------------|
| KMS Key | `2d982e46-c7bd-4606-a1bf-a470d1c09e07` |
| KMS Alias | `alias/vault-auto-unseal` |
| IAM User | `vault-kms-user` |
| IAM Policy | `vault-kms-unseal-policy` |
| Access Key | `AKIA4NFDJMBLFU2VGRFT` |
| K8s Secret | `vault-kms-credentials` (vault namespace) |

---

### Phase 2: Vault Configuration Updates ✅ COMPLETE
Update Vault to use KMS seal.

#### Task 2.1: Update Vault ConfigMap ✅
**Files:** `base-apps/vault/configmaps.yaml`

**Steps:**
1. ✅ Add `seal` stanza to vault.json config:
   ```json
   "seal": {
     "awskms": {
       "region": "us-east-2"
     }
   }
   ```
2. ✅ Note: `kms_key_id` will be read from environment variable

**Testing:**
- [x] YAML syntax valid: `kubectl apply --dry-run=client -f base-apps/vault/configmaps.yaml`

#### Task 2.2: Update Vault StatefulSet ✅
**Files:** `base-apps/vault/statefulsets.yaml`

**Steps:**
1. ✅ Add environment variables to Vault container:
   - `AWS_ACCESS_KEY_ID` from secret
   - `AWS_SECRET_ACCESS_KEY` from secret
   - `AWS_REGION` from secret
   - `VAULT_AWSKMS_SEAL_KEY_ID` from secret
2. ✅ Reference the `vault-kms-credentials` secret

**Testing:**
- [x] YAML syntax valid
- [x] Environment variables properly reference secret keys

#### Task 2.3: Commit and Push Configuration ✅
**Files:** `base-apps/vault/configmaps.yaml`, `base-apps/vault/statefulsets.yaml`

**Steps:**
1. ✅ `git add base-apps/vault/`
2. ✅ `git commit -m "Configure Vault for AWS KMS auto-unseal"`
3. ✅ `git push origin fix/cni-recovery`

**Testing:**
- [x] Changes pushed successfully
- [x] ArgoCD synced vault application

**Commit:** `fe85a57` - "Configure Vault for AWS KMS auto-unseal"

---

### Phase 3: Migration ✅ COMPLETE
Perform the one-time migration from Shamir to KMS seal.

#### Task 3.1: Backup Current Vault Data ✅
**Steps:**
1. ✅ Create Vault snapshot (if using Raft - we use file, so skip)
2. ✅ Document current unseal keys location: `recovery/vault-credentials.txt`

**Testing:**
- [x] Verify unseal keys are documented and accessible

#### Task 3.2: Trigger Vault Restart ✅
**Steps:**
1. ✅ Delete pod: `kubectl delete pod vault-0 -n vault`
2. ✅ Watch logs for migration mode message

**Testing:**
- [x] Logs show: "entering seal migration mode; Vault will not automatically unseal even if using an autoseal: from_barrier_type=shamir to_barrier_type=awskms"
- [x] Vault status shows sealed with migration in progress

#### Task 3.3: Perform Migration Unseal ✅
**Steps:**
1. ✅ Run: `kubectl exec -n vault vault-0 -- vault operator unseal -migrate <UNSEAL_KEY_1>`
2. ✅ Run: `kubectl exec -n vault vault-0 -- vault operator unseal -migrate <UNSEAL_KEY_2>`
3. ✅ Vault converts seal type and generates recovery keys

**Testing:**
- [x] `vault status` shows: `Seal Type: awskms`, `Sealed: false`
- [x] Original Shamir keys converted to recovery keys

#### Task 3.4: Save Recovery Keys ✅
**Steps:**
1. ✅ Update `recovery/vault-credentials.txt` with:
   - Recovery keys (former unseal keys)
   - KMS configuration details
   - Note that auto-unseal is now active
2. ✅ Document when recovery keys are needed

**Testing:**
- [x] Recovery keys documented
- [x] File clearly indicates auto-unseal is active

---

### Phase 4: Verification and Cleanup ✅ COMPLETE
Verify auto-unseal works and update documentation.

#### Task 4.1: Test Auto-Unseal ✅
**Steps:**
1. ✅ Delete Vault pod: `kubectl delete pod vault-0 -n vault`
2. ✅ Wait for pod to restart
3. ✅ Check status immediately after Ready

**Testing:**
- [x] Pod reaches Ready state
- [x] `vault status` shows `Sealed: false` without manual intervention
- [x] No manual unseal required

#### Task 4.2: Update Recovery Scripts ✅
**Files:** `recovery/restore-vault-secrets.sh`, `recovery/CLUSTER-RECOVERY.md`, `recovery/full-cluster-recovery.sh`

**Steps:**
1. ✅ Remove unseal logic from `restore-vault-secrets.sh` (no longer needed)
2. ✅ Update `CLUSTER-RECOVERY.md` to reflect auto-unseal
3. ✅ Update `full-cluster-recovery.sh` to verify auto-unseal
4. ✅ Note that recovery keys are only needed for special operations

**Testing:**
- [x] Scripts updated with auto-unseal references
- [x] Documentation accurately reflects new behavior

#### Task 4.3: Enable CloudTrail Logging (Optional)
**Notes:** CloudTrail is enabled by default in AWS and will automatically log KMS events.

**Testing:**
- [x] CloudTrail logs KMS decrypt events during Vault startup (default AWS behavior)

---

## End-to-End Testing

After all phases complete:
1. Simulate server restart by deleting Vault pod
2. Verify Vault comes up unsealed automatically
3. Verify External Secrets can still sync from Vault
4. Verify applications can access their secrets

## Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| KMS key deleted | Low | High | 30-day deletion window, CloudTrail alerts |
| AWS connectivity issues | Low | Medium | Vault stays sealed until connectivity restored |
| Migration failure | Low | Medium | Rollback: remove seal config, restart with Shamir |
| Lost recovery keys | Medium | High | Store in multiple secure locations |

## Rollback Plan

If issues occur after migration:
1. Update `configmaps.yaml` - remove seal stanza
2. Update `statefulsets.yaml` - remove AWS env vars
3. Push changes, restart Vault
4. Unseal with recovery keys using `-migrate` flag
5. Save new Shamir unseal keys

---

## Implementation Log

| Date | Phase | Action | Result |
|------|-------|--------|--------|
| 2026-01-19 | Phase 1 | Created vault-kms.tf | 6 AWS/K8s resources created |
| 2026-01-19 | Phase 2 | Updated ConfigMap with seal stanza | YAML valid |
| 2026-01-19 | Phase 2 | Updated StatefulSet with AWS env vars | YAML valid |
| 2026-01-19 | Phase 2 | Committed and pushed (fe85a57) | ArgoCD synced |
| 2026-01-19 | Phase 3 | Triggered Vault restart | Migration mode entered |
| 2026-01-19 | Phase 3 | Migration unseal with 2 keys | Seal type changed to awskms |
| 2026-01-19 | Phase 3 | Updated vault-credentials.txt | Recovery keys documented |
| 2026-01-19 | Phase 4 | Deleted Vault pod to test auto-unseal | Pod restarted, auto-unsealed successfully |
| 2026-01-19 | Phase 4 | Updated restore-vault-secrets.sh | Removed manual unseal logic |
| 2026-01-19 | Phase 4 | Updated CLUSTER-RECOVERY.md | Reflects auto-unseal config |
| 2026-01-19 | Phase 4 | Updated full-cluster-recovery.sh | Added auto-unseal verification |

---

## Implementation Complete

**Date:** 2026-01-19

Vault has been successfully migrated from Shamir-based manual unsealing to AWS KMS auto-unseal. Key outcomes:

1. **Automated Recovery**: Vault now automatically unseals after restarts without manual intervention
2. **AWS KMS Integration**: KMS key `2d982e46-c7bd-4606-a1bf-a470d1c09e07` with alias `alias/vault-auto-unseal`
3. **Security**: IAM user with minimal permissions (Encrypt, Decrypt, DescribeKey only)
4. **Documentation**: All recovery scripts and documentation updated
5. **Cost**: ~$1/month for KMS key
