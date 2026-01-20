# Vault Auto-Unseal Implementation Plan

## Executive Summary

This plan outlines the migration from Shamir-based manual unsealing to AWS KMS auto-unseal for HashiCorp Vault running on the k3s cluster. This eliminates the need for manual intervention after server restarts.

## Current State

- **Seal Type**: Shamir (default)
- **Threshold**: 2 of 3 keys required
- **Storage**: File-based (`/vault/data`)
- **Version**: 1.18.1
- **Problem**: Requires manual unsealing after every restart

## Proposed Solution: AWS KMS Auto-Unseal

### Why AWS KMS?

| Option | Pros | Cons |
|--------|------|------|
| **AWS KMS** | Simple setup, managed service, low cost, integrates with existing AWS account | Requires AWS connectivity |
| Transit (another Vault) | No cloud dependency | Need second Vault instance |
| Azure Key Vault | Managed service | Not using Azure |
| GCP Cloud KMS | Managed service | Not using GCP |

**Recommendation**: AWS KMS - best fit given existing AWS infrastructure (ECR, RDS, S3)

---

## Cost Analysis

### AWS KMS Costs (us-east-2 region)

| Item | Cost | Notes |
|------|------|-------|
| **KMS Key (Monthly)** | $1.00/month | Single symmetric key |
| **Key Rotation** | $1.00/month | After first rotation |
| **API Requests** | ~$0.00/month | First 20,000 requests/month FREE |
| | | $0.03 per 10,000 requests after |

### Estimated Monthly Cost

```
Base Key:                    $1.00
API Requests (est. 5,000):   $0.00 (within free tier)
────────────────────────────────────
Total Monthly:               ~$1.00
Total Yearly:               ~$12.00
```

**Vault unseals ~1-5 times per month**, generating minimal API calls. Each unseal = 1-2 KMS decrypt operations.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        k3s Cluster                          │
│  ┌─────────────────────────────────────────────────────┐   │
│  │                   Vault Pod                          │   │
│  │  ┌─────────────┐    ┌─────────────────────────────┐ │   │
│  │  │   Vault     │───▶│  AWS KMS (us-east-2)        │ │   │
│  │  │   Server    │    │  ┌───────────────────────┐  │ │   │
│  │  │             │    │  │ vault-auto-unseal-key │  │ │   │
│  │  │  seal {     │    │  │   (Symmetric CMK)     │  │ │   │
│  │  │   awskms    │    │  └───────────────────────┘  │ │   │
│  │  │  }          │    │                             │ │   │
│  │  └─────────────┘    └─────────────────────────────┘ │   │
│  │         │                                            │   │
│  │         ▼                                            │   │
│  │  ┌─────────────┐                                    │   │
│  │  │  File       │                                    │   │
│  │  │  Storage    │                                    │   │
│  │  │  (PVC)      │                                    │   │
│  │  └─────────────┘                                    │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

---

## Implementation Phases

### Phase 1: AWS Infrastructure Setup (Terraform)

Create KMS key and IAM resources:

1. **KMS Key** - Symmetric key for auto-unseal
2. **IAM Policy** - Minimal permissions for Vault
3. **IAM User/Role** - Credentials for Vault pod

**Files to create/modify:**
- `terraform/modules/vault-kms/main.tf` (new module)
- `terraform/modules/vault-kms/variables.tf`
- `terraform/modules/vault-kms/outputs.tf`
- `terraform/roots/asela-cluster/main.tf` (add module)

### Phase 2: Kubernetes Resources

1. **Secret** - Store AWS credentials (or use IRSA if available)
2. **ConfigMap** - Update Vault configuration with seal stanza
3. **StatefulSet** - Add environment variables for AWS credentials

**Files to modify:**
- `base-apps/vault/configmaps.yaml`
- `base-apps/vault/statefulsets.yaml`
- `base-apps/vault/secrets.yaml` (new - for AWS creds via External Secrets)

### Phase 3: Migration

1. Stop Vault
2. Update configuration
3. Start Vault with migration flag
4. Unseal with existing Shamir keys (one last time)
5. Vault converts to KMS-based sealing
6. Generate new recovery keys
7. Verify auto-unseal works

---

## Detailed Implementation Steps

### Step 1: Create Terraform Module for KMS

```hcl
# terraform/modules/vault-kms/main.tf

resource "aws_kms_key" "vault_auto_unseal" {
  description              = "Vault Auto Unseal Key"
  key_usage               = "ENCRYPT_DECRYPT"
  customer_master_key_spec = "SYMMETRIC_DEFAULT"
  is_enabled              = true
  enable_key_rotation     = true
  deletion_window_in_days = 30

  tags = {
    Name        = "vault-auto-unseal"
    Environment = "production"
    ManagedBy   = "terraform"
  }
}

resource "aws_kms_alias" "vault_auto_unseal" {
  name          = "alias/vault-auto-unseal"
  target_key_id = aws_kms_key.vault_auto_unseal.key_id
}

# IAM Policy for Vault
resource "aws_iam_policy" "vault_kms" {
  name        = "vault-kms-unseal"
  description = "Allow Vault to use KMS for auto-unseal"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "VaultKMSUnseal"
        Effect = "Allow"
        Action = [
          "kms:Encrypt",
          "kms:Decrypt",
          "kms:DescribeKey"
        ]
        Resource = aws_kms_key.vault_auto_unseal.arn
      }
    ]
  })
}

# IAM User for Vault (alternative: use IRSA with EKS)
resource "aws_iam_user" "vault" {
  name = "vault-kms-user"

  tags = {
    Name      = "vault-kms-user"
    ManagedBy = "terraform"
  }
}

resource "aws_iam_user_policy_attachment" "vault_kms" {
  user       = aws_iam_user.vault.name
  policy_arn = aws_iam_policy.vault_kms.arn
}

resource "aws_iam_access_key" "vault" {
  user = aws_iam_user.vault.name
}
```

### Step 2: Update Vault ConfigMap

```yaml
# base-apps/vault/configmaps.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: vault-config
  namespace: vault
data:
  vault.json: |
    {
      "ui": true,
      "disable_mlock": true,
      "listener": {
        "tcp": {
          "address": "0.0.0.0:8200",
          "tls_disable": 1
        }
      },
      "storage": {
        "file": {
          "path": "/vault/data"
        }
      },
      "seal": {
        "awskms": {
          "region": "us-east-2",
          "kms_key_id": "${KMS_KEY_ID}"
        }
      }
    }
```

### Step 3: Update StatefulSet with AWS Credentials

Add environment variables to the Vault container:
```yaml
env:
  - name: AWS_ACCESS_KEY_ID
    valueFrom:
      secretKeyRef:
        name: vault-kms-credentials
        key: aws-access-key-id
  - name: AWS_SECRET_ACCESS_KEY
    valueFrom:
      secretKeyRef:
        name: vault-kms-credentials
        key: aws-secret-access-key
  - name: AWS_REGION
    value: "us-east-2"
```

### Step 4: Create External Secret for AWS Credentials

```yaml
# base-apps/vault/external-secret.yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: vault-kms-credentials
  namespace: vault
spec:
  refreshInterval: 1h
  secretStoreRef:
    kind: SecretStore
    name: vault-backend
  target:
    name: vault-kms-credentials
    creationPolicy: Owner
  data:
    - secretKey: aws-access-key-id
      remoteRef:
        key: vault-kms
        property: aws-access-key-id
    - secretKey: aws-secret-access-key
      remoteRef:
        key: vault-kms
        property: aws-secret-access-key
```

### Step 5: Migration Procedure

```bash
# 1. Store current Vault data (backup)
kubectl exec -n vault vault-0 -- vault operator raft snapshot save /tmp/vault-backup.snap
kubectl cp vault/vault-0:/tmp/vault-backup.snap ./vault-backup.snap

# 2. Apply Terraform to create KMS resources
cd terraform/roots/asela-cluster
terraform apply -target=module.vault_kms

# 3. Store KMS credentials in Vault (chicken-egg: use k8s secret initially)
# Create temporary secret with AWS creds from Terraform output

# 4. Apply updated Vault configuration
git add base-apps/vault/
git commit -m "Configure Vault for AWS KMS auto-unseal"
git push

# 5. Delete Vault pod to pick up new config
kubectl delete pod vault-0 -n vault

# 6. Watch logs - should show migration mode
kubectl logs -f vault-0 -n vault

# 7. Perform migration unseal (one last time with Shamir keys)
kubectl exec -n vault vault-0 -- vault operator unseal -migrate <KEY1>
kubectl exec -n vault vault-0 -- vault operator unseal -migrate <KEY2>

# 8. Verify auto-unseal
kubectl exec -n vault vault-0 -- vault status
# Should show: Seal Type: awskms, Sealed: false

# 9. Save recovery keys (generated during migration)
# These replace Shamir keys for root token regeneration
```

---

## Post-Migration

### New Recovery Keys

After migration, Vault generates **recovery keys** instead of unseal keys. Store these securely - they're needed for:
- Root token regeneration
- Migrating to a different seal type
- Emergency recovery

### Testing Auto-Unseal

```bash
# Delete the Vault pod
kubectl delete pod vault-0 -n vault

# Wait for pod to restart
kubectl wait --for=condition=Ready pod/vault-0 -n vault --timeout=120s

# Verify it auto-unsealed
kubectl exec -n vault vault-0 -- vault status
# Should show Sealed: false without manual intervention
```

### Update Recovery Scripts

Update `recovery/restore-vault-secrets.sh` to remove the unseal step since it's now automatic.

---

## Rollback Plan

If issues occur:

1. **Stop Vault**
2. **Remove seal stanza** from config
3. **Add `-migrate` flag** to unseal command
4. **Unseal with recovery keys** to migrate back to Shamir
5. **Save new Shamir keys**

---

## Security Considerations

1. **KMS Key Policy**: Restrict to only Vault IAM user
2. **CloudTrail**: Enable logging for KMS key usage
3. **Key Rotation**: Enabled by default (annual)
4. **IAM Credentials**:
   - Rotate periodically
   - Consider IRSA if migrating to EKS
5. **Recovery Keys**: Store as securely as unseal keys

---

## Timeline

| Phase | Duration | Description |
|-------|----------|-------------|
| 1 | 30 min | Create Terraform module |
| 2 | 20 min | Update Kubernetes manifests |
| 3 | 30 min | Migration procedure |
| 4 | 15 min | Testing and verification |
| **Total** | **~2 hours** | |

---

## Checklist

- [ ] Create Terraform module for KMS
- [ ] Apply Terraform to create AWS resources
- [ ] Store AWS credentials in Vault
- [ ] Update Vault ConfigMap with seal stanza
- [ ] Update StatefulSet with AWS env vars
- [ ] Create External Secret for credentials
- [ ] Backup current Vault data
- [ ] Perform migration unseal
- [ ] Save new recovery keys
- [ ] Test auto-unseal by restarting pod
- [ ] Update recovery documentation
- [ ] Enable CloudTrail for KMS auditing

---

## References

- [HashiCorp: Auto-unseal Vault using AWS KMS](https://developer.hashicorp.com/vault/tutorials/auto-unseal/autounseal-aws-kms)
- [AWS KMS Seal Configuration](https://developer.hashicorp.com/vault/docs/configuration/seal/awskms)
- [Sealing Best Practices](https://developer.hashicorp.com/vault/docs/configuration/seal/seal-best-practices)
- [AWS KMS Pricing](https://aws.amazon.com/kms/pricing/)
- [Migration Guide](https://medium.com/@mpoore/how-to-migrate-vault-auto-unseal-to-aws-kms-a4d3c1170124)
