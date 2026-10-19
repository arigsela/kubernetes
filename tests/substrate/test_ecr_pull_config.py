# tests/substrate/test_ecr_pull_config.py
"""atelet's image credential provider config (SPEC §T.129, §V.100).

atelet parses this config strictly at startup and dies on any error (cmd/atelet/main.go:213-216
@ v0.5.0-alpha2), on every node at once, which takes every agent down. These tests apply atelet's own
rules before Argo does. They also pin the two security properties the spike found
(docs/superpowers/specs/2026-10-18-substrate-ecr-pulls.md, Q2 and Q3).
"""
import posixpath
import re

from conftest import (CONTAINER_BIN_DIR, PLAYBOOK, SCRIPT, TERRAFORM, playbook_vars, provider_config,
                      provider_env, rendered, substrate_app, substrate_values, the_provider)

REGISTRY = "852893458518.dkr.ecr.us-east-2.amazonaws.com"
REPOSITORY = f"{REGISTRY}/docs-checker"

# The fields kubelet.config.k8s.io/v1 defines. atelet unmarshals strictly, so anything else is fatal.
CONFIG_FIELDS = {"apiVersion", "kind", "providers"}
PROVIDER_FIELDS = {"name", "matchImages", "defaultCacheDuration", "apiVersion", "args", "env", "tokenAttributes"}

# Go's time.ParseDuration, which metav1.Duration uses: "0", or one or more <number><unit>.
GO_DURATION = re.compile(r"[-+]?(0|((\d+(\.\d*)?|\.\d+)(ns|us|µs|μs|ms|s|m|h))+)")

# What the chart puts in atelet's own environment, and so in the provider's (exec.go:63-65 unions
# them; the config's env wins). Chart 0.5.0-alpha2, templates/atelet.yaml:186-197, when
# rustfs.enabled. The test pins the chart version below so a bump forces this list to be re-derived.
CHART_VERSION = "0.5.0-alpha2"
INHERITED_WITH_RUSTFS = {"AWS_REGION", "AWS_ENDPOINT_URL", "AWS_S3_USE_PATH_STYLE",
                         "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"}
# Variables that change which credentials the AWS SDK uses or where it sends the request. Any of them
# that atelet inherits must be overridden in the provider env (an empty value is "unset" to the SDK,
# aws-sdk-go-v2 config/env_config.go setStringFromEnvVal).
SDK_CREDENTIAL_OR_ENDPOINT = {"AWS_ACCESS_KEY_ID", "AWS_ACCESS_KEY", "AWS_SECRET_ACCESS_KEY", "AWS_SECRET_KEY",
                              "AWS_SESSION_TOKEN", "AWS_ENDPOINT_URL", "AWS_ENDPOINT_URL_ECR",
                              "AWS_PROFILE", "AWS_SHARED_CREDENTIALS_FILE", "AWS_CONFIG_FILE",
                              "AWS_WEB_IDENTITY_TOKEN_FILE", "AWS_ROLE_ARN",
                              "AWS_CONTAINER_CREDENTIALS_FULL_URI", "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI"}

# Host paths a worker pod or a sandbox can reach (workerpool_apply.go: /var/lib/ate read-write in every
# worker pod) or that other pods share (kubelet plugins with CSI drivers; /dev). The credentials file
# must not be under any of them.
SHARED_HOST_PATHS = ["/var/lib/ate", "/var/lib/kubelet", "/dev", "/proc", "/sys", "/run", "/tmp", "/var/tmp"]


def test_the_chart_version_is_the_one_this_model_was_derived_from():
    assert substrate_app()["spec"]["source"]["targetRevision"] == CHART_VERSION, (
        "Substrate chart bumped: re-read templates/atelet.yaml (its env and the credential-provider mounts) "
        "and cmd/atelet/internal/credentialprovider at the new tag, update INHERITED_WITH_RUSTFS and the "
        "rules here, then CHART_VERSION")


def test_only_the_inline_config_is_set_and_the_bin_dir_with_it():
    # chart templates/atelet.yaml:17-23 fail the render otherwise; the host-path variant would also take
    # the provider's env and matchImages out of git (spec, "Why …ConfigYAML").
    atelet = substrate_values()["atelet"]
    assert not atelet.get("imageCredentialProviderConfig"), "use imageCredentialProviderConfigYAML, not the host path"
    assert atelet.get("imageCredentialProviderConfigYAML")
    assert atelet.get("imageCredentialProviderBinDir")


def test_the_config_passes_atelets_strict_parse():
    cfg = provider_config()
    assert set(cfg) <= CONFIG_FIELDS, f"unknown top-level fields {set(cfg) - CONFIG_FIELDS} are fatal to atelet"
    assert cfg["apiVersion"] == "kubelet.config.k8s.io/v1"
    assert cfg["kind"] == "CredentialProviderConfig"
    assert isinstance(cfg["providers"], list) and cfg["providers"]
    for p in cfg["providers"]:
        assert set(p) <= PROVIDER_FIELDS, f"unknown provider fields {set(p) - PROVIDER_FIELDS} are fatal to atelet"
        for e in p.get("env", []):
            # ExecEnvVar is {name, value}, both strings: valueFrom or a non-string value fails the strict parse.
            assert set(e) == {"name", "value"}, f"env entry {e}: only name and value (literals)"
            assert isinstance(e["name"], str) and isinstance(e["value"], str), f"env entry {e}: both must be strings"
        for a in p.get("args", []):
            assert isinstance(a, str)


def test_every_provider_passes_atelets_per_provider_checks():
    # config.go:91-138 @ v0.5.0-alpha2. A provider failing these is skipped, so its pulls go anonymous.
    for p in provider_config()["providers"]:
        assert p["name"] and "/" not in p["name"], "name is a bare file name in the bin dir"
        assert p["matchImages"], "matchImages is required"
        assert p["apiVersion"] == "credentialprovider.kubelet.k8s.io/v1"
        assert "tokenAttributes" not in p, "atelet refuses tokenAttributes (actors have no service account)"
        d = p["defaultCacheDuration"]
        # A YAML number is not a metav1.Duration (it unmarshals from a JSON string): fatal, not skipped.
        assert isinstance(d, str) and GO_DURATION.fullmatch(d), f"defaultCacheDuration {d!r}: a quoted Go duration"
        assert not d.startswith("-")


def test_the_provider_is_scoped_to_the_docs_checker_repository():
    p = the_provider()
    # match.go: hosts compared per segment, path by string prefix. The IAM policy is what limits the
    # token to this repository; matchImages limits when the binary runs at all.
    assert p["matchImages"] == [REPOSITORY]
    assert p["name"] == "ecr-credential-provider"


def test_every_inherited_credential_or_endpoint_variable_is_overridden():
    """§V.100 / spec Q2: the provider inherits atelet's env, where rustfs's AWS keys and endpoint would
    otherwise beat the credentials file and redirect the ECR call to rustfs."""
    values = substrate_values()
    inherited = set()
    if values.get("rustfs", {}).get("enabled", True):  # the chart default is enabled
        inherited |= INHERITED_WITH_RUSTFS
    inherited |= {e["name"] for e in values["atelet"].get("extraEnv", []) if e["name"].startswith("AWS_")}
    env = provider_env()
    for name in sorted(inherited & SDK_CREDENTIAL_OR_ENDPOINT):
        assert name in env, f"{name} is inherited from atelet and must be overridden in the provider env"
    # Blank, not re-pointed: the keys come only from the shared credentials file.
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_ENDPOINT_URL"):
        assert env.get(name) == "", f"{name} must be set to the empty string (unset to the SDK)"
    assert env.get("AWS_IGNORE_CONFIGURED_ENDPOINT_URLS") == "true"
    assert env.get("AWS_EC2_METADATA_DISABLED") == "true", "not EC2: fail fast instead of probing IMDS"


def test_the_keys_come_from_the_file_the_playbook_writes():
    env, pv = provider_env(), playbook_vars()
    assert env["AWS_SHARED_CREDENTIALS_FILE"] == posixpath.join(CONTAINER_BIN_DIR, pv["ate_cp_credentials_file"])
    assert env["AWS_PROFILE"] == pv["ate_cp_profile"] == "docs-checker-pull"
    assert pv["ate_cp_binary"] == the_provider()["name"]
    assert pv["ate_cp_registry"] == REGISTRY
    assert rendered(pv["ate_cp_verify_image"]) == REPOSITORY


def test_the_playbooks_verify_runs_the_provider_with_the_configs_environment():
    """The per-worker verify is only evidence if it runs the provider the way atelet will. The one difference
    is where the bin dir is: on the host for the playbook, at /run/image-credential-provider/bin for atelet."""
    pv = playbook_vars()
    playbook_env = rendered(pv["ate_cp_env"])
    config_env = provider_env()
    assert set(playbook_env) == set(config_env)
    host_file = posixpath.join(pv["ate_cp_dir"], pv["ate_cp_credentials_file"])
    assert playbook_env.pop("AWS_SHARED_CREDENTIALS_FILE") == host_file
    config_env.pop("AWS_SHARED_CREDENTIALS_FILE")
    assert playbook_env == config_env


def test_the_bin_dir_is_the_playbooks_and_outside_every_shared_host_path():
    """§V.100 / spec Q3: worker pods mount /var/lib/ate read-write, so a credentials file under it would be
    reachable by the host side of every gVisor sandbox."""
    bin_dir = substrate_values()["atelet"]["imageCredentialProviderBinDir"]
    assert bin_dir == playbook_vars()["ate_cp_dir"]
    assert posixpath.isabs(bin_dir) and posixpath.normpath(bin_dir) == bin_dir
    for shared in SHARED_HOST_PATHS:
        assert bin_dir != shared and not bin_dir.startswith(shared + "/"), f"{bin_dir} is under {shared}"


def test_the_playbook_pins_the_binary_by_version_and_checksum():
    pv = playbook_vars()
    assert re.fullmatch(r"v\d+\.\d+\.\d+", pv["ate_cp_version"])
    assert re.fullmatch(r"[0-9a-f]{64}", pv["ate_cp_sha256"])
    assert pv["ate_cp_url"] == ("https://artifacts.k8s.io/binaries/cloud-provider-aws/{{ ate_cp_version }}"
                                "/linux/amd64/ecr-credential-provider-linux-amd64")


def test_the_iam_user_can_only_pull_the_docs_checker_repository():
    tf = TERRAFORM.read_text()
    assert re.search(r'resource "aws_iam_user" "docs_checker_pull" \{[^}]*name\s*=\s*"docs-checker-pull"', tf)
    assert re.search(r'resource "aws_iam_user" "docs_checker_pull" \{[^}]*path\s*=\s*"/system/"', tf), \
        "Atlantis may only manage IAM users under /system/ (atlantis-iam.tf)"
    actions = re.findall(r'"(ecr:[A-Za-z*]+)"', tf)
    assert sorted(actions) == ["ecr:BatchGetImage", "ecr:GetAuthorizationToken", "ecr:GetDownloadUrlForLayer"]
    assert 'aws_ecr_repository.this["docs-checker"].arn' in tf
    # GetAuthorizationToken has no resource-level permissions; it must be alone in the "*" statement.
    star = re.search(r'Action\s*=\s*\[([^\]]*)\]\s*Resource\s*=\s*"\*"', tf)
    assert star and re.findall(r'"(ecr:[A-Za-z]+)"', star.group(1)) == ["ecr:GetAuthorizationToken"]
    # The key is made by the provisioning script, never by Terraform (it would be in the state bucket).
    assert not re.search(r'resource "aws_iam_access_key"', tf)


def test_the_script_and_terraform_name_the_same_user_and_vault_key():
    script = SCRIPT.read_text()
    assert 'IAM_USER=docs-checker-pull' in script
    assert 'KEY=docs-checker-pull' in script and 'MOUNT=k8s-secrets' in script
    # ecr-auth's property names (base-apps/ecr-auth/external-secret.yaml)
    for prop in ("aws-access-key-id", "aws-secret-access-key", "aws-region"):
        assert prop in script
    assert "playbooks/ate-image-credentials.yml" in script
    playbook = PLAYBOOK.read_text()
    for var in ("DOCS_CHECKER_PULL_AWS_ACCESS_KEY_ID", "DOCS_CHECKER_PULL_AWS_SECRET_ACCESS_KEY"):
        assert var in script and var in playbook
