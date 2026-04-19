package terraform

import rego.v1

resource_changes := input.resource_changes

# -----------------------------------------------------------------------------
# DENY rules — these block apply when --no-fail is removed
# -----------------------------------------------------------------------------

deny contains msg if {
	some rc in resource_changes
	rc.type == "aws_iam_user"
	rc.change.actions[_] in ["create", "update"]
	path := object.get(rc.change.after, "path", "/")
	not startswith(path, "/system/")
	msg := sprintf("%s: IAM users must be under /system/ path (got %q)", [rc.address, path])
}

deny contains msg if {
	some rc in resource_changes
	rc.type == "aws_kms_key"
	rc.change.actions[_] in ["create", "update"]
	rc.change.after.enable_key_rotation == false
	msg := sprintf("%s: KMS keys must have key rotation enabled", [rc.address])
}

deny contains msg if {
	some rc in resource_changes
	rc.type == "aws_s3_bucket_public_access_block"
	rc.change.actions[_] in ["create", "update"]
	rc.change.after.block_public_acls == false
	msg := sprintf("%s: S3 public access blocks must not allow public ACLs", [rc.address])
}

deny contains msg if {
	some rc in resource_changes
	rc.type == "aws_security_group_rule"
	rc.change.actions[_] in ["create", "update"]
	rc.change.after.type == "ingress"
	cidrs := object.get(rc.change.after, "cidr_blocks", [])
	some cidr in cidrs
	cidr == "0.0.0.0/0"
	msg := sprintf("%s: security group ingress must not allow 0.0.0.0/0", [rc.address])
}

# -----------------------------------------------------------------------------
# WARN rules — visibility only, never block apply
# -----------------------------------------------------------------------------

warn contains msg if {
	some rc in resource_changes
	rc.type == "aws_iam_user_policy"
	rc.change.actions[_] in ["create", "update"]
	policy_json := rc.change.after.policy
	policy := json.unmarshal(policy_json)
	some stmt in policy.Statement
	stmt.Effect == "Allow"
	stmt.Resource == "*"
	some action in stmt.Action
	action in ["iam:*", "s3:*", "sts:*"]
	msg := sprintf("%s: IAM policy grants %q on all resources — consider scoping", [rc.address, action])
}

warn contains msg if {
	some rc in resource_changes
	rc.change.actions[_] in ["create", "update"]
	tags_field := object.get(rc.change.after, "tags", null)
	tags_field != null
	not object.get(tags_field, "ManagedBy", false)
	msg := sprintf("%s: resource is missing a ManagedBy tag", [rc.address])
}
