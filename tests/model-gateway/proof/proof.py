#!/usr/bin/env python3
"""Local proof of the model gateway's generated config (spec §7 step 3; plan Task 5).

Runs, in Docker: Postgres 16, a mock Anthropic API (mock_anthropic.py) and TWO gateways (A and B)
from the exact image the cluster runs, both on the same database and both on the committed
base-apps/model-gateway/configmap.yaml (see committed_config() for the few local overrides), with
test keys passed in the environment the way the cluster passes the real ones. Then it checks, one by one, the behaviours the spec
relies on, and exits non-zero at the first that fails. The provider key is a made-up test value
and the upstream is the mock, except check 14: one refused call to the real api.anthropic.com.

    tests/model-gateway/proof/run-proof.sh            # needs Docker and the ECR image pulled
"""
from __future__ import annotations

import json
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import model_budgets as mb  # noqa: E402

IMAGE = ("852893458518.dkr.ecr.us-east-2.amazonaws.com/agentgateway:v1.6.0"
         "@sha256:083adf216093ed57813debcfdbb5f6aa9cb119741b373455233f6c0605faca2d")
NET = "mgproof"
ALPINE = "alpine:3.24@sha256:be2656061b536c1883c4034790e21d0a09c5aaaf51968b89368a3c0371ea2c9a"
VOLUME = "mgproof-files"
PROVIDER_KEY = "sk-proof-provider-not-a-real-key"
DB_PASSWORD = "proof-db-password-0123456789abcdef"
PORTS = {"A": {"llm": 18080, "admin": 18000, "stats": 18020}, "B": {"llm": 18081, "admin": 18001, "stats": 18021}}
MOCK_PORT = 18090
RESULTS: list[tuple[str, bool, str]] = []


def sh(*args, check=True, **kw):
    return subprocess.run(list(args), check=check, capture_output=True, text=True, **kw)


def cleanup():
    for name in ("mgproof-gw-a", "mgproof-gw-b", "mgproof-gw-tls", "mgproof-mock", "mgproof-pg"):
        sh("docker", "rm", "-f", name, check=False)
    sh("docker", "rm", "-f", "mgproof-files", check=False)
    sh("docker", "network", "rm", NET, check=False)
    sh("docker", "volume", "rm", VOLUME, check=False)


def http(port, path, *, key=None, body=None, method="POST", timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method)
    req.add_header("content-type", "application/json")
    req.add_header("anthropic-version", "2023-06-01")
    if key is not None:
        req.add_header("x-api-key", key)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read().decode()


def msg(model, usage="in=10,out=5", *, stream=False, text="hi", extra=None):
    body = {"model": model, "max_tokens": 64, "stream": stream, "metadata": {"user_id": f"usage:{usage}"},
            "messages": [{"role": "user", "content": [{"type": "text", "text": text,
                                                        "cache_control": {"type": "ephemeral"}}]}]}
    body.update(extra or {})
    return body


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""), flush=True)
    if not ok:
        raise SystemExit(1)


def budgets(gw):
    status, _, body = http(PORTS[gw]["admin"], "/api/budgets/status", method="GET")
    if status != 200:
        raise RuntimeError(f"/api/budgets/status → {status}: {body[:300]}")
    return json.loads(body)


def used(gw, group, keys):
    """The day's spend the gateway holds for a group's key (USD)."""
    for b in budgets(gw)["budgets"]:
        if b.get("apiKeyName") in (group, keys[group]) or group in json.dumps(b.get("apiKeyName", "")):
            return float(b["usage"]["used"])
    raise KeyError(f"{group} not in {json.dumps(budgets(gw))[:600]}")


def wait_for(fn, timeout=20, step=1.0):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        try:
            last = fn()
            if last:
                return last
        except Exception as e:  # noqa: BLE001 - the gateway may still be starting
            last = e
        time.sleep(step)
    return last


def mock_log():
    return json.loads(urllib.request.urlopen(f"http://127.0.0.1:{MOCK_PORT}/_log", timeout=10).read())


def committed_config(upstream: str | None = "http://mgproof-mock:8090/v1") -> dict:
    """base-apps/model-gateway/configmap.yaml as committed, with only what must differ locally:
    the mock as upstream, the admin API reachable from the host, no tracing, and two tiny
    budgets (dcc: the block check; restarter: the shared count). The $MODEL_GATEWAY_* references
    stay, so the gateway's own env expansion of keys and the database URL is part of the proof."""
    doc = yaml.safe_load((REPO / "base-apps/model-gateway/configmap.yaml").read_text())
    cfg = yaml.safe_load(doc["data"]["config.yaml"])
    for m in cfg["llm"]["models"]:
        if upstream:
            m["params"]["baseUrl"] = upstream
    cfg["config"]["database"]["url"] = "postgres://proof@mgproof-pg:5432/model_gateway?sslmode=disable"
    cfg["config"]["adminAddr"] = "0.0.0.0:15000"
    cfg.pop("frontendPolicies", None)
    for k in cfg["llm"]["policies"]["apiKey"]["keys"]:
        if k["metadata"]["name"] in ("dcc", "restarter"):
            k["budgets"][0]["limit"]["amount"] = 0.001
    return cfg


def start_stack(work: Path, stagger: bool = False, gateways=("A", "B")) -> None:
    """Postgres, the mock and the gateways (B only once A is ready when stagger is set)."""
    sh("docker", "network", "create", NET)
    sh("docker", "run", "-d", "--name", "mgproof-pg", "--network", NET, "-e", "POSTGRES_USER=proof",
       "-e", f"POSTGRES_PASSWORD={DB_PASSWORD}", "-e", "POSTGRES_DB=model_gateway", "postgres:16")
    sh("docker", "create", "--name", "mgproof-mock", "--network", NET, "-p", f"{MOCK_PORT}:8090",
       "python:3.12-slim", "python", "/mock_anthropic.py")
    sh("docker", "cp", str(HERE / "mock_anthropic.py"), "mgproof-mock:/mock_anthropic.py")
    sh("docker", "start", "mgproof-mock")
    # Over TCP and on the database itself: during first boot the image runs a socket-only server
    # that answers pg_isready before the real one listens.
    wait_for(lambda: sh("docker", "exec", "mgproof-pg", "pg_isready", "-h", "127.0.0.1", "-U", "proof",
                        "-d", "model_gateway", check=False).returncode == 0, 60)
    time.sleep(2)
    files = work / "files"
    (files / "config").mkdir(parents=True, exist_ok=True)
    (files / "anthropic").mkdir(parents=True, exist_ok=True)
    (files / "config/config.yaml").write_text((work / "config.yaml").read_text())
    (files / "anthropic/api-key").write_text(PROVIDER_KEY)
    sh("docker", "volume", "create", VOLUME)
    sh("docker", "create", "--name", "mgproof-files", "-v", f"{VOLUME}:/files", "alpine:3.20", "true")
    sh("docker", "cp", f"{files}/.", "mgproof-files:/files")
    sh("docker", "rm", "-f", "mgproof-files")
    # The CA bundle the Deployment's init container copies from the repo's pinned alpine.
    sh("docker", "run", "--rm", "-v", f"{VOLUME}:/files", ALPINE, "sh", "-c",
       "mkdir -p /files/ca && cp /etc/ssl/certs/ca-certificates.crt /files/ca/")
    for gw in gateways:
        if stagger and gw == "B":
            wait_for(lambda: budgets("A"), 90, 2)
        name = f"mgproof-gw-{gw.lower()}"
        p = PORTS[gw]
        # As the Deployment runs it: UID 65532, read-only root, /tmp writable, the config and the
        # provider key mounted read-only where the pod mounts them (a volume stands in for the
        # ConfigMap and the Secret).
        sh("docker", "create", "--name", name, "--network", NET, "--platform", "linux/amd64",
           "--env-file", str(work / "env"), "--user", "65532:65532", "--read-only", "--tmpfs", "/tmp",
           "--mount", f"type=volume,src={VOLUME},dst=/config,volume-subpath=config,readonly",
           "--mount", f"type=volume,src={VOLUME},dst=/var/run/model-gateway/anthropic,volume-subpath=anthropic,readonly",
           "-p", f"{p['llm']}:8080", "-p", f"{p['admin']}:15000", "-p", f"{p['stats']}:15020",
           IMAGE, "-f", "/config/config.yaml")
        sh("docker", "start", name)
    for gw in gateways:
        ok = wait_for(lambda gw=gw: budgets(gw), 90, 2)
        if not isinstance(ok, dict):
            print(sh("docker", "logs", f"mgproof-gw-{gw.lower()}", check=False).stderr[-3000:])
            check(f"gateway {gw} starts on the generated config", False, str(ok))


def tls_check(work: Path, keys: dict) -> None:
    """14. The real upstream over TLS (the one call that leaves this machine: a 16-token request
    with a made-up key, refused by Anthropic). The image is FROM scratch: without a CA bundle the
    gateway cannot verify api.anthropic.com (live 2026-10-23: 503 UnknownIssuer); with the bundle
    the Deployment mounts (SSL_CERT_FILE) Anthropic itself answers 401 with its request-id."""
    cfg = committed_config(upstream=None)
    (work / "files-tls").mkdir(exist_ok=True)
    path = work / "config-tls.yaml"
    path.write_text(yaml.safe_dump(cfg, sort_keys=False))
    sh("docker", "create", "--name", "mgproof-files", "-v", f"{VOLUME}:/files", "alpine:3.20", "true")
    sh("docker", "cp", str(path), "mgproof-files:/files/config/config-tls.yaml")
    sh("docker", "rm", "-f", "mgproof-files")
    results = {}
    for label, extra in (("no bundle", []),
                         ("bundle", ["--mount", f"type=volume,src={VOLUME},dst=/etc/ssl/certs,volume-subpath=ca,readonly",
                                     "-e", "SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt"])):
        name = "mgproof-gw-tls"
        sh("docker", "rm", "-f", name, check=False)
        sh("docker", "run", "-d", "--name", name, "--network", NET, "--platform", "linux/amd64",
           "--env-file", str(work / "env"), "--user", "65532:65532", "--read-only", "--tmpfs", "/tmp",
           "--mount", f"type=volume,src={VOLUME},dst=/config,volume-subpath=config,readonly",
           "--mount", f"type=volume,src={VOLUME},dst=/var/run/model-gateway/anthropic,volume-subpath=anthropic,readonly",
           *extra, "-p", "18082:8080", IMAGE, "-f", "/config/config-tls.yaml")
        wait_for(lambda: http(18082, "/v1/messages", key="probe", body=msg("claude-haiku-5-5"))[0] != 0, 60, 2)
        results[label] = http(18082, "/v1/messages", key=keys["k8s-reader"], body=msg("claude-haiku-5-5"))
        sh("docker", "rm", "-f", name, check=False)
    s0, _, b0 = results["no bundle"]
    s1, h1, b1 = results["bundle"]
    check("14a. without a CA bundle the upstream TLS fails (the live bug)", s0 == 503 and "UnknownIssuer" in b0, f"{s0} {b0[:120]}")
    check("14b. with the Deployment's CA bundle Anthropic itself answers (401 for the made-up key)",
          s1 == 401 and "request-id" in h1, f"{s1} request-id={'request-id' in h1} {b1[:120]}")


def go_sdk_check(keys):
    """The Anthropic Go SDK v1.80.0 (kagent's Go ADK) against an over-budget group."""
    name = "mgproof-go"
    sh("docker", "rm", "-f", name, check=False)
    sh("docker", "create", "--name", name, "--network", NET, "-w", "/src", "-e", "BASE=http://mgproof-gw-a:8080",
       "-e", f"KEY={keys['dcc']}", "-e", "MODEL=claude-haiku-5-5", "golang:1.25-alpine",
       "sh", "-c", "go mod tidy >/dev/null 2>&1 && go run .")
    sh("docker", "cp", f"{HERE / 'go-sdk'}/.", f"{name}:/src")
    out = sh("docker", "start", "-a", name, check=False, timeout=600)
    sh("docker", "rm", "-f", name, check=False)
    text = out.stdout + out.stderr
    elapsed = float((text.split("elapsed=")[1].split()[0]) if "elapsed=" in text else 999)
    check("11. Go SDK v1.80.0: an over-budget call fails within seconds with budget_exceeded",
          elapsed < 10 and "budget_exceeded" in text and "429" in text, f"elapsed {elapsed}s; {text.strip()[-240:]}")


def main() -> int:
    cleanup()
    keys = {g: "vk-" + secrets.token_hex(16) for g in mb.load()["groups"]}
    cfg = committed_config()
    work = Path(tempfile.mkdtemp(prefix="mgproof-"))
    (work / "config.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    env = {mb.env_name(g): k for g, k in keys.items()}
    env["PGPASSWORD"] = DB_PASSWORD
    # As in the cluster: agentgateway logs its whole startup config when it sees this variable.
    env["KUBERNETES_SERVICE_HOST"] = "10.43.0.1"
    (work / "env").write_text("".join(f"{k}={v}\n" for k, v in env.items()))

    start_stack(work, stagger="--stagger" in sys.argv)
    check("both gateways start on the generated config and the shared database", True)

    A, B = PORTS["A"]["llm"], PORTS["B"]["llm"]
    sonnet, haiku = "claude-sonnet-5-5", "claude-haiku-5-5"

    # 1. a known key is answered, and the upstream sees the provider key, never the virtual key
    s, h, body = http(A, "/v1/messages", key=keys["hk"], body=msg(sonnet))
    seen = mock_log()[-1]
    check("1. known key → 200, upstream gets the provider key", s == 200
          and seen["headers"].get("x-api-key") == PROVIDER_KEY and keys["hk"] not in json.dumps(seen),
          f"status {s}, upstream x-api-key {'provider' if seen['headers'].get('x-api-key') == PROVIDER_KEY else seen['headers'].get('x-api-key')}")

    # 2. unknown or missing key
    s1, _, _ = http(A, "/v1/messages", key="vk-not-a-key", body=msg(sonnet))
    s2, _, _ = http(A, "/v1/messages", body=msg(sonnet))
    check("2. unknown key → 401, missing key → 401", s1 == 401 and s2 == 401, f"{s1}, {s2}")

    # 9. the body reaches the provider unchanged (passthrough): cache_control and unknown fields kept
    sent = msg(sonnet, extra={"kagent_extra": {"keep": [1, 2, 3]}, "thinking": {"type": "adaptive"}})
    http(A, "/v1/messages", key=keys["hk"], body=sent)
    check("9. request body forwarded unchanged (cache_control, thinking, unknown fields)",
          mock_log()[-1]["body"] == sent)

    # 10. a key may only use its group's models
    s, _, body = http(A, "/v1/messages", key=keys["hk"], body=msg(haiku))
    check("10. a key outside its allowedModels is refused", s in (401, 403, 404), f"{s} {body[:120]}")

    # 3. a streamed call is charged; 4. prices: Sonnet with cache reads, Haiku base and >100K tier
    before = used("A", "hk", keys)
    s, h, body = http(A, "/v1/messages", key=keys["hk"], body=msg(sonnet, "in=100,out=50,cr=10000,cw=0", stream=True))
    want = (100 * 2 + 10000 * 0.2 + 50 * 10) / 1e6
    got = wait_for(lambda: (lambda u: u if abs(u - before - want) < 1e-9 else None)(used("A", "hk", keys)), 15)
    check("3+4a. streamed Sonnet call charged at the collector's price (cache reads at 0.2)",
          s == 200 and "message_stop" in body and isinstance(got, float), f"charged {got if isinstance(got, float) else '?'} - {before}, want +{want}")
    for usage, want, label in (("in=1000,out=500", (1000 * 0.1 + 500 * 0.5) / 1e6, "base"),
                               ("in=200000,out=500", (200000 * 0.5 + 500 * 2.5) / 1e6, "over 100K")):
        before = used("A", "k8s-reader", keys)
        s, _, _ = http(A, "/v1/messages", key=keys["k8s-reader"], body=msg(haiku, usage))
        got = wait_for(lambda b=before, w=want: (lambda u: u if abs(u - b - w) < 1e-9 else None)(used("A", "k8s-reader", keys)), 15)
        check(f"4. Haiku charged at the {label} rate", s == 200 and isinstance(got, float),
              f"want +{want}, have {used('A', 'k8s-reader', keys) - before}")

    # 5. over budget: 429 budget_exceeded, x-should-retry: false (the first call is allowed: checked before, charged after)
    s, _, _ = http(A, "/v1/messages", key=keys["dcc"], body=msg(haiku, "in=10000,out=2000"))
    check("5a. the call that overspends is still answered", s == 200, str(s))
    wait_for(lambda: used("A", "dcc", keys) >= 0.001, 15)
    s, h, body = http(A, "/v1/messages", key=keys["dcc"], body=msg(haiku))
    err = json.loads(body).get("error", {}) if body.startswith("{") else {}
    check("5b. over budget → 429 budget_exceeded with x-should-retry: false",
          s == 429 and err.get("code") == "budget_exceeded" and h.get("x-should-retry") == "false",
          f"{s} {body[:160]} x-should-retry={h.get('x-should-retry')!r} retry-after={h.get('retry-after')}")

    # 6. Anthropic's own 429 keeps its retry behaviour
    s, h, body = http(A, "/v1/messages", key=keys["hk"], body=msg(sonnet, text="MOCK429"))
    check("6. upstream 429 keeps retry-after and is not marked no-retry",
          s == 429 and h.get("retry-after") == "3" and h.get("x-should-retry", "") != "false",
          f"{s} retry-after={h.get('retry-after')} x-should-retry={h.get('x-should-retry')!r}")

    # 7. two replicas share one budget through Postgres
    s, _, _ = http(A, "/v1/messages", key=keys["restarter"], body=msg(sonnet, "in=1000,out=10"))
    t0 = time.time()
    blocked = wait_for(lambda: http(B, "/v1/messages", key=keys["restarter"], body=msg(sonnet, "in=1,out=1"))[0] == 429, 20, 1)
    # Recorded, not gated: in this proof the sharing failed in about one run in three (agentgateway
    # 1.6.0: one replica's spend never reached Postgres; cause not found), which is why the gateway
    # runs one replica (spec §3.1a). Re-run this after an agentgateway upgrade: two replicas need it.
    shared = s == 200 and blocked is True
    note = (f"A answered {s}; after {time.time() - t0:.0f}s; A used {used('A', 'restarter', keys)}, "
            f"B used {used('B', 'restarter', keys)}")
    RESULTS.append(("7. (observed) spend on A blocks the same key on B", True, ("shared: " if shared else "NOT shared: ") + note))
    print(("OBSERVED " if shared else "WARN ") + "7. spend on A blocks the same key on B — " + note, flush=True)

    # 8. token counting is routed
    s, _, body = http(A, "/v1/messages/count_tokens", key=keys["hk"], body={"model": sonnet, "messages": [{"role": "user", "content": "hi"}]})
    check("8. /v1/messages/count_tokens reaches the provider", s == 200 and mock_log()[-1]["path"].endswith("/v1/messages/count_tokens"), f"{s} {body[:120]}")

    # 11. the real client: the Anthropic Go SDK v1.80.0 (kagent's Go ADK) fails at once, with the reason
    if "--no-go" not in sys.argv:
        go_sdk_check(keys)

    # 14
    if "--no-tls" not in sys.argv:
        tls_check(work, keys)



    # 12. no secret in the gateways' logs (startup config included, Kubernetes mode)
    logs = "".join(sh("docker", "logs", f"mgproof-gw-{g}", check=False).stdout
                   + sh("docker", "logs", f"mgproof-gw-{g}", check=False).stderr for g in "ab")
    leaked = [n for n, v in [("db password", DB_PASSWORD), ("provider key", PROVIDER_KEY),
                             *[(f"key {g}", k) for g, k in keys.items()]] if v in logs]
    check("12. no secret in the logs (startup config printed in Kubernetes mode)",
          not leaked and "running with config" in logs, f"leaked: {leaked}" if leaked else "startup config logged without secrets")

    # 13. what the gateway keeps in Postgres: the budget counts and a per-request log of metadata
    # (route, group, model, tokens, cost, status). No prompt or answer: the payload table stays
    # empty and no message text appears. The log has no retention of its own; a CronJob prunes it.
    def q(sql):
        return sh("docker", "exec", "mgproof-pg", "psql", "-U", "proof", "-d", "model_gateway", "-tAc", sql).stdout.strip()
    tables = q("select table_name from information_schema.tables where table_schema = 'public' order by 1").split()
    payloads = q("select count(*) from request_log_payloads")
    text_rows = q("select count(*) from request_logs where attributes_json::text like '%MOCK429%' "
                  "or attributes_json::text like '%kagent_extra%'")
    check("13. Postgres holds budget counts and request metadata only (no payloads, no message text)",
          set(tables) == {"budget_usage", "request_logs", "request_log_payloads", "_agentgateway_request_log_migrations"}
          and payloads == "0" and text_rows == "0", f"tables {tables}, payload rows {payloads}, rows with message text {text_rows}")

    # Metrics for the alerts (Task 8): what a refusal and a charge look like per group
    metrics = urllib.request.urlopen(f"http://127.0.0.1:{PORTS['A']['stats']}/metrics", timeout=10).read().decode()
    lines = [l for l in metrics.splitlines() if not l.startswith("#") and ('group="dcc"' in l or 'group="hk"' in l)]
    (HERE / "metrics-sample.txt").write_text("\n".join(lines) + "\n")
    print(f"metrics with a group label: {len(lines)} lines → metrics-sample.txt")
    check("metrics carry the group label", any('group="dcc"' in l for l in lines))
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        if "--keep" not in sys.argv:
            cleanup()
    print(f"\n{sum(ok for _, ok, _ in RESULTS)}/{len(RESULTS)} checks passed")
    raise SystemExit(rc)
