# WAN-IP Monitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Survive an ISP WAN-address rotation without a manual outage: reconcile Route 53 automatically and open a ready-to-merge PR for the Istio allow-list.

**Architecture:** A CronJob in a dedicated `wan-ip-monitor` namespace runs every 5 minutes. It is a **reconciler, not a change-detector** — each run independently compares the observed WAN address against two desired states (Route 53 records, and the allow-list on `main`) and fixes whichever drifted. Route 53 is corrected directly because it is not in git. The allow-list is corrected by opening a PR, because it is the security boundary and the `main` ruleset forbids direct pushes. An `arigsela.com/wan-ip` annotation on the AuthorizationPolicy is the source of truth for *which* of its four `/32`s is home.

**Tech Stack:** Python 3 (stdlib + PyYAML), AWS CLI via subprocess, GitHub REST API via `urllib`, Kubernetes CronJob, External Secrets + Vault, pytest.

## Global Constraints

- Annotation key is exactly `arigsela.com/wan-ip` — used by both the policy file and the script.
- The reconciler MUST only ever modify Route 53 A records whose sole value equals the **previous** WAN IP. Never records pointing anywhere else.
- The reconciler MUST NOT be able to merge its own PR. It has no bypass on the `main` ruleset and must not be given one.
- Detected addresses that are private, loopback, reserved, multicast, link-local, or unspecified MUST be rejected without any write.
- Route 53 hosted zone ID: `Z0524483LR4JCFNLS7N0` (zone `arigsela.com`).
- Repo is `arigsela/kubernetes`; allow-list path is `base-apps/istio-ingress/authorizationpolicy.yaml`.
- n8n webhook base (in-cluster, no TLS, no auth): `http://n8n.n8n.svc.cluster.local:5678`.
- pytest version used across this repo's CI: `pytest==8.3.3`.
- The script is stored **only** in the ConfigMap (`base-apps/wan-ip-monitor/configmap-reconcile.yaml`, key `reconcile.py`). Tests load it from there. Do not create a second copy under `scripts/`.

---

## Task 0: Prerequisite — Vault setup (HUMAN, not automatable)

The Vault MCP available to this agent exposes KV and PKI only; it cannot create auth roles or ACL policies. The operator must run these once before Task 5 can work at runtime. Tasks 1–4 do not depend on it.

```bash
vault policy write wan-ip-monitor - <<'EOF'
path "k8s-secrets/data/wan-ip-monitor" {
  capabilities = ["read"]
}
EOF

vault write auth/kubernetes/role/wan-ip-monitor \
  bound_service_account_names=default \
  bound_service_account_namespaces=wan-ip-monitor \
  policies=wan-ip-monitor \
  ttl=24h

vault kv put k8s-secrets/wan-ip-monitor \
  aws-access-key-id='<route53-scoped key>' \
  aws-secret-access-key='<secret>' \
  github-token='<PAT with contents:write + pull_requests:write on arigsela/kubernetes>'
```

The AWS key should be scoped to `route53:ListResourceRecordSets` and `route53:ChangeResourceRecordSets` on zone `Z0524483LR4JCFNLS7N0` only. The GitHub token needs **no** admin or workflow scope — it only reads a file, creates a branch, and opens a PR.

---

## Task 1: Annotate the AuthorizationPolicy and guard it in CI

Establishes the source of truth the reconciler depends on. Ships independently: no behavior change, and the guard test catches the annotation drifting from the rules by hand.

**Files:**
- Modify: `base-apps/istio-ingress/authorizationpolicy.yaml` (add annotation to `metadata`)
- Create: `tests/wan_ip/__init__.py`
- Create: `tests/wan_ip/test_policy_annotation.py`
- Modify: `.github/workflows/validate.yaml` (add `wan-ip-validate` job)

**Interfaces:**
- Consumes: nothing.
- Produces: the annotation `arigsela.com/wan-ip` on `AuthorizationPolicy/gateway-allow`, relied on by Tasks 2–4.

- [ ] **Step 1: Write the failing test**

Create `tests/wan_ip/__init__.py` as an empty file, then create `tests/wan_ip/test_policy_annotation.py`:

```python
"""The wan-ip annotation must stay consistent with the rules it describes.

The reconciler replaces exactly the IP named in the annotation. If someone
edits the ipBlocks by hand without moving the annotation, the reconciler would
later rewrite the wrong /32 - or silently no-op. This test makes that
divergence a CI failure instead of a 3am surprise.
"""
import pathlib

import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]
POLICY = REPO / "base-apps" / "istio-ingress" / "authorizationpolicy.yaml"
ANNOTATION = "arigsela.com/wan-ip"


def _policy():
    return yaml.safe_load(POLICY.read_text())


def test_annotation_present():
    ann = (_policy()["metadata"].get("annotations") or {})
    assert ANNOTATION in ann, f"{ANNOTATION} annotation missing from gateway-allow"


def test_annotation_value_appears_in_every_restricted_rule():
    doc = _policy()
    wan = doc["metadata"]["annotations"][ANNOTATION]
    restricted = [r for r in doc["spec"]["rules"] if "from" in r]
    assert restricted, "expected at least one restricted rule"
    for rule in restricted:
        blocks = rule["from"][0]["source"]["ipBlocks"]
        host = rule["to"][0]["operation"]["hosts"][0]
        assert f"{wan}/32" in blocks, f"{host} does not allow the declared WAN IP {wan}"


def test_no_other_ip_looks_like_a_stale_wan_entry():
    """Every restricted rule should carry the same declared WAN /32 exactly once."""
    doc = _policy()
    wan = f"{doc['metadata']['annotations'][ANNOTATION]}/32"
    for rule in [r for r in doc["spec"]["rules"] if "from" in r]:
        blocks = rule["from"][0]["source"]["ipBlocks"]
        assert blocks.count(wan) == 1, f"expected exactly one {wan}, got {blocks}"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pip install pytest==8.3.3 pyyaml
python -m pytest tests/wan_ip/ -q
```

Expected: FAIL — `test_annotation_present` asserts with "arigsela.com/wan-ip annotation missing from gateway-allow".

- [ ] **Step 3: Add the annotation**

In `base-apps/istio-ingress/authorizationpolicy.yaml`, change the `metadata` block from:

```yaml
metadata:
  name: gateway-allow
  namespace: istio-ingress
```

to:

```yaml
metadata:
  name: gateway-allow
  namespace: istio-ingress
  annotations:
    # SOURCE OF TRUTH for which of the four /32s below is the home WAN address.
    #
    # This file allow-lists four addresses per rule; only this one rotates when
    # the ISP reassigns it. base-apps/wan-ip-monitor replaces exactly the string
    # named here and moves this annotation in the same commit, so it can never
    # guess wrong and clobber one of the three remote addresses.
    #
    # Keep it consistent with the ipBlocks by hand too - tests/wan_ip/
    # test_policy_annotation.py fails CI if they diverge.
    arigsela.com/wan-ip: "76.97.4.210"
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/wan_ip/ -q
```

Expected: PASS (3 passed).

- [ ] **Step 5: Wire the tests into CI**

In `.github/workflows/validate.yaml`, add this job (match the existing `techdocs-validate` shape, two-space indentation under `jobs:`):

```yaml
  wan-ip-validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install deps
        run: pip install pytest==8.3.3 pyyaml==6.0.2
      - name: Run wan-ip-monitor tests
        run: python -m pytest tests/wan_ip/ -q
```

- [ ] **Step 6: Commit**

```bash
git add base-apps/istio-ingress/authorizationpolicy.yaml tests/wan_ip/ .github/workflows/validate.yaml
git commit -m "istio-ingress: declare the home WAN IP in an annotation, guarded by CI"
```

---

## Task 2: Pure logic — IP validation and policy rewriting

The reconciler's decision-making, with no network or AWS involved. This is the part that must never be wrong, so it is the part that is tested hardest.

**Files:**
- Create: `base-apps/wan-ip-monitor/configmap-reconcile.yaml`
- Create: `tests/wan_ip/conftest.py`
- Create: `tests/wan_ip/test_logic.py`

**Interfaces:**
- Consumes: the `arigsela.com/wan-ip` annotation from Task 1.
- Produces, all importable from the `reconcile` module fixture:
  - `WAN_IP_ANNOTATION: str`
  - `is_valid_public_ipv4(value: str) -> bool`
  - `read_declared_wan_ip(policy_yaml: str) -> str`
  - `rewrite_policy(policy_yaml: str, old_ip: str, new_ip: str) -> str`

- [ ] **Step 1: Write the test loader**

Create `tests/wan_ip/conftest.py`:

```python
"""Load reconcile.py out of the ConfigMap that ships it.

The script has exactly one home - the ConfigMap - so there is no second copy to
drift. Tests extract it and import it as a module.
"""
import importlib.util
import pathlib
import tempfile

import pytest
import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]
CONFIGMAP = REPO / "base-apps" / "wan-ip-monitor" / "configmap-reconcile.yaml"


@pytest.fixture(scope="session")
def reconcile():
    source = yaml.safe_load(CONFIGMAP.read_text())["data"]["reconcile.py"]
    path = pathlib.Path(tempfile.mkdtemp()) / "reconcile.py"
    path.write_text(source)
    spec = importlib.util.spec_from_file_location("reconcile", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
```

- [ ] **Step 2: Write the failing tests**

Create `tests/wan_ip/test_logic.py`:

```python
import pytest

POLICY = """\
apiVersion: security.istio.io/v1
kind: AuthorizationPolicy
metadata:
  name: gateway-allow
  namespace: istio-ingress
  annotations:
    arigsela.com/wan-ip: "76.97.4.210"
spec:
  rules:
    - to:
        - operation:
            hosts:
              - argocd.arigsela.com
      from:
        - source:
            ipBlocks:
              - 76.97.4.210/32
              - 170.85.56.189/32
              - 104.28.177.82/32
"""


@pytest.mark.parametrize("value", ["76.97.4.210", "8.8.8.8", " 1.1.1.1 "])
def test_accepts_public_addresses(reconcile, value):
    assert reconcile.is_valid_public_ipv4(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "10.0.1.182",        # private
        "192.168.1.1",       # private
        "127.0.0.1",         # loopback
        "169.254.1.1",       # link-local
        "224.0.0.1",         # multicast
        "0.0.0.0",           # unspecified
        "",                  # empty
        "not-an-ip",         # junk
        "<html>error</html>",  # captive portal
        "76.97.4.210\n76.97.4.211",  # two values
        "2606:4700::1111",   # IPv6
    ],
)
def test_rejects_everything_else(reconcile, value):
    assert reconcile.is_valid_public_ipv4(value) is False


def test_reads_declared_wan_ip(reconcile):
    assert reconcile.read_declared_wan_ip(POLICY) == "76.97.4.210"


def test_missing_annotation_raises(reconcile):
    with pytest.raises(KeyError):
        reconcile.read_declared_wan_ip("metadata:\n  name: x\nspec: {}\n")


def test_rewrite_moves_annotation_and_rule(reconcile):
    out = reconcile.rewrite_policy(POLICY, "76.97.4.210", "203.0.113.9")
    assert 'arigsela.com/wan-ip: "203.0.113.9"' in out
    assert "- 203.0.113.9/32" in out
    assert "76.97.4.210" not in out


def test_rewrite_leaves_the_other_addresses_alone(reconcile):
    out = reconcile.rewrite_policy(POLICY, "76.97.4.210", "203.0.113.9")
    assert "- 170.85.56.189/32" in out
    assert "- 104.28.177.82/32" in out


def test_rewrite_refuses_a_bad_new_address(reconcile):
    with pytest.raises(ValueError):
        reconcile.rewrite_policy(POLICY, "76.97.4.210", "10.0.0.5")


def test_rewrite_is_a_noop_when_already_current(reconcile):
    assert reconcile.rewrite_policy(POLICY, "76.97.4.210", "76.97.4.210") == POLICY
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
python -m pytest tests/wan_ip/test_logic.py -q
```

Expected: FAIL — the ConfigMap does not exist, so `conftest.py` raises `FileNotFoundError`.

- [ ] **Step 4: Create the ConfigMap with the pure logic**

Create `base-apps/wan-ip-monitor/configmap-reconcile.yaml`:

```yaml
# The reconciler script. This ConfigMap is its ONLY home - tests/wan_ip/
# load reconcile.py out of this file rather than a copy, so the two cannot
# drift apart.
apiVersion: v1
kind: ConfigMap
metadata:
  name: wan-ip-reconcile
  namespace: wan-ip-monitor
data:
  reconcile.py: |
    """Reconcile the home WAN address into Route 53 and the Istio allow-list.

    This is a RECONCILER, not a change-detector. Every run compares reality
    against two desired states independently and fixes whichever drifted. That
    matters: a change-detector that updated Route 53 first would see "no change"
    on the next run and never re-open an allow-list PR that went unmerged.
    """
    import base64
    import ipaddress
    import json
    import os
    import subprocess
    import urllib.error
    import urllib.request

    import yaml

    WAN_IP_ANNOTATION = "arigsela.com/wan-ip"


    def is_valid_public_ipv4(value):
        """True only for a single, routable, public IPv4 address.

        Deliberately strict. This value ends up in a security allow-list and in
        public DNS, so an error page, a captive-portal redirect, or an RFC1918
        address from a misbehaving detector must never get through.
        """
        try:
            addr = ipaddress.IPv4Address(str(value).strip())
        except (ipaddress.AddressValueError, ValueError):
            return False
        return not (
            addr.is_private
            or addr.is_loopback
            or addr.is_reserved
            or addr.is_multicast
            or addr.is_link_local
            or addr.is_unspecified
        )


    def read_declared_wan_ip(policy_yaml):
        """The WAN address the allow-list currently claims to be built around."""
        doc = yaml.safe_load(policy_yaml)
        annotations = (doc.get("metadata") or {}).get("annotations") or {}
        value = annotations.get(WAN_IP_ANNOTATION)
        if not value:
            raise KeyError("%s annotation missing from the policy" % WAN_IP_ANNOTATION)
        return value


    def rewrite_policy(policy_yaml, old_ip, new_ip):
        """Swap exactly the declared WAN /32, and move the annotation with it.

        Text substitution rather than a YAML round-trip on purpose: this file is
        heavily commented and the comments are load-bearing documentation.
        Re-serialising through PyYAML would strip every one of them.
        """
        if not is_valid_public_ipv4(new_ip):
            raise ValueError("refusing to write non-public address %r" % new_ip)
        if old_ip == new_ip:
            return policy_yaml
        out = policy_yaml.replace("- %s/32" % old_ip, "- %s/32" % new_ip)
        out = out.replace(
            '%s: "%s"' % (WAN_IP_ANNOTATION, old_ip),
            '%s: "%s"' % (WAN_IP_ANNOTATION, new_ip),
        )
        return out
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/wan_ip/ -q
```

Expected: PASS (all tests in `test_logic.py` plus Task 1's three).

- [ ] **Step 6: Commit**

```bash
git add base-apps/wan-ip-monitor/configmap-reconcile.yaml tests/wan_ip/
git commit -m "wan-ip-monitor: add IP validation and allow-list rewriting"
```

---

## Task 3: Route 53 reconciliation

The automatic half. Decides which records to touch and builds the change batch; the AWS call itself is a thin wrapper so the decision logic stays testable.

**Files:**
- Modify: `base-apps/wan-ip-monitor/configmap-reconcile.yaml` (append functions to `reconcile.py`)
- Create: `tests/wan_ip/test_route53.py`

**Interfaces:**
- Consumes: `is_valid_public_ipv4` from Task 2.
- Produces:
  - `records_needing_update(recordsets: list[dict], old_ip: str) -> list[dict]`
  - `build_change_batch(records: list[dict], new_ip: str) -> dict`
  - `aws_json(args: list[str]) -> dict` (thin subprocess wrapper)

- [ ] **Step 1: Write the failing tests**

Create `tests/wan_ip/test_route53.py`:

```python
RECORDSETS = [
    {"Name": "argocd.arigsela.com.", "Type": "A", "TTL": 300,
     "ResourceRecords": [{"Value": "76.97.4.210"}]},
    {"Name": "grafana.arigsela.com.", "Type": "A", "TTL": 0,
     "ResourceRecords": [{"Value": "76.97.4.210"}]},
    {"Name": "elsewhere.arigsela.com.", "Type": "A", "TTL": 300,
     "ResourceRecords": [{"Value": "203.0.113.77"}]},
    {"Name": "arigsela.com.", "Type": "NS", "TTL": 172800,
     "ResourceRecords": [{"Value": "ns-1337.awsdns-39.org."}]},
    {"Name": "multi.arigsela.com.", "Type": "A", "TTL": 300,
     "ResourceRecords": [{"Value": "76.97.4.210"}, {"Value": "1.2.3.4"}]},
]


def test_selects_only_records_on_the_old_address(reconcile):
    got = reconcile.records_needing_update(RECORDSETS, "76.97.4.210")
    assert [r["Name"] for r in got] == ["argocd.arigsela.com.", "grafana.arigsela.com."]


def test_ignores_non_a_records(reconcile):
    got = reconcile.records_needing_update(RECORDSETS, "76.97.4.210")
    assert all(r["Type"] == "A" for r in got)


def test_ignores_multi_value_records(reconcile):
    """A record with several values is not ours to rewrite blindly."""
    got = reconcile.records_needing_update(RECORDSETS, "76.97.4.210")
    assert "multi.arigsela.com." not in [r["Name"] for r in got]


def test_returns_nothing_when_already_current(reconcile):
    assert reconcile.records_needing_update(RECORDSETS, "203.0.113.9") == []


def test_change_batch_preserves_ttl_per_record(reconcile):
    records = reconcile.records_needing_update(RECORDSETS, "76.97.4.210")
    batch = reconcile.build_change_batch(records, "203.0.113.9")
    ttls = {c["ResourceRecordSet"]["Name"]: c["ResourceRecordSet"]["TTL"]
            for c in batch["Changes"]}
    assert ttls == {"argocd.arigsela.com.": 300, "grafana.arigsela.com.": 0}


def test_change_batch_upserts_the_new_address(reconcile):
    records = reconcile.records_needing_update(RECORDSETS, "76.97.4.210")
    batch = reconcile.build_change_batch(records, "203.0.113.9")
    assert all(c["Action"] == "UPSERT" for c in batch["Changes"])
    for change in batch["Changes"]:
        assert change["ResourceRecordSet"]["ResourceRecords"] == [{"Value": "203.0.113.9"}]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/wan_ip/test_route53.py -q
```

Expected: FAIL with `AttributeError: module 'reconcile' has no attribute 'records_needing_update'`.

- [ ] **Step 3: Append the implementation**

Append to the `reconcile.py` block in `base-apps/wan-ip-monitor/configmap-reconcile.yaml` (keep the 4-space YAML block indentation):

```python
    HOSTED_ZONE_ID = os.environ.get("HOSTED_ZONE_ID", "Z0524483LR4JCFNLS7N0")


    def aws_json(args):
        """Run an aws CLI command and parse its JSON output."""
        result = subprocess.run(
            ["aws"] + args, capture_output=True, text=True, check=True, timeout=60
        )
        return json.loads(result.stdout) if result.stdout.strip() else {}


    def records_needing_update(recordsets, old_ip):
        """A records whose ONLY value is the previous WAN address.

        Anything else is left alone on purpose: a record pointing somewhere else
        is someone's deliberate choice, and a multi-value record is not ours to
        rewrite from a single-address signal.
        """
        out = []
        for record in recordsets:
            if record.get("Type") != "A":
                continue
            values = [v["Value"] for v in record.get("ResourceRecords", [])]
            if values == [old_ip]:
                out.append(record)
        return out


    def build_change_batch(records, new_ip):
        """UPSERT each record onto the new address, preserving its own TTL."""
        changes = []
        for record in records:
            updated = dict(record)
            updated["ResourceRecords"] = [{"Value": new_ip}]
            changes.append({"Action": "UPSERT", "ResourceRecordSet": updated})
        return {"Comment": "wan-ip-monitor: rotate to %s" % new_ip, "Changes": changes}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/wan_ip/ -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add base-apps/wan-ip-monitor/configmap-reconcile.yaml tests/wan_ip/test_route53.py
git commit -m "wan-ip-monitor: reconcile Route 53 A records onto the current WAN address"
```

---

## Task 4: GitHub PR, notification, and the main loop

The reviewed half, plus the wiring that runs each cycle.

**Files:**
- Modify: `base-apps/wan-ip-monitor/configmap-reconcile.yaml` (append to `reconcile.py`)
- Create: `tests/wan_ip/test_github.py`

**Interfaces:**
- Consumes: everything from Tasks 2–3.
- Produces:
  - `branch_name(new_ip: str) -> str`
  - `detect_wan_ip(fetch) -> str`
  - `notify(payload: dict, post) -> None`
  - `main() -> int`
  - `open_allowlist_pr(old_ip: str, new_ip: str) -> tuple[str | None, bool]`

- [ ] **Step 1: Write the failing tests**

Create `tests/wan_ip/test_github.py`:

```python
import pytest


def test_branch_name_is_deterministic_per_address(reconcile):
    """Idempotency depends on this: same rotation, same branch, so a second run
    finds the existing PR instead of opening another."""
    assert reconcile.branch_name("203.0.113.9") == "automation/wan-ip-203.0.113.9"
    assert reconcile.branch_name("203.0.113.9") == reconcile.branch_name("203.0.113.9")


def test_detect_uses_the_first_source_that_returns_a_public_address(reconcile):
    calls = []

    def fetch(url):
        calls.append(url)
        return "203.0.113.9\n"

    assert reconcile.detect_wan_ip(fetch) == "203.0.113.9"
    assert len(calls) == 1


def test_detect_falls_through_a_bad_response(reconcile):
    def fetch(url):
        return "<html>captive portal</html>" if "amazonaws" in url else "203.0.113.9"

    assert reconcile.detect_wan_ip(fetch) == "203.0.113.9"


def test_detect_falls_through_an_exception(reconcile):
    def fetch(url):
        if "amazonaws" in url:
            raise OSError("network unreachable")
        return "203.0.113.9"

    assert reconcile.detect_wan_ip(fetch) == "203.0.113.9"


def test_detect_raises_when_every_source_is_unusable(reconcile):
    def fetch(url):
        return "10.0.0.1"

    with pytest.raises(RuntimeError):
        reconcile.detect_wan_ip(fetch)


def test_notify_never_raises(reconcile):
    """A failed notification must not fail the run - DNS is already fixed by
    then, and losing the alert is strictly better than losing the reconcile."""
    def post(payload):
        raise OSError("n8n is down")

    reconcile.notify({"new": "203.0.113.9"}, post)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/wan_ip/test_github.py -q
```

Expected: FAIL with `AttributeError: module 'reconcile' has no attribute 'branch_name'`.

- [ ] **Step 3: Append the implementation**

Append to the `reconcile.py` block in the ConfigMap:

```python
    REPO = os.environ.get("GITHUB_REPO", "arigsela/kubernetes")
    POLICY_PATH = "base-apps/istio-ingress/authorizationpolicy.yaml"
    N8N_WEBHOOK = os.environ.get(
        "N8N_WEBHOOK",
        "http://n8n.n8n.svc.cluster.local:5678/webhook/wan-ip-rotated",
    )
    IP_SOURCES = ["https://checkip.amazonaws.com", "https://ifconfig.me/ip"]
    DRY_RUN = os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")


    def http_get(url):
        request = urllib.request.Request(url, headers={"User-Agent": "wan-ip-monitor"})
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.read().decode()


    def detect_wan_ip(fetch=http_get):
        """First source that yields a usable public address wins.

        Two sources because a single detector having a bad day should not look
        like a rotation. Anything unusable is skipped rather than trusted.
        """
        for url in IP_SOURCES:
            try:
                candidate = fetch(url).strip()
            except Exception:
                continue
            if is_valid_public_ipv4(candidate):
                return candidate
        raise RuntimeError("no source returned a usable public IPv4")


    def branch_name(new_ip):
        return "automation/wan-ip-%s" % new_ip


    def github(method, path, body=None):
        url = "https://api.github.com/repos/%s%s" % (REPO, path)
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Authorization", "Bearer %s" % os.environ["GITHUB_TOKEN"])
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("User-Agent", "wan-ip-monitor")
        if data:
            request.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = response.read().decode()
        return json.loads(payload) if payload.strip() else {}


    def open_allowlist_pr(old_ip, new_ip):
        """Branch, commit the rewritten policy, open a PR.

        Returns (url, created). `created` is False when this rotation already
        had a PR, which is what keeps a rotation that sits unmerged for a day
        from opening 288 of them - and what keeps the notification quiet.
        """
        branch = branch_name(new_ip)
        owner = REPO.split("/")[0]
        existing = github("GET", "/pulls?state=open&head=%s:%s" % (owner, branch))
        if existing:
            return existing[0]["html_url"], False

        main_sha = github("GET", "/git/ref/heads/main")["object"]["sha"]
        try:
            github("POST", "/git/refs", {"ref": "refs/heads/%s" % branch, "sha": main_sha})
        except urllib.error.HTTPError as exc:
            if exc.code != 422:  # 422 = ref already exists, which is fine
                raise

        current = github("GET", "/contents/%s?ref=main" % POLICY_PATH)
        text = base64.b64decode(current["content"]).decode()
        rewritten = rewrite_policy(text, old_ip, new_ip)
        if rewritten == text:
            return None, False

        github(
            "PUT",
            "/contents/%s" % POLICY_PATH,
            {
                "message": "istio-ingress: follow the WAN IP rotation %s -> %s" % (old_ip, new_ip),
                "content": base64.b64encode(rewritten.encode()).decode(),
                "sha": current["sha"],
                "branch": branch,
            },
        )
        pr = github(
            "POST",
            "/pulls",
            {
                "title": "istio-ingress: follow the WAN IP rotation %s -> %s" % (old_ip, new_ip),
                "head": branch,
                "base": "main",
                "body": (
                    "Opened automatically by `wan-ip-monitor`.\n\n"
                    "The ISP moved the home WAN address from `%s` to `%s`.\n\n"
                    "**Route 53 has already been updated** - DNS is correct right now. "
                    "Until this merges, the allow-listed hosts resolve correctly and "
                    "answer `403`, because the Istio policy still trusts the old "
                    "address.\n\nMerging is all that is required; Argo CD syncs it.\n"
                ) % (old_ip, new_ip),
            },
        )
        return pr["html_url"]


    def post_json(payload):
        request = urllib.request.Request(
            N8N_WEBHOOK,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(request, timeout=15).close()


    def notify(payload, post=post_json):
        """Best effort. Never let a missing alert fail an otherwise good run."""
        try:
            post(payload)
        except Exception as exc:
            print("notify failed (continuing): %s" % exc)


    def read_policy_from_main():
        """The allow-list as it exists on main.

        main IS the desired state - Argo CD syncs from it, and the PR targets
        it - so reading from there keeps the comparison and the eventual commit
        anchored to the same revision. Reading a mounted copy instead would add
        a second source of truth that can silently drift.
        """
        current = github("GET", "/contents/%s?ref=main" % POLICY_PATH)
        return base64.b64decode(current["content"]).decode()


    def main():
        current = detect_wan_ip()
        declared = read_declared_wan_ip(read_policy_from_main())
        print("detected=%s declared=%s dry_run=%s" % (current, declared, DRY_RUN))

        # The annotation is the whole comparison. When it already names the
        # detected address, both targets are consistent by definition: the
        # Route 53 records this job manages are exactly those sitting on the
        # declared address, and the allow-list is built around it. This is the
        # steady state and must stay completely silent - it runs 288 times a day.
        if declared == current:
            print("in sync, nothing to do")
            return 0

        zone = aws_json(
            ["route53", "list-resource-record-sets", "--hosted-zone-id", HOSTED_ZONE_ID,
             "--output", "json"]
        )
        stale = records_needing_update(zone.get("ResourceRecordSets", []), declared)

        if DRY_RUN:
            print("DRY_RUN: would move %d records %s -> %s and open a PR"
                  % (len(stale), declared, current))
            return 0

        updated = 0
        if stale:
            batch = build_change_batch(stale, current)
            aws_json(
                ["route53", "change-resource-record-sets",
                 "--hosted-zone-id", HOSTED_ZONE_ID,
                 "--change-batch", json.dumps(batch), "--output", "json"]
            )
            updated = len(stale)
            print("updated %d Route 53 records" % updated)

        pr_url, pr_created = open_allowlist_pr(declared, current)
        print("allow-list PR: %s (new=%s)" % (pr_url, pr_created))

        # Only speak up when something actually happened. Between the rotation
        # and the merge this function runs every 5 minutes with the PR already
        # open and DNS already fixed; alerting on each of those would train the
        # operator to ignore it.
        if updated or pr_created:
            notify({
                "old": declared,
                "new": current,
                "records_updated": updated,
                "pr_url": pr_url,
            })
        return 0


    if __name__ == "__main__":
        raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/wan_ip/ -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add base-apps/wan-ip-monitor/configmap-reconcile.yaml tests/wan_ip/test_github.py
git commit -m "wan-ip-monitor: open the allow-list PR, notify n8n, wire the main loop"
```

---

## Task 5: Kubernetes manifests, Argo CD Application, and docs

Everything needed to actually run, plus the doc contract this repo's CI enforces.

**Files:**
- Create: `base-apps/wan-ip-monitor/namespace.yaml`
- Create: `base-apps/wan-ip-monitor/secret-store.yaml`
- Create: `base-apps/wan-ip-monitor/external-secret.yaml`
- Create: `base-apps/wan-ip-monitor/cronjob.yaml`
- Create: `base-apps/wan-ip-monitor/docs.md`
- Create: `base-apps/wan-ip-monitor/runbook.md`
- Create: `base-apps/wan-ip-monitor/catalog-info.yaml`
- Create: `base-apps/wan-ip-monitor/mkdocs.yml`
- Create: `base-apps/wan-ip-monitor.yaml`
- Modify: `scripts/agent-docs-scope.txt`

**Interfaces:**
- Consumes: the ConfigMap and script from Tasks 2–4; the Vault role from Task 0.
- Produces: a running CronJob. Nothing downstream depends on it.

- [ ] **Step 1: Verify the container image has both `aws` and `python3`**

The script shells out to the AWS CLI and runs under Python. Confirm one image carries both before committing to it:

```bash
kubectl run imgcheck --rm -i --restart=Never --image=amazon/aws-cli:2.17.0 \
  --command -- sh -c 'aws --version; python3 -c "import yaml; print(\"yaml ok\")"'
```

If either is missing, do **not** try to install the AWS CLI at runtime. Instead switch to `python:3.12-slim`, replace `aws_json` with boto3 equivalents, and set the container command to `pip install --quiet boto3==1.35.99 pyyaml==6.0.2 && python3 /app/reconcile.py`. The boto3 calls are `client.list_resource_record_sets(HostedZoneId=...)` and `client.change_resource_record_sets(HostedZoneId=..., ChangeBatch=batch)`; `records_needing_update` and `build_change_batch` are unaffected because they operate on plain dicts either way. Record which option was chosen in `docs.md`.

- [ ] **Step 2: Create the namespace, SecretStore, and ExternalSecret**

`base-apps/wan-ip-monitor/namespace.yaml`:

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: wan-ip-monitor
```

`base-apps/wan-ip-monitor/secret-store.yaml`:

```yaml
apiVersion: external-secrets.io/v1
kind: SecretStore
metadata:
  name: vault-backend
  namespace: wan-ip-monitor
spec:
  provider:
    vault:
      server: "http://vault.vault.svc.cluster.local:8200"
      path: "k8s-secrets"
      version: "v2"
      auth:
        kubernetes:
          mountPath: "kubernetes"
          role: "wan-ip-monitor"
          serviceAccountRef:
            name: "default"
```

`base-apps/wan-ip-monitor/external-secret.yaml`:

```yaml
# Credentials are deliberately narrow. The AWS key should carry only
# route53:ListResourceRecordSets + ChangeResourceRecordSets on the arigsela.com
# zone, and the GitHub token only contents:write + pull_requests:write. This job
# must never be able to merge its own PR - the review of the security boundary
# is the entire reason the allow-list half goes through a PR at all.
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: wan-ip-monitor
  namespace: wan-ip-monitor
spec:
  refreshInterval: "1h"
  secretStoreRef:
    name: vault-backend
    kind: SecretStore
  target:
    name: wan-ip-monitor
    creationPolicy: Owner
  data:
    - secretKey: AWS_ACCESS_KEY_ID
      remoteRef:
        key: wan-ip-monitor
        property: aws-access-key-id
    - secretKey: AWS_SECRET_ACCESS_KEY
      remoteRef:
        key: wan-ip-monitor
        property: aws-secret-access-key
    - secretKey: GITHUB_TOKEN
      remoteRef:
        key: wan-ip-monitor
        property: github-token
```

- [ ] **Step 3: Create the CronJob**

`base-apps/wan-ip-monitor/cronjob.yaml`:

```yaml
# Runs every 5 minutes. Steady state is two read-only API calls, so the cost of
# checking often is negligible and the reward is that a rotation is caught
# before it is noticed.
#
# The policy file is mounted from the live cluster object rather than fetched
# from GitHub, so the job reconciles against what is actually enforced.
apiVersion: batch/v1
kind: CronJob
metadata:
  name: wan-ip-monitor
  namespace: wan-ip-monitor
spec:
  schedule: "*/5 * * * *"
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 3
  concurrencyPolicy: Forbid
  jobTemplate:
    spec:
      ttlSecondsAfterFinished: 3600
      backoffLimit: 1
      template:
        spec:
          restartPolicy: Never
          nodeSelector:
            node.kubernetes.io/workload: infrastructure
          tolerations:
            - key: node-role.kubernetes.io/control-plane
              effect: NoSchedule
          containers:
            - name: reconcile
              image: amazon/aws-cli:2.17.0
              command: ["python3", "/app/reconcile.py"]
              env:
                - name: AWS_DEFAULT_REGION
                  value: us-east-1
                - name: DRY_RUN
                  value: "true"
              envFrom:
                - secretRef:
                    name: wan-ip-monitor
              volumeMounts:
                - name: script
                  mountPath: /app
              resources:
                requests:
                  cpu: 10m
                  memory: 64Mi
                limits:
                  memory: 128Mi
          volumes:
            - name: script
              configMap:
                name: wan-ip-reconcile
```

Note there is no policy volume: the job reads the allow-list from `main` via the
GitHub API (`read_policy_from_main`), which is the same revision Argo CD syncs
and the same one the PR targets. That avoids a mounted copy that could drift,
and avoids granting this job RBAC to read Istio objects.

- [ ] **Step 4: Create the Argo CD Application**

`base-apps/wan-ip-monitor.yaml`:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  finalizers:
    - resources-finalizer.argocd.argoproj.io
  name: wan-ip-monitor
  namespace: argo-cd
spec:
  project: default
  source:
    repoURL: https://github.com/arigsela/kubernetes
    targetRevision: main
    path: base-apps/wan-ip-monitor
    directory:
      exclude: '{catalog-info.yaml,mkdocs.yml}'
  destination:
    server: https://kubernetes.default.svc
    namespace: wan-ip-monitor
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
```

- [ ] **Step 5: Write the docs contract files**

Copy the three templates from `templates/agent-docs/` into `base-apps/wan-ip-monitor/` as `docs.md`, `runbook.md`, and `catalog-info.yaml`, plus `mkdocs.yml`. `docs.md` must cover: what the job does, the reconciler-not-detector distinction, why Route 53 is automatic but the allow-list is a PR, and the `arigsela.com/wan-ip` annotation contract. `runbook.md` must cover at minimum:

- **PR opened but nothing merged** — expected state; hosts resolve and return `403` until merge.
- **Job failing on AWS auth** — check the ExternalSecret synced and the Vault role from Task 0 exists.
- **Job failing on GitHub auth** — token expired; re-mint with `contents:write` + `pull_requests:write`.
- **Repeated PRs for the same address** — branch deletion is racing the job; confirm `branch_name` is deterministic.
- **Detected address flapping** — check both IP sources agree; a CGNAT'd or double-NAT'd connection breaks the whole premise.
- **Disabling it** — set `DRY_RUN=true` on the CronJob, or suspend with `kubectl -n wan-ip-monitor patch cronjob wan-ip-monitor -p '{"spec":{"suspend":true}}'`.

Then register it and regenerate:

```bash
echo "wan-ip-monitor" >> scripts/agent-docs-scope.txt
python3 scripts/gen-okf.py --repo-root .
python3 scripts/gen-techdocs.py --repo-root .
```

- [ ] **Step 6: Validate everything locally**

```bash
python -m pytest tests/wan_ip/ -q
python3 scripts/validate-agent-docs.py --repo-root .
python3 scripts/gen-techdocs.py --repo-root . --check
python3 -c "import yaml,glob; [yaml.safe_load_all(open(f).read()) for f in glob.glob('base-apps/wan-ip-monitor/*.yaml')]; print('yaml ok')"
```

Expected: all pass.

- [ ] **Step 7: Commit and open the PR**

```bash
git add base-apps/wan-ip-monitor/ base-apps/wan-ip-monitor.yaml scripts/agent-docs-scope.txt base-apps/index.md
git commit -m "wan-ip-monitor: deploy the reconciler as a CronJob"
git push -u origin feat/wan-ip-monitor
```

- [ ] **Step 8: Verify in dry-run before arming it**

The CronJob ships with `DRY_RUN=true` deliberately. After it syncs, confirm a real run is sane:

```bash
kubectl -n wan-ip-monitor create job --from=cronjob/wan-ip-monitor dryrun-1
kubectl -n wan-ip-monitor logs job/dryrun-1
```

Expected output: `detected=<your WAN IP> declared=76.97.4.210 dry_run=True` followed by `in sync, nothing to do`.

Only once that is confirmed, flip `DRY_RUN` to `"false"` in `cronjob.yaml` and push that as a separate commit — so arming the automation is its own reviewable change.

---

## Self-Review Notes

- **Spec coverage:** detection (T4), Route 53 reconcile (T3), allow-list PR (T4), idempotent branch (T4), n8n notify (T4), annotation as source of truth (T1), tests wired to CI (T1), Vault prerequisite (T0), docs contract (T5). No gaps.
- **Type consistency:** `rewrite_policy`, `read_declared_wan_ip`, `is_valid_public_ipv4` defined in T2 and used unchanged in T4's `open_allowlist_pr` and `main`. `records_needing_update` / `build_change_batch` defined in T3, used in T4's `main`. `aws_json` defined in T3, used in T4.
- **Known deferral:** the policy snapshot ConfigMap (T5 Step 4) duplicates the annotation value. If it drifts from the real policy the job reconciles against a stale declaration. A follow-up should either grant read RBAC on the Istio object or add a CI check that the two agree — noted in `docs.md` rather than left silent.
