# Callers as Principals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every A2A call that reaches the kagent controller carries an identity the mesh verified (a Dex ID token for humans, a projected service account token for automations); the controller never sees a client-chosen identity.

**Architecture:** The `kagent`, `dispatcher` and `backstage` namespaces join the ambient mesh; a waypoint in `kagent` fronts the controller Service. A `RequestAuthentication` on the waypoint (and the same rule on the ingress gateway for `kagent-mcp.arigsela.com`) verifies two issuers and writes the verified claim into `X-User-Id`; an `AuthorizationPolicy` allows the user paths only with a token, the runtime paths only from the worker principal, and the UI principal as itself. The controller stays in `insecure` mode: the mesh is the authority. Clients (`kagent_a2a.py`, `ask-hk`, the dispatcher, the capture script) learn to send tokens first, so every step is backward compatible until the enforcement task.

**Tech Stack:** Istio 1.30.5 ambient (ztunnel, `istio-waypoint` GatewayClass), Gateway API, Dex (client `kubernetes`, kubelogin v1.34), Kubernetes projected service account tokens, Python 3.12 stdlib, pytest, Argo CD `managedNamespaceMetadata`.

**Spec:** `docs/superpowers/specs/2026-10-13-callers-as-principals-and-eval-gate-design.md` (Design 1)

## Global Constraints

- The IP allow-list in `base-apps/istio-ingress/authorizationpolicy.yaml` stays; JWT is added inside the existing kagent-mcp rule's `source`, never as a second ALLOW policy (a second ALLOW would OR with the allow-list and let token-less requests through).
- Controller `controller.auth.mode` stays `insecure`; the reason is written next to the value in `base-apps/kagent.yaml`.
- Issuers: Dex `https://dex.arigsela.com` (audience `kubernetes`, claim `email`) and `https://kubernetes.default.svc.cluster.local` (audience `kagent`, claim `sub`). Projected tokens expire in 3600 s or less.
- No client sends `X-User-Id` when it sends a token. `admin@kagent.dev` is sent by no script after this plan.
- Every change goes through Git and Argo CD; nothing is `kubectl apply`ed. Live checks are read-only (`kubectl get/logs`, `curl`, `ask-hk`).
- Each task is one PR; enforcement (Task 6) is its own PR and is reverted, not patched, if an acceptance check fails.
- Conventional commits with scope; PR bodies list the local validation run.
- The dispatcher's identity becomes `system:serviceaccount:dispatcher:dispatcher`; V86–V88 keep holding.

## Review Focus

1. A Dex token whose `email` claim is absent (a client that did not request the `email` scope) must be refused on the user paths, never admitted as the controller default identity. Pinned in Task 6 (`test_dex_rule_requires_email_claim`).
2. A request with a valid token **and** a client-supplied `X-User-Id: admin@kagent.dev` must reach the controller as the token's identity. Pinned live in Task 6's acceptance (b) and by `test_request_authentication_outputs_claim_header`.
3. `?user_id=<x>` on an A2A URL is read **first** by the controller in insecure mode; the waypoint route must refuse it. Pinned in Task 6 (`test_waypoint_route_refuses_user_id_query`) and live check (c).
4. A projected token presented to the Dex rule, or a kubectl token (audience `kubernetes`) presented where audience `kagent` is required, must be a 401, not a pass. Pinned in Task 4's live checks.
5. Backstage keeps working before its plugin sends tokens: its principal is allowed without a JWT until the dated exception row is removed. Pinned in Task 6 (`test_backstage_exception_is_dated`) and live check (d).

---

### Task 1: The A2A client sends a bearer token and never a self-declared identity alongside it

**Files:**
- Modify: `scripts/kagent_a2a.py` (`request_headers`, `send_message`; add `acquire_token`, `kubelogin_token`, `serviceaccount_token`)
- Test: `tests/eval-corpus/test_kagent_a2a.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `request_headers(user: str | None, token: str | None = None) -> dict` — with `token`, sets `Authorization: Bearer <token>` and no `X-User-Id`.
  - `send_message(endpoint, text, *, ..., user=DEFAULT_USER, token: str | None = None, token_file: str | None = None, ...)` — `token_file` is read on every call.
  - `read_token_file(path: str) -> str` — stripped contents, `OSError` propagates.
  - `kubelogin_token(run=subprocess.run) -> str` — runs `kubectl oidc-login get-token` for client `kubernetes` with scopes `email` and `offline_access`, returns `status.token`.
  - `serviceaccount_token(ref: str, run=subprocess.run) -> str` — `ref` is `namespace/name`; runs `kubectl create token <name> -n <ns> --audience kagent --duration 10m`.
  - `acquire_token(*, token=None, token_file=None, as_serviceaccount=None, env=os.environ, run=subprocess.run) -> str` — precedence: `token`, `token_file`, `$KAGENT_TOKEN`, `$KAGENT_TOKEN_FILE`, `as_serviceaccount`, else kubelogin.
  - `DEX_ISSUER = "https://dex.arigsela.com"`, `DEX_CLIENT_ID = "kubernetes"`, `TOKEN_AUDIENCE = "kagent"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/eval-corpus/test_kagent_a2a.py`:

```python
# ---------------------------------------------------------------- tokens (callers as principals)

def test_headers_with_a_token_carry_bearer_and_no_user_id():
    h = a2a.request_headers("someone@example.com", token="abc.def.ghi")
    assert h["Authorization"] == "Bearer abc.def.ghi"
    assert "X-User-Id" not in h


def test_headers_without_a_token_keep_the_user_header():
    h = a2a.request_headers("eval-capture")
    assert h["X-User-Id"] == "eval-capture" and "Authorization" not in h


def test_send_reads_the_token_file_on_every_call(tmp_path):
    f = tmp_path / "token"
    f.write_text("first\n")
    seen = []

    def transport(url, payload, headers, timeout):
        seen.append(headers.get("Authorization"))
        return {"result": {"message": {"role": "agent", "parts": [{"text": "ok"}]}}}

    a2a.send_message("http://x/agents/kagent/a", "q", token_file=str(f), transport=transport)
    f.write_text("second\n")
    a2a.send_message("http://x/agents/kagent/a", "q", token_file=str(f), transport=transport)
    assert seen == ["Bearer first", "Bearer second"]


def test_kubelogin_token_parses_the_exec_credential():
    def run(cmd, **kw):
        assert cmd[:3] == ["kubectl", "oidc-login", "get-token"]
        assert "--oidc-issuer-url=https://dex.arigsela.com" in cmd
        assert "--oidc-client-id=kubernetes" in cmd
        assert "--oidc-extra-scope=email" in cmd
        class R: returncode = 0; stdout = '{"kind":"ExecCredential","status":{"token":"id.tok.en"}}'; stderr = ""
        return R()
    assert a2a.kubelogin_token(run=run) == "id.tok.en"


def test_serviceaccount_token_mints_with_the_kagent_audience():
    def run(cmd, **kw):
        assert cmd == ["kubectl", "create", "token", "dispatcher", "-n", "dispatcher",
                       "--audience", "kagent", "--duration", "10m"]
        class R: returncode = 0; stdout = "sa.tok.en\n"; stderr = ""
        return R()
    assert a2a.serviceaccount_token("dispatcher/dispatcher", run=run) == "sa.tok.en"


def test_serviceaccount_token_rejects_a_bad_ref():
    with pytest.raises(ValueError):
        a2a.serviceaccount_token("dispatcher", run=lambda *a, **k: None)


def test_acquire_token_precedence(tmp_path):
    f = tmp_path / "t"
    f.write_text("from-file")
    assert a2a.acquire_token(token="explicit", token_file=str(f), env={}) == "explicit"
    assert a2a.acquire_token(token_file=str(f), env={"KAGENT_TOKEN": "env"}) == "from-file"
    assert a2a.acquire_token(env={"KAGENT_TOKEN": "env"}) == "env"
    assert a2a.acquire_token(env={"KAGENT_TOKEN_FILE": str(f)}) == "from-file"
    called = []
    def run(cmd, **kw):
        called.append(cmd[:3])
        class R: returncode = 0; stdout = '{"status":{"token":"kl"}}'; stderr = ""
        return R()
    assert a2a.acquire_token(env={}, run=run) == "kl"
    assert called == [["kubectl", "oidc-login", "get-token"]]


def test_acquire_token_reports_a_failed_kubectl():
    def run(cmd, **kw):
        class R: returncode = 1; stdout = ""; stderr = "no browser"
        return R()
    with pytest.raises(RuntimeError, match="no browser"):
        a2a.acquire_token(env={}, run=run)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/eval-corpus/test_kagent_a2a.py -q -k "token or bearer"`
Expected: FAIL — `TypeError: request_headers() got an unexpected keyword argument 'token'` and `AttributeError: module has no attribute 'kubelogin_token'`.

- [ ] **Step 3: Implement**

In `scripts/kagent_a2a.py`, add after the constants block (near `DEFAULT_USER`):

```python
# Callers as principals (docs/superpowers/specs/2026-10-13-callers-as-principals-and-eval-gate-design.md).
# The mesh verifies the bearer token and sets X-User-Id from its claim; a client that sends a
# token must not also name itself. Humans reuse kubectl's Dex login (kubelogin cache);
# automations present a projected service account token with this audience.
DEX_ISSUER = "https://dex.arigsela.com"
DEX_CLIENT_ID = "kubernetes"
TOKEN_AUDIENCE = "kagent"
```

Replace `request_headers`:

```python
def request_headers(user: str | None, token: str | None = None) -> dict:
    headers = {"Content-Type": "application/json", "Accept": "application/json",
               # Not enforced by the alpha11 JSON-RPC handler, but the kagent CLI
               # and the a2a-go client always send it (go/core/internal/utils/a2a_version.go).
               "A2A-Version": A2A_VERSION,
               # The HITL extension is negotiated per request (a2a-go reads this header).
               "A2A-Extensions": HITL_EXTENSION}
    if token:
        headers["Authorization"] = f"Bearer {token}"   # identity = the verified claim (mesh)
    elif user:
        headers["X-User-Id"] = user                     # the no-mesh dev path (port-forward)
    return headers
```

Add the token helpers before `send_message`:

```python
def read_token_file(path: str) -> str:
    return Path(path).read_text().strip()


def _run_kubectl(cmd: list[str], run) -> str:
    r = run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:3])} failed: {(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout


def kubelogin_token(run=subprocess.run) -> str:
    """The Dex ID token kubectl caches (int128/kubelogin). The browser opens only when the
    cached refresh token is gone. The ExecCredential's status.token is the ID token."""
    out = _run_kubectl(["kubectl", "oidc-login", "get-token",
                        f"--oidc-issuer-url={DEX_ISSUER}", f"--oidc-client-id={DEX_CLIENT_ID}",
                        "--oidc-extra-scope=email", "--oidc-extra-scope=offline_access"], run)
    return json.loads(out)["status"]["token"]


def serviceaccount_token(ref: str, run=subprocess.run) -> str:
    """A 10-minute projected token for namespace/name with the kagent audience: an audited
    TokenRequest, which is how the owner continues a session an automation owns."""
    ns, _, name = ref.partition("/")
    if not ns or not name or "/" in name:
        raise ValueError(f"--as-serviceaccount wants namespace/name, got {ref!r}")
    return _run_kubectl(["kubectl", "create", "token", name, "-n", ns,
                         "--audience", TOKEN_AUDIENCE, "--duration", "10m"], run).strip()


def acquire_token(*, token: str | None = None, token_file: str | None = None,
                  as_serviceaccount: str | None = None, env=os.environ, run=subprocess.run) -> str:
    """Explicit token, then a file, then $KAGENT_TOKEN, $KAGENT_TOKEN_FILE, a service account
    to impersonate, else the kubelogin cache."""
    if token:
        return token
    if token_file:
        return read_token_file(token_file)
    if env.get("KAGENT_TOKEN"):
        return env["KAGENT_TOKEN"]
    if env.get("KAGENT_TOKEN_FILE"):
        return read_token_file(env["KAGENT_TOKEN_FILE"])
    if as_serviceaccount:
        return serviceaccount_token(as_serviceaccount, run)
    return kubelogin_token(run)
```

Add `import os`, `import subprocess` and `from pathlib import Path` to the imports if missing. Change `send_message`'s signature and header line:

```python
def send_message(endpoint: str, text: str, *, context_id: str | None = None,
                 task_id: str | None = None, metadata: dict | None = None,
                 user: str | None = DEFAULT_USER, token: str | None = None,
                 token_file: str | None = None, timeout: float = DEFAULT_TIMEOUT,
                 transport=None, sleep=time.sleep, clock=time.monotonic,
                 retry_deadline: float = RETRY_DEADLINE) -> dict:
```

and

```python
    headers = request_headers(user, token or (read_token_file(token_file) if token_file else None))
```

Update the module docstring's identity paragraph: "The conversation belongs to the verified identity: the Dex `email` for a human, `system:serviceaccount:<ns>:<sa>` for an automation. `user` (X-User-Id) is honoured only where no mesh sits in front of the controller (a port-forward)."

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/eval-corpus/ -q`
Expected: PASS, every test in the suite (the existing header test still passes: no token → `X-User-Id`).

- [ ] **Step 5: Commit**

```bash
git add scripts/kagent_a2a.py tests/eval-corpus/test_kagent_a2a.py
git commit -m "feat(a2a): send a bearer token and never a self-declared identity beside it"
```

### Task 2: ask-hk and the capture script authenticate with the kubectl Dex token

**Files:**
- Modify: `scripts/ask-hk` (arguments, token acquisition, docstring)
- Modify: `scripts/capture-eval-answers.py` (`--token-file`, token acquisition)
- Test: `tests/eval-corpus/test_kagent_a2a.py`, `tests/eval-corpus/test_capture_eval_answers.py`

**Interfaces:**
- Consumes: `a2a.acquire_token`, `a2a.send_message(token=...)` from Task 1.
- Produces: `ask-hk --token-file F | --as-serviceaccount NS/NAME | --no-token`; `ask-hk` sends a token by default when the endpoint is the public base. `capture-eval-answers.py --token-file F` with the same default.

- [ ] **Step 1: Write the failing tests**

Append to `tests/eval-corpus/test_kagent_a2a.py` (the file already has `_load_ask_hk()` and a fake transport pattern; reuse them):

```python
def test_ask_hk_sends_the_kubelogin_token_by_default(monkeypatch):
    mod = _load_ask_hk()
    monkeypatch.setattr(mod.a2a, "kubelogin_token", lambda run=None: "kl.tok.en")
    seen = {}

    def transport(url, payload, headers, timeout):
        seen.update(headers)
        return {"result": {"message": {"role": "agent", "parts": [{"text": "hi"}]}}}

    out = io.StringIO()
    rc = mod.main(["hello"], stdout=out, stderr=io.StringIO(), transport=transport)
    assert rc == 0
    assert seen["Authorization"] == "Bearer kl.tok.en" and "X-User-Id" not in seen


def test_ask_hk_as_serviceaccount_mints_a_token(monkeypatch):
    mod = _load_ask_hk()
    monkeypatch.setattr(mod.a2a, "serviceaccount_token", lambda ref, run=None: f"sa:{ref}")
    seen = {}

    def transport(url, payload, headers, timeout):
        seen.update(headers)
        return {"result": {"message": {"role": "agent", "parts": [{"text": "hi"}]}}}

    rc = mod.main(["--as-serviceaccount", "dispatcher/dispatcher", "hello"],
                  stdout=io.StringIO(), stderr=io.StringIO(), transport=transport)
    assert rc == 0 and seen["Authorization"] == "Bearer sa:dispatcher/dispatcher"


def test_ask_hk_no_token_keeps_the_user_header_for_port_forwards():
    mod = _load_ask_hk()
    seen = {}

    def transport(url, payload, headers, timeout):
        seen.update(headers)
        return {"result": {"message": {"role": "agent", "parts": [{"text": "hi"}]}}}

    rc = mod.main(["--no-token", "--endpoint", "http://127.0.0.1:8083", "--user", "dev", "hello"],
                  stdout=io.StringIO(), stderr=io.StringIO(), transport=transport)
    assert rc == 0 and seen["X-User-Id"] == "dev" and "Authorization" not in seen


def test_ask_hk_reports_a_token_failure_before_sending(monkeypatch):
    mod = _load_ask_hk()
    def boom(run=None):
        raise RuntimeError("kubectl oidc-login failed: no browser")
    monkeypatch.setattr(mod.a2a, "kubelogin_token", boom)
    err = io.StringIO()
    rc = mod.main(["hello"], stdout=io.StringIO(), stderr=err,
                  transport=lambda *a: pytest.fail("must not send"))
    assert rc == 1 and "no browser" in err.getvalue()
```

Append to `tests/eval-corpus/test_capture_eval_answers.py` (it loads the script by path already; follow its existing loader name):

```python
def test_capture_sends_the_token_file(tmp_path):
    f = tmp_path / "token"
    f.write_text("cap.tok.en")
    seen = {}

    def transport(url, payload, headers, timeout):
        seen.update(headers)
        return {"result": {"message": {"role": "agent", "parts": [{"text": "answer"}]}}}

    mod = _load()   # the module's existing loader helper
    res = mod.send_message("http://x/agents/kagent/a", "q", transport=transport, token_file=str(f))
    assert mod.extract_text(res) == "answer"
    assert seen["Authorization"] == "Bearer cap.tok.en" and "X-User-Id" not in seen
```

(If the capture test file has no loader helper, add one mirroring `_load_ask_hk` for `capture-eval-answers.py`.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/eval-corpus/ -q -k "ask_hk or capture_sends"`
Expected: FAIL — `unrecognized arguments: --as-serviceaccount` / `--no-token`, and `send_message() got an unexpected keyword argument 'token_file'`.

- [ ] **Step 3: Implement ask-hk**

In `scripts/ask-hk`, add arguments after `--user`:

```python
    tok = ap.add_mutually_exclusive_group()
    tok.add_argument("--token-file", help="bearer token file (a projected service account token)")
    tok.add_argument("--as-serviceaccount", metavar="NS/NAME",
                     help="mint a 10-minute token for this service account (kubectl create token) "
                          "and speak as it: how the owner continues a dispatcher investigation")
    tok.add_argument("--no-token", action="store_true",
                     help="send only X-User-Id (the port-forward path, no mesh in front)")
```

Change the `--user` help to `"X-User-Id for --no-token (default: $KAGENT_USER_ID or %(default)s); ignored when a token is sent"`.

After `url = a2a.resolve_endpoint(...)` and before `pending = load_pending(...)`:

```python
    token = None
    if not args.no_token:
        try:
            token = a2a.acquire_token(token_file=args.token_file,
                                      as_serviceaccount=args.as_serviceaccount)
        except (RuntimeError, OSError, ValueError, KeyError) as err:
            print(f"ask-hk: cannot get a token: {err}\n"
                  "  (kubelogin: brew install int128/kubelogin/kubelogin; "
                  "--no-token for a port-forward)", file=stderr)
            return 1
```

Change the send call:

```python
        reply = a2a.parse_result(a2a.send_message(
            url, text, context_id=args.context, task_id=task_id, metadata=metadata,
            user=None if token else args.user, token=token, timeout=args.timeout,
            transport=transport))
```

Update the docstring paragraph that says the conversation belongs to `--user`: "The conversation belongs to the identity the mesh verified: your Dex email (kubelogin, the default) or the service account named by `--as-serviceaccount`. `--user` matters only with `--no-token`."

- [ ] **Step 4: Implement the capture script**

In `scripts/capture-eval-answers.py`:

```python
def send_message(endpoint: str, question: str, *, transport=None,
                 timeout: float = a2a.DEFAULT_TIMEOUT, user: str | None = DEFAULT_USER,
                 token: str | None = None, token_file: str | None = None) -> dict:
    """One SendMessage in a new session; returns the JSON-RPC result."""
    return a2a.send_message(endpoint, question, user=None if (token or token_file) else user,
                            token=token, token_file=token_file, timeout=timeout, transport=transport)
```

Add arguments `--token-file` and `--no-token` (same help as ask-hk), and in `main` before the loop:

```python
    token = None
    if not args.no_token and not args.token_file:
        token = a2a.acquire_token()          # kubelogin, $KAGENT_TOKEN, $KAGENT_TOKEN_FILE
```

pass `token=token, token_file=args.token_file` into `send_message`. Replace the docstring's `X-User-Id eval-capture` paragraph with: "In the cluster the eval job presents its projected token (`--token-file`) and its sessions belong to `system:serviceaccount:kagent:eval-gate`; from the laptop the kubelogin token is used and sessions belong to you. The `memory` category expects state saved earlier by the *same* identity: seed it with `ask-hk --as-serviceaccount kagent/eval-gate "My favorite color is octarine. Please save that."` for the job, or with plain `ask-hk` for yourself."

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m pytest tests/eval-corpus/ -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/ask-hk scripts/capture-eval-answers.py tests/eval-corpus/
git commit -m "feat(ask-hk): authenticate with the kubectl Dex token, or as a service account"
```

### Task 3: The dispatcher presents its projected token

**Files:**
- Modify: `scripts/dispatch.py` (`Config`, `investigate`, `main` log line)
- Modify: `base-apps/dispatcher/deployment.yaml` (serviceAccountName, projected volume, env)
- Create: `base-apps/dispatcher/serviceaccount.yaml`
- Modify: `base-apps/dispatcher/runbook.md`, `base-apps/dispatcher/docs.md`
- Regenerate: `base-apps/dispatcher/code-configmap.yaml` (and the checksum annotations) with `python3 scripts/gen-dispatcher-configmap.py --repo-root .`
- Test: `tests/dispatch/test_dispatch.py`, `tests/dispatch/test_manifests.py`

**Interfaces:**
- Consumes: `a2a.send_message(token_file=...)`.
- Produces: `Config.token_file: str | None` (env `KAGENT_TOKEN_FILE`, default `/var/run/kagent/token`, `""` disables); the Deployment runs as ServiceAccount `dispatcher`.

- [ ] **Step 1: Write the failing tests**

In `tests/dispatch/test_dispatch.py`, find the fake-agent fixture (`fake.send` near line 137) and add a test beside `test_firing_matched_alert_is_investigated_as_dispatcher_in_a_new_session`:

```python
def test_investigation_sends_the_projected_token_not_a_user(tmp_path):
    f = tmp_path / "token"
    f.write_text("disp.tok.en")
    seen = {}

    def send(url, text, **kw):
        seen.update(kw)
        return {"task": {"id": "t1", "contextId": "c1", "status": {"state": "TASK_STATE_COMPLETED"},
                         "artifacts": [{"parts": [{"text": "looked"}]}]}}

    cfg = dp.Config(table_path="x", a2a_base="http://a", n8n_base="http://n", user="dispatcher",
                    timeout=5, ui_url="u/{session}", token_file=str(f))
    d = dp.Dispatcher(dp.load_table(json.dumps(TABLE)), cfg, send=send, post=lambda *a, **k: None,
                      log=lambda o: None, run_async=False)
    d.handle_grafana({"alerts": [alert()]})
    assert seen["token_file"] == str(f) and seen["user"] is None


def test_config_token_file_default_and_disable():
    assert dp.Config.from_env({}).token_file == "/var/run/kagent/token"
    assert dp.Config.from_env({"KAGENT_TOKEN_FILE": ""}).token_file is None
```

In `tests/dispatch/test_manifests.py` add:

```python
def test_deployment_runs_as_the_dispatcher_service_account_with_a_projected_token():
    deploy = load_one("deployment.yaml", "Deployment")
    pod = deploy["spec"]["template"]["spec"]
    assert pod["serviceAccountName"] == "dispatcher"
    vol = next(v for v in pod["volumes"] if v["name"] == "kagent-token")
    src = vol["projected"]["sources"][0]["serviceAccountToken"]
    assert src["audience"] == "kagent" and src["expirationSeconds"] <= 3600 and src["path"] == "token"
    mount = next(m for m in pod["containers"][0]["volumeMounts"] if m["name"] == "kagent-token")
    assert mount["mountPath"] == "/var/run/kagent" and mount["readOnly"] is True
    env = {e["name"]: e.get("value") for e in pod["containers"][0]["env"]}
    assert "DISPATCH_USER" not in env
    sa = load_one("serviceaccount.yaml", "ServiceAccount")
    assert sa["metadata"]["name"] == "dispatcher" and sa["metadata"]["namespace"] == "dispatcher"
```

(`load_one(file, kind)` is the helper that file already uses to read a manifest; if it is named differently, use that name.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/dispatch/ -q`
Expected: FAIL — `Config.__init__() got an unexpected keyword argument 'token_file'`; `serviceaccount.yaml` not found.

- [ ] **Step 3: Implement dispatch.py**

```python
@dataclass
class Config:
    table_path: str
    a2a_base: str
    n8n_base: str
    user: str
    timeout: float
    ui_url: str
    token_file: str | None = "/var/run/kagent/token"   # projected SA token; "" = none (dev)

    @classmethod
    def from_env(cls, env=os.environ) -> "Config":
        return cls(
            table_path=env.get("DISPATCH_TABLE", "/etc/dispatch/dispatch.json"),
            a2a_base=env.get("KAGENT_A2A_BASE", a2a.IN_CLUSTER_BASE),
            n8n_base=env.get("N8N_WEBHOOK_BASE", "http://n8n.n8n.svc.cluster.local:5678/webhook"),
            user=env.get("DISPATCH_USER", "dispatcher"),
            timeout=float(env.get("INVESTIGATION_TIMEOUT", "300")),
            ui_url=env.get("KAGENT_UI_SESSION_URL", "https://kagent.arigsela.com/agents/{session}/chat"),
            token_file=env.get("KAGENT_TOKEN_FILE", "/var/run/kagent/token") or None,
        )
```

In `investigate`, the send call becomes:

```python
            reply = a2a.parse_result(self.send(url, render_prompt(rule, alert),
                                               user=None if self.config.token_file else self.config.user,
                                               token_file=self.config.token_file,
                                               timeout=self.config.timeout))
```

and the timeout text: `f"the answer lands in its session, owned by the dispatcher's service account; continue it with ask-hk --as-serviceaccount dispatcher/dispatcher --context <id>."` (keep the first sentence). In the env docstring at the top, replace the `DISPATCH_USER` line with `KAGENT_TOKEN_FILE  /var/run/kagent/token (projected SA token, audience kagent; empty = X-User-Id DISPATCH_USER, dev only)`. In `main`'s start log replace `"user": config.user` with `"identity": "token" if config.token_file else config.user`.

- [ ] **Step 4: Manifests**

`base-apps/dispatcher/serviceaccount.yaml`:

```yaml
# base-apps/dispatcher/serviceaccount.yaml
# The dispatcher's identity on the agent platform (V88, V91): its projected token (audience
# kagent) is verified by the mesh, so its investigations are owned by
# system:serviceaccount:dispatcher:dispatcher and nothing else can claim to be it.
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: dispatcher
  namespace: dispatcher
  labels:
    app.kubernetes.io/part-of: agent-guardrails
automountServiceAccountToken: false   # the projected token below is the only one it needs
```

In `deployment.yaml`: add `serviceAccountName: dispatcher` under `spec.template.spec`; delete the `DISPATCH_USER` env entry; add to `volumeMounts`:

```yaml
            - name: kagent-token
              mountPath: /var/run/kagent
              readOnly: true
```

and to `volumes`:

```yaml
        # Projected token, audience kagent, rotated by the kubelet before it expires; the mesh
        # verifies it and sets X-User-Id to this service account (V91, V92). Read on every
        # send, never cached.
        - name: kagent-token
          projected:
            sources:
              - serviceAccountToken:
                  audience: kagent
                  expirationSeconds: 3600
                  path: token
```

Then `python3 scripts/gen-dispatcher-configmap.py --repo-root .` (rewrites `code-configmap.yaml` and both checksum annotations).

- [ ] **Step 5: Docs**

`base-apps/dispatcher/docs.md`: in the identity bullet, replace "sends A2A as `X-User-Id: dispatcher`" with "presents its projected service account token; the mesh verifies it and the investigation is owned by `system:serviceaccount:dispatcher:dispatcher`". `runbook.md`: replace the "continue the session as the dispatcher" command with `scripts/ask-hk --as-serviceaccount dispatcher/dispatcher --agent kagent/k8s-reader --context <id> "<follow-up>"`.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python3 -m pytest tests/dispatch/ -q && python3 scripts/gen-dispatcher-configmap.py --repo-root . --check && yamllint -c .yamllint.yaml base-apps/dispatcher/`
Expected: all pass, generator in sync, yamllint clean.

- [ ] **Step 7: Commit, PR, merge, verify live**

```bash
git add scripts/dispatch.py base-apps/dispatcher/ tests/dispatch/
git commit -m "feat(dispatcher): present a projected service account token on every investigation"
```

Open the PR (it is `review:read`: the dispatcher is deny-listed). After merge and sync: `kubectl -n dispatcher get pod -o jsonpath='{.items[0].spec.serviceAccountName}'` → `dispatcher`; the start log line shows `"identity": "token"`; replay a `Pod CPU starved` alert (runbook command) and confirm a finding reaches Slack. In `insecure` mode without the mesh the token is ignored and the session owner is still the controller default; that changes in Task 6.

### Task 4: Enrol the namespaces, the waypoint, and verification in observe mode

**Files:**
- Modify: `base-apps/kagent-secrets.yaml` (`managedNamespaceMetadata` labels), `base-apps/backstage.yaml` (same), `base-apps/dispatcher/namespace.yaml` (label)
- Create: `base-apps/kagent/waypoint.yaml`, `base-apps/kagent/request-authentication.yaml`, `base-apps/kagent/authorization-policy.yaml` (observe form), `base-apps/cluster-rbac/service-account-issuer-discovery.yaml`
- Modify: `base-apps/kagent.yaml` (comment on `controller.auth.mode`)
- Create: `tests/identity/test_mesh.py`, `tests/identity/conftest.py`
- Modify: `.github/workflows/validate.yaml` (job `identity-validate`)

**Interfaces:**
- Produces: the waypoint `kagent-waypoint`; `RequestAuthentication kagent-callers` with the two issuers; `AuthorizationPolicy kagent-controller` (observe: one `ALLOW` rule matching everything, so the policy exists and the waypoint logs); `tests/identity/` helpers `load(path) -> list[dict]`, `one(path, kind, name) -> dict`.

- [ ] **Step 1: Write the failing tests**

`tests/identity/conftest.py`:

```python
# tests/identity/conftest.py
from pathlib import Path
import yaml
import pytest

REPO = Path(__file__).resolve().parents[2]


def load(rel: str) -> list[dict]:
    return [d for d in yaml.safe_load_all((REPO / rel).read_text()) if d]


def one(rel: str, kind: str, name: str) -> dict:
    docs = [d for d in load(rel) if d.get("kind") == kind and d["metadata"]["name"] == name]
    assert len(docs) == 1, f"{rel}: expected one {kind}/{name}, found {len(docs)}"
    return docs[0]


@pytest.fixture
def repo() -> Path:
    return REPO
```

`tests/identity/test_mesh.py`:

```python
# tests/identity/test_mesh.py
"""Callers as principals (V91, V92): the mesh verifies every caller's token and sets the
identity header; the policy files say so and name exactly the two issuers."""
from conftest import load, one, REPO
import yaml

DEX = "https://dex.arigsela.com"
K8S = "https://kubernetes.default.svc.cluster.local"


def test_kagent_dispatcher_and_backstage_are_in_the_ambient_mesh():
    for app in ("base-apps/kagent-secrets.yaml", "base-apps/backstage.yaml"):
        spec = load(app)[0]["spec"]
        labels = spec["syncPolicy"]["managedNamespaceMetadata"]["labels"]
        assert labels["istio.io/dataplane-mode"] == "ambient", app
        assert "CreateNamespace=true" in spec["syncPolicy"]["syncOptions"], app
    ns = one("base-apps/dispatcher/namespace.yaml", "Namespace", "dispatcher")
    assert ns["metadata"]["labels"]["istio.io/dataplane-mode"] == "ambient"
    kagent = load("base-apps/kagent-secrets.yaml")[0]["spec"]["syncPolicy"]["managedNamespaceMetadata"]["labels"]
    assert kagent["istio.io/use-waypoint"] == "kagent-waypoint"


def test_waypoint_exists_for_services():
    wp = one("base-apps/kagent/waypoint.yaml", "Gateway", "kagent-waypoint")
    assert wp["spec"]["gatewayClassName"] == "istio-waypoint"
    assert wp["metadata"]["labels"]["istio.io/waypoint-for"] == "service"
    assert wp["spec"]["listeners"][0]["protocol"] == "HBONE"


def test_request_authentication_names_exactly_the_two_issuers():
    ra = one("base-apps/kagent/request-authentication.yaml", "RequestAuthentication", "kagent-callers")
    assert ra["spec"]["targetRefs"] == [{"group": "gateway.networking.k8s.io", "kind": "Gateway",
                                        "name": "kagent-waypoint"}]
    rules = {r["issuer"]: r for r in ra["spec"]["jwtRules"]}
    assert set(rules) == {DEX, K8S}
    assert rules[DEX]["audiences"] == ["kubernetes"] and rules[DEX]["jwksUri"] == f"{DEX}/keys"
    assert rules[K8S]["audiences"] == ["kagent"]
    assert "jwksUri" in rules[K8S] or "jwks" in rules[K8S]


def test_request_authentication_outputs_claim_header():
    ra = one("base-apps/kagent/request-authentication.yaml", "RequestAuthentication", "kagent-callers")
    out = {r["issuer"]: r["outputClaimToHeaders"] for r in ra["spec"]["jwtRules"]}
    assert out[DEX] == [{"header": "x-user-id", "claim": "email"}]
    assert out[K8S] == [{"header": "x-user-id", "claim": "sub"}]
    assert all(r["forwardOriginalToken"] is True for r in ra["spec"]["jwtRules"])


def test_authorization_policy_targets_the_waypoint():
    ap = one("base-apps/kagent/authorization-policy.yaml", "AuthorizationPolicy", "kagent-controller")
    assert ap["spec"]["targetRefs"][0]["name"] == "kagent-waypoint"
    assert ap["spec"]["action"] == "ALLOW"


def test_issuer_discovery_is_readable_by_istiod():
    crb = one("base-apps/cluster-rbac/service-account-issuer-discovery.yaml", "ClusterRoleBinding",
              "service-account-issuer-discovery-unauthenticated")
    assert crb["roleRef"]["name"] == "system:service-account-issuer-discovery"
    assert {s["name"] for s in crb["subjects"]} == {"system:unauthenticated"}


def test_controller_auth_mode_is_insecure_on_purpose():
    text = (REPO / "base-apps/kagent.yaml").read_text()
    assert "mode: insecure" in text and "the mesh is the identity authority" in text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/identity/ -q`
Expected: FAIL — `KeyError: 'managedNamespaceMetadata'`, files not found.

- [ ] **Step 3: Namespace enrolment**

`base-apps/kagent-secrets.yaml`, under `syncPolicy:` add:

```yaml
    # Callers as principals (V91): the namespace joins the ambient mesh and every Service in it
    # is fronted by the waypoint in base-apps/kagent/waypoint.yaml. managedNamespaceMetadata
    # labels the namespace Argo created without owning it (no Namespace manifest, so a deleted
    # Application can never delete the namespace).
    managedNamespaceMetadata:
      labels:
        istio.io/dataplane-mode: ambient
        istio.io/use-waypoint: kagent-waypoint
```

`base-apps/backstage.yaml`: the same block with only `istio.io/dataplane-mode: ambient` and the comment "Backstage calls the kagent controller; in the mesh its calls carry its workload principal (ztunnel mTLS) and traverse the kagent waypoint". `base-apps/dispatcher/namespace.yaml`: add `istio.io/dataplane-mode: ambient` to the labels with the comment "in the mesh, so the dispatcher's calls reach the controller through the waypoint (V91)".

- [ ] **Step 4: The waypoint, the issuers, the observe policy, the discovery binding**

`base-apps/kagent/waypoint.yaml`:

```yaml
# base-apps/kagent/waypoint.yaml
# The L7 proxy every Service in `kagent` is fronted by (namespace label istio.io/use-waypoint
# in base-apps/kagent-secrets.yaml). It is where callers of the controller are authenticated:
# request-authentication.yaml verifies tokens, authorization-policy.yaml decides who may call
# what. Spec: docs/superpowers/specs/2026-10-13-callers-as-principals-and-eval-gate-design.md.
# One replica (Istio renders the Deployment); a dead waypoint makes the controller unreachable
# from everything, including the UI, which is the safe direction. Revert the enrolment label
# to bypass it in an emergency (runbook).
---
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: kagent-waypoint
  namespace: kagent
  labels:
    istio.io/waypoint-for: service
spec:
  gatewayClassName: istio-waypoint
  listeners:
    - name: mesh
      port: 15008
      protocol: HBONE
```

`base-apps/kagent/request-authentication.yaml`:

```yaml
# base-apps/kagent/request-authentication.yaml
# Who a caller is (V91, V92). Two issuers, nothing else:
#   * Dex, client `kubernetes` (the token kubectl already caches with kubelogin): a human; the
#     identity is the verified `email` claim.
#   * the Kubernetes service account issuer, audience `kagent` (projected tokens, <= 1 h): an
#     automation; the identity is `sub` = system:serviceaccount:<ns>:<name>.
# The verified claim is WRITTEN into x-user-id, replacing anything the client sent, which is
# what the controller (insecure mode) reads. A request with no token passes this object (it only
# rejects invalid tokens) and is then refused by authorization-policy.yaml unless it comes from
# the UI or the worker principal.
---
apiVersion: security.istio.io/v1
kind: RequestAuthentication
metadata:
  name: kagent-callers
  namespace: kagent
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: Gateway
      name: kagent-waypoint
  jwtRules:
    - issuer: https://dex.arigsela.com
      jwksUri: https://dex.arigsela.com/keys
      audiences: ["kubernetes"]
      forwardOriginalToken: true
      outputClaimToHeaders:
        - header: x-user-id
          claim: email
    - issuer: https://kubernetes.default.svc.cluster.local
      # istiod fetches this with the API server's CA. If the istiod log says
      # "Jwks remote fetch is failed" for this issuer, replace jwksUri with an inline `jwks:`
      # from `kubectl get --raw /openid/v1/jwks` (public keys; safe in git) and note it in SPEC.
      jwksUri: https://kubernetes.default.svc/openid/v1/jwks
      audiences: ["kagent"]
      forwardOriginalToken: true
      outputClaimToHeaders:
        - header: x-user-id
          claim: sub
```

`base-apps/kagent/authorization-policy.yaml` (observe form; Task 6 replaces the rules):

```yaml
# base-apps/kagent/authorization-policy.yaml
# OBSERVE MODE (Task 4 of docs/superpowers/plans/2026-10-13-callers-as-principals.md): one rule
# that matches everything, so the policy exists, the waypoint logs every call with its source
# principal and path, and nothing is refused yet. Task 6 replaces the rules with the real ones.
---
apiVersion: security.istio.io/v1
kind: AuthorizationPolicy
metadata:
  name: kagent-controller
  namespace: kagent
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: Gateway
      name: kagent-waypoint
  action: ALLOW
  rules:
    - {}
```

`base-apps/cluster-rbac/service-account-issuer-discovery.yaml`:

```yaml
# base-apps/cluster-rbac/service-account-issuer-discovery.yaml
# Lets istiod (and any unauthenticated client) read /.well-known/openid-configuration and
# /openid/v1/jwks: the PUBLIC keys that verify projected service account tokens. k3s binds this
# ClusterRole to system:authenticated only; the waypoint's RequestAuthentication
# (base-apps/kagent/request-authentication.yaml) names the JWKS URL, and istiod fetches it
# without credentials. Public keys only; nothing here grants anything else.
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: service-account-issuer-discovery-unauthenticated
  labels:
    app.kubernetes.io/name: cluster-rbac
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: system:service-account-issuer-discovery
subjects:
  - apiGroup: rbac.authorization.k8s.io
    kind: Group
    name: system:unauthenticated
```

`base-apps/kagent.yaml`: under `controller:` add

```yaml
          # Callers as principals (V91): the mesh is the identity authority. The waypoint in
          # base-apps/kagent/ verifies every caller's token and sets x-user-id from the claim;
          # the controller reads that header, so `insecure` here is exact, not an omission.
          # `trusted-proxy` would need every caller, the UI included, to carry a JWT: that is
          # the UI-login follow-up (oauth2-proxy + Dex).
          auth:
            mode: insecure
```

- [ ] **Step 5: CI job**

In `.github/workflows/validate.yaml`, after `promotion-validate`:

```yaml
  identity-validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4
      - name: Set up Python
        uses: actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065 # v5
        with:
          python-version: "3.12"
      - name: Install deps
        run: pip install pyyaml==6.0.2 pytest==8.3.3
      - name: Run identity tests
        run: python -m pytest tests/identity/ -q
```

Add `tests/identity` to the `AGENTS.md` suite table ("none" extra needs).

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python3 -m pytest tests/identity/ -q && yamllint -c .yamllint.yaml base-apps/kagent/waypoint.yaml base-apps/kagent/request-authentication.yaml base-apps/kagent/authorization-policy.yaml base-apps/cluster-rbac/service-account-issuer-discovery.yaml base-apps/kagent-secrets.yaml base-apps/backstage.yaml base-apps/dispatcher/namespace.yaml && kubeconform -summary -strict -ignore-missing-schemas -schema-location default -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' -kubernetes-version 1.33.0 base-apps/kagent/waypoint.yaml base-apps/kagent/request-authentication.yaml base-apps/kagent/authorization-policy.yaml base-apps/cluster-rbac/service-account-issuer-discovery.yaml`
Expected: tests pass, yamllint clean, kubeconform valid.

- [ ] **Step 7: Commit, PR, merge, and the live observe checks (the spike's criteria 1, 3, 4)**

```bash
git add base-apps/kagent-secrets.yaml base-apps/backstage.yaml base-apps/dispatcher/namespace.yaml base-apps/kagent/waypoint.yaml base-apps/kagent/request-authentication.yaml base-apps/kagent/authorization-policy.yaml base-apps/cluster-rbac/service-account-issuer-discovery.yaml base-apps/kagent.yaml tests/identity/ .github/workflows/validate.yaml AGENTS.md
git commit -m "feat(identity): kagent, dispatcher and backstage join the ambient mesh; waypoint and issuers in observe mode"
```

After the merge and sync, in order, each with its expected output; a failure stops the plan with a ruling in the ledger:

1. `kubectl -n kagent get gateway kagent-waypoint` → `PROGRAMMED True`; `kubectl -n kagent get deploy kagent-waypoint` → 1/1.
2. `kubectl get ns kagent dispatcher backstage -L istio.io/dataplane-mode` → `ambient` on all three.
3. **Criterion 4 (Substrate survives capture):** `scripts/ask-hk "Which namespace runs Vault, and what unseals it?"` completes with a cited answer; `kubectl -n kagent logs deploy/kagent-controller --since=5m | grep -ci error` does not grow; `kubectl -n ate-system logs deploy/ate-controller --since=5m | grep -i "actor" | tail -3` shows a snapshot/resume, not a network error. Then `scripts/ask-hk --context <id> "And what backs its storage?"` resumes the actor.
4. **Criterion 1 (ingress → waypoint):** `kubectl -n kagent logs deploy/kagent-waypoint --since=5m | grep agents/kagent` shows the two requests above with `"outbound_.*kagent-controller"` and a source of the ingress gateway.
5. **Criterion 3 (JWKS):** `kubectl -n istio-system logs deploy/istiod --since=10m | grep -i jwks` shows no `fetch is failed` for either issuer. If the Kubernetes issuer fails: ruling, switch to inline `jwks` (`kubectl get --raw /openid/v1/jwks`), commit, re-check. Then `scripts/ask-hk --as-serviceaccount dispatcher/dispatcher "ping"` works (token verified; observe policy allows), and `curl -s -o /dev/null -w '%{http_code}' -H 'Authorization: Bearer not.a.jwt' https://kagent-mcp.arigsela.com/agents/kagent/homelab-knowledge -X POST -d '{}'` → `401` (an invalid token is refused even in observe mode).
6. The dispatcher runbook replay produces a finding in Slack; `kubectl -n kagent logs deploy/kagent-waypoint --since=5m | grep dispatcher` shows `spiffe://cluster.local/ns/dispatcher/sa/dispatcher` as the source principal.
7. Backstage: open the portal's agents page; it lists the seven agents. Waypoint log shows `ns/backstage/sa/`.
8. The UI chats (one message in `kagent.arigsela.com`). Waypoint log shows `ns/kagent/sa/kagent-ui`.
9. Record in the ledger every distinct `(source principal, path prefix)` pair from `kubectl -n kagent logs deploy/kagent-waypoint --since=30m`; this is the input to Task 6's runtime rule.

### Task 5: The ingress gateway verifies tokens for kagent-mcp, and the UI route strips identity headers

**Files:**
- Create: `base-apps/istio-ingress/request-authentication.yaml`
- Modify: `base-apps/istio-ingress/authorizationpolicy.yaml` (the kagent-mcp rule)
- Modify: `base-apps/kagent/httproute.yaml` (filter)
- Test: `tests/identity/test_gateway.py`, `tests/wan_ip/` must stay green

**Interfaces:**
- Consumes: nothing from other tasks (independent of the waypoint).
- Produces: `RequestAuthentication gateway-kagent-callers` in `istio-ingress`; the kagent-mcp rule requires `requestPrincipals`.

- [ ] **Step 1: Write the failing tests**

`tests/identity/test_gateway.py`:

```python
# tests/identity/test_gateway.py
"""The external path is checked at the ingress too (V91): kagent-mcp needs a verified token in
addition to the allow-list, and the UI host cannot carry an identity header through."""
from conftest import load, one

DEX = "https://dex.arigsela.com"
K8S = "https://kubernetes.default.svc.cluster.local"


def test_gateway_request_authentication_names_the_two_issuers():
    ra = one("base-apps/istio-ingress/request-authentication.yaml", "RequestAuthentication",
             "gateway-kagent-callers")
    assert ra["spec"]["targetRefs"][0] == {"group": "gateway.networking.k8s.io", "kind": "Gateway", "name": "main"}
    assert {r["issuer"] for r in ra["spec"]["jwtRules"]} == {DEX, K8S}
    assert all(r["forwardOriginalToken"] is True for r in ra["spec"]["jwtRules"])


def test_kagent_mcp_rule_requires_a_token_inside_the_allow_list_source():
    ap = one("base-apps/istio-ingress/authorizationpolicy.yaml", "AuthorizationPolicy", "gateway-allow")
    rules = [r for r in ap["spec"]["rules"]
             if any("kagent-mcp.arigsela.com" in h for t in r.get("to", []) for h in t["operation"].get("hosts", []))]
    assert len(rules) == 1, "exactly one rule for kagent-mcp; a second ALLOW rule would OR away the token"
    src = rules[0]["from"][0]["source"]
    assert src["ipBlocks"] and src["requestPrincipals"] == ["*"]
    assert len(ap["spec"]["rules"]) == len([r for r in ap["spec"]["rules"]]), "ALLOW policy, nothing removed"


def test_ui_route_strips_identity_headers():
    route = one("base-apps/kagent/httproute.yaml", "HTTPRoute", "kagent")
    filters = route["spec"]["rules"][0]["filters"]
    mod = next(f for f in filters if f["type"] == "RequestHeaderModifier")["requestHeaderModifier"]
    assert {h.lower() for h in mod["remove"]} == {"x-user-id", "x-agent-name", "authorization"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/identity/test_gateway.py -q`
Expected: FAIL — file not found; `KeyError: 'requestPrincipals'`; `KeyError: 'filters'`.

- [ ] **Step 3: Implement**

`base-apps/istio-ingress/request-authentication.yaml`:

```yaml
# base-apps/istio-ingress/request-authentication.yaml
# Token verification at the edge for the agent API host (V91). Same two issuers as the kagent
# waypoint (base-apps/kagent/request-authentication.yaml): Dex for humans, the Kubernetes
# service account issuer for automations. This object only rejects INVALID tokens; the
# kagent-mcp rule in authorizationpolicy.yaml is what requires one. The token is forwarded, so
# the waypoint verifies it a second time on the way to the controller: two independent checks
# on the external path. Other hosts are untouched: a request to them with no token passes here
# and is judged by their own allow-list rules as before.
---
apiVersion: security.istio.io/v1
kind: RequestAuthentication
metadata:
  name: gateway-kagent-callers
  namespace: istio-ingress
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: Gateway
      name: main
  jwtRules:
    - issuer: https://dex.arigsela.com
      jwksUri: https://dex.arigsela.com/keys
      audiences: ["kubernetes"]
      forwardOriginalToken: true
    - issuer: https://kubernetes.default.svc.cluster.local
      jwksUri: https://kubernetes.default.svc/openid/v1/jwks
      audiences: ["kagent"]
      forwardOriginalToken: true
```

In `authorizationpolicy.yaml`, the kagent-mcp rule becomes:

```yaml
    # kagent-mcp - restricted. Basic auth dropped (T52). Since 2026-10-13 the allow-list is
    # ANDed with a verified token (request-authentication.yaml; V91): both conditions sit in
    # the same `source`, which Istio ANDs. Never add a second rule for this host: ALLOW rules
    # are ORed and a token-less one would reopen the door.
    - to:
        - operation:
            hosts:
              - kagent-mcp.arigsela.com
              - kagent-mcp.arigsela.com:*
      from:
        - source:
            requestPrincipals: ["*"]
            ipBlocks:
              - 76.97.4.210/32
              - 170.85.56.189/32
              - 170.85.130.202/32
              - 104.28.177.82/32
```

(Keep the four addresses exactly as they are in the file at the time; the WAN one is the annotation's value.)

In `base-apps/kagent/httproute.yaml`, the rule gains:

```yaml
      filters:
        # A browser user on the allow-list cannot smuggle an identity through the UI's nginx
        # (V91): the UI is one identity, the controller default, and that is decided here.
        - type: RequestHeaderModifier
          requestHeaderModifier:
            remove: ["X-User-Id", "X-Agent-Name", "Authorization"]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/identity/ tests/wan_ip/ -q && yamllint -c .yamllint.yaml base-apps/istio-ingress/ base-apps/kagent/httproute.yaml`
Expected: PASS; wan_ip's annotation test still finds the WAN /32 in the rule.

- [ ] **Step 5: Commit, PR (review:read: istio-ingress is deny-listed), merge, verify**

```bash
git add base-apps/istio-ingress/request-authentication.yaml base-apps/istio-ingress/authorizationpolicy.yaml base-apps/kagent/httproute.yaml tests/identity/test_gateway.py
git commit -m "feat(identity): the ingress requires a verified token for kagent-mcp; the UI route strips identity headers"
```

Live: `scripts/ask-hk "ping"` → answer (token accepted at the edge); `curl -s -o /dev/null -w '%{http_code}' -X POST https://kagent-mcp.arigsela.com/agents/kagent/homelab-knowledge -d '{}'` from the LAN → `403` (allow-listed IP, no token: refused); `scripts/ask-hk --no-token --endpoint https://kagent-mcp.arigsela.com "ping"` → exits 1 with the 403. The UI still chats. Backstage, in-cluster, is unaffected by this task.

### Task 6: Enforce on the waypoint, STRICT peer authentication, the NetworkPolicy

**Files:**
- Modify: `base-apps/kagent/authorization-policy.yaml` (real rules)
- Create: `base-apps/kagent/peer-authentication.yaml`, `base-apps/kagent/network-policy-controller.yaml`, `base-apps/kagent/httproute-waypoint.yaml`
- Modify: `tests/identity/test_mesh.py`
- Modify: `base-apps/kagent/docs.md`, `base-apps/kagent/runbook.md`

**Interfaces:**
- Consumes: the `(source principal, path)` pairs recorded in Task 4 step 7.9.
- Produces: the enforced policy; the dated Backstage exception row.

- [ ] **Step 1: Write the failing tests**

Replace `test_authorization_policy_targets_the_waypoint` in `tests/identity/test_mesh.py` with:

```python
def _policy():
    return one("base-apps/kagent/authorization-policy.yaml", "AuthorizationPolicy", "kagent-controller")


def _rules_with(ap, key):
    return [r for r in ap["spec"]["rules"] if key in r]


def test_authorization_policy_is_allow_on_the_waypoint_with_no_match_all_rule():
    ap = _policy()
    assert ap["spec"]["targetRefs"][0]["name"] == "kagent-waypoint" and ap["spec"]["action"] == "ALLOW"
    assert {} not in ap["spec"]["rules"], "the observe-mode rule must be gone"


def test_user_paths_need_a_verified_principal():
    ap = _policy()
    user_rules = [r for r in ap["spec"]["rules"]
                  if any("/agents/*" in t["operation"].get("paths", []) for t in r.get("to", []))]
    assert user_rules, "a rule for /agents/* is required"
    for r in user_rules:
        src = r["from"][0]["source"]
        assert src.get("requestPrincipals") or src.get("principals") == ["cluster.local/ns/kagent/sa/kagent-ui"] \
            or src.get("principals") == ["cluster.local/ns/backstage/sa/default"], r


def test_dex_rule_requires_email_claim():
    ap = _policy()
    dex = [r for r in ap["spec"]["rules"]
           if r.get("from") and r["from"][0]["source"].get("requestPrincipals") == ["https://dex.arigsela.com/*"]]
    assert dex and any(w["key"] == "request.auth.claims[email]" and w["values"] == ["*"] for w in dex[0]["when"])


def test_agent_name_header_only_from_the_workers():
    ap = _policy()
    deny = one("base-apps/kagent/authorization-policy.yaml", "AuthorizationPolicy", "kagent-controller-deny-agent-header")
    assert deny["spec"]["action"] == "DENY"
    rule = deny["spec"]["rules"][0]
    assert rule["when"][0] == {"key": "request.headers[x-agent-name]", "values": ["*"]}
    assert rule["from"][0]["source"]["notPrincipals"] == ["cluster.local/ns/kagent/sa/default"]


def test_backstage_exception_is_dated():
    text = (REPO / "base-apps/kagent/authorization-policy.yaml").read_text()
    if "ns/backstage/sa/default" in text:
        assert "EXCEPTION until" in text and "arigsela/backstage" in text


def test_peer_authentication_is_strict():
    pa = one("base-apps/kagent/peer-authentication.yaml", "PeerAuthentication", "default")
    assert pa["spec"]["mtls"]["mode"] == "STRICT" and "selector" not in pa["spec"]


def test_network_policy_limits_controller_ingress():
    np = one("base-apps/kagent/network-policy-controller.yaml", "NetworkPolicy", "kagent-controller-ingress")
    assert np["spec"]["podSelector"]["matchLabels"] == {"app.kubernetes.io/name": "kagent", "app.kubernetes.io/component": "controller"}
    allowed = {p["namespaceSelector"]["matchLabels"]["kubernetes.io/metadata.name"]
               for rule in np["spec"]["ingress"] for p in rule["from"]}
    assert allowed == {"istio-ingress", "istio-system", "kagent", "dispatcher", "backstage"}


def test_waypoint_route_refuses_user_id_query():
    route = one("base-apps/kagent/httproute-waypoint.yaml", "HTTPRoute", "kagent-controller-waypoint")
    assert route["spec"]["parentRefs"][0] == {"group": "", "kind": "Service", "name": "kagent-controller"}
    first = route["spec"]["rules"][0]
    assert first["matches"][0]["queryParams"] == [{"type": "RegularExpression", "name": "user_id", "value": ".*"}]
    assert first["backendRefs"][0]["name"] == "refused"      # no such Service: a 5xx, never the controller
    assert route["spec"]["rules"][-1]["backendRefs"][0]["name"] == "kagent-controller"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/identity/ -q`
Expected: FAIL — `{}` still in the rules; files not found.

- [ ] **Step 3: Implement**

`base-apps/kagent/authorization-policy.yaml` (replace the observe form). Paths for the worker rule come from Task 4's ledger row; the ones below are the expected set and are corrected from the log before commit:

```yaml
# base-apps/kagent/authorization-policy.yaml
# Who may call the controller, through the waypoint (V91). ALLOW semantics: anything not listed
# is refused. Identity was set by request-authentication.yaml.
#   1. The user paths with a Dex token that carries an email (a human).
#   2. The user paths with a Kubernetes service account token (an automation).
#   3. The user paths from the UI's own workload principal, no token: the UI is one identity,
#      the controller default; httproute.yaml strips any header a browser user sends.
#   4. The runtime paths from the worker principal (Substrate workers run as `default` in this
#      namespace): sub-agent A2A, task updates, push notifications.
#   5. EXCEPTION until arigsela/backstage sends its projected token (plugin PR): Backstage's
#      workload principal on the user paths without a JWT. Remove this rule in that PR's wake.
# The DENY policy below refuses the runtime's X-Agent-Name header from anyone but the workers.
---
apiVersion: security.istio.io/v1
kind: AuthorizationPolicy
metadata:
  name: kagent-controller
  namespace: kagent
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: Gateway
      name: kagent-waypoint
  action: ALLOW
  rules:
    - from:
        - source:
            requestPrincipals: ["https://dex.arigsela.com/*"]
      to:
        - operation:
            paths: ["/agents/*", "/mcp", "/mcp/*", "/api/*"]
      when:
        - key: request.auth.claims[email]
          values: ["*"]
    - from:
        - source:
            requestPrincipals: ["https://kubernetes.default.svc.cluster.local/*"]
      to:
        - operation:
            paths: ["/agents/*", "/mcp", "/mcp/*", "/api/*"]
    - from:
        - source:
            principals: ["cluster.local/ns/kagent/sa/kagent-ui"]
      to:
        - operation:
            paths: ["/agents/*", "/mcp", "/mcp/*", "/api/*"]
    - from:
        - source:
            principals: ["cluster.local/ns/kagent/sa/default"]
      to:
        - operation:
            paths: ["/agents/*", "/api/*", "/a2a/*"]     # corrected from the Task 4 ledger row
    - from:
        - source:
            principals: ["cluster.local/ns/backstage/sa/default"]   # EXCEPTION until arigsela/backstage#<n>
      to:
        - operation:
            paths: ["/agents/*", "/api/*"]
---
apiVersion: security.istio.io/v1
kind: AuthorizationPolicy
metadata:
  name: kagent-controller-deny-agent-header
  namespace: kagent
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: Gateway
      name: kagent-waypoint
  action: DENY
  rules:
    - from:
        - source:
            notPrincipals: ["cluster.local/ns/kagent/sa/default"]
      when:
        - key: request.headers[x-agent-name]
          values: ["*"]
```

(Backstage's pod runs as the namespace `default` service account unless `base-apps/backstage/deployments.yaml` sets one; check `kubectl -n backstage get pod -o jsonpath='{.items[0].spec.serviceAccountName}'` and use that name.)

`base-apps/kagent/peer-authentication.yaml`:

```yaml
# base-apps/kagent/peer-authentication.yaml
# Nothing outside the mesh talks to anything in `kagent` (V91): plaintext from an unenrolled
# namespace is refused by ztunnel, so the only way to the controller is through the waypoint.
---
apiVersion: security.istio.io/v1
kind: PeerAuthentication
metadata:
  name: default
  namespace: kagent
spec:
  mtls:
    mode: STRICT
```

`base-apps/kagent/network-policy-controller.yaml`:

```yaml
# base-apps/kagent/network-policy-controller.yaml
# Belt and braces under the mesh policy (upstream kagent's own advice): only these namespaces
# may open a connection to the controller pods at all. istio-system carries ztunnel/HBONE.
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: kagent-controller-ingress
  namespace: kagent
spec:
  podSelector:
    matchLabels:
      app.kubernetes.io/name: kagent
      app.kubernetes.io/component: controller
  policyTypes: ["Ingress"]
  ingress:
    - from:
        - namespaceSelector:
            matchLabels: {kubernetes.io/metadata.name: istio-ingress}
        - namespaceSelector:
            matchLabels: {kubernetes.io/metadata.name: istio-system}
        - namespaceSelector:
            matchLabels: {kubernetes.io/metadata.name: kagent}
        - namespaceSelector:
            matchLabels: {kubernetes.io/metadata.name: dispatcher}
        - namespaceSelector:
            matchLabels: {kubernetes.io/metadata.name: backstage}
```

(Confirm the controller pod labels first: `kubectl -n kagent get pod -l app.kubernetes.io/component=controller --show-labels`; use the labels the chart renders.)

`base-apps/kagent/httproute-waypoint.yaml`:

```yaml
# base-apps/kagent/httproute-waypoint.yaml
# Attached to the controller Service, so the waypoint applies it to every caller. In insecure
# mode the controller reads `?user_id=` BEFORE the x-user-id header the mesh set, so a token
# holder could name someone else in the query string. No such request reaches the controller:
# it is routed to a Service that does not exist and gets a 5xx. The last rule is the normal path.
---
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: kagent-controller-waypoint
  namespace: kagent
spec:
  parentRefs:
    - group: ""
      kind: Service
      name: kagent-controller
  rules:
    - matches:
        - queryParams:
            - type: RegularExpression
              name: user_id
              value: ".*"
      backendRefs:
        - name: refused
          port: 1
    - backendRefs:
        - name: kagent-controller
          port: 8083
```

Docs: `base-apps/kagent/docs.md` "Identity and access" paragraph is rewritten: who the two issuers are, that the controller stays insecure because the mesh sets the header, the UI's identity, the dispatcher's, how to call with `ask-hk` (kubelogin) and `--as-serviceaccount`, and the residual risk (a pod inside `kagent` can reach the controller as the runtime does). `runbook.md` gains "Agents unreachable after an identity change": check the waypoint pod, istiod's jwks lines, `kubectl -n kagent logs deploy/kagent-waypoint | grep 'RBAC: access denied'`, and the emergency bypass (revert the enrolment PR).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/identity/ -q && yamllint -c .yamllint.yaml base-apps/kagent/ && kubeconform -summary -strict -ignore-missing-schemas -schema-location default -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' -kubernetes-version 1.33.0 base-apps/kagent/*.yaml`
Expected: PASS, clean, valid.

- [ ] **Step 5: Commit, PR, merge, acceptance (the spec's a–f, plus the query check)**

```bash
git add base-apps/kagent/ tests/identity/test_mesh.py
git commit -m "feat(identity): enforce verified identity on the kagent waypoint; STRICT mTLS; controller NetworkPolicy"
```

After sync, in order; any failure → revert this PR (`gh pr revert` or `git revert` + PR), ledger the finding, stop:

- (a) `scripts/ask-hk "Which namespace runs Vault?"` answers; then `kubectl -n kagent exec deploy/kagent-controller -- true` is not needed: read the owner with `scripts/agent-audit.py`? No: `kubectl -n postgresql exec postgresql-cluster-1 -- psql -U postgres -d kagent_v1 -c "select user_id, created_at from kagent.session order by created_at desc limit 3"` → the newest row's `user_id` is the owner's GitHub email.
- (b) **Header overwrite (criterion 2):** `TOKEN=$(kubectl oidc-login get-token --oidc-issuer-url=https://dex.arigsela.com --oidc-client-id=kubernetes --oidc-extra-scope=email | python3 -c 'import json,sys;print(json.load(sys.stdin)["status"]["token"])')`; `scripts/ask-hk --token-file <(echo $TOKEN) ...` is the normal path; for the overwrite: `curl -s -X POST https://kagent-mcp.arigsela.com/agents/kagent/homelab-knowledge -H "Authorization: Bearer $TOKEN" -H 'X-User-Id: admin@kagent.dev' -H 'Content-Type: application/json' -d "$(python3 -c 'import sys;sys.path.insert(0,"scripts");import kagent_a2a as a,json;print(json.dumps(a.build_send_request("say ok")))')"` → the new session row's `user_id` is the email, not `admin@kagent.dev`.
- (c) **Query refusal:** the same curl with `?user_id=admin@kagent.dev` appended → HTTP 5xx and no new session row. **No token from the LAN:** `curl -s -o /dev/null -w '%{http_code}' -X POST https://kagent-mcp.arigsela.com/agents/kagent/homelab-knowledge -H 'X-User-Id: admin@kagent.dev' -d '{}'` → `403`. **In-cluster no token:** `kubectl run t --rm -i --restart=Never --image=curlimages/curl:8.10.1 --overrides='{"spec":{"containers":[{"name":"t","image":"curlimages/curl:8.10.1","command":["curl","-s","-o","/dev/null","-w","%{http_code}","-X","POST","http://kagent-controller.kagent.svc:8083/agents/kagent/homelab-knowledge","-H","X-User-Id: admin@kagent.dev","-d","{}"],"resources":{"limits":{"cpu":"100m","memory":"64Mi"}}}]}}' -n dispatcher` → `403` (the namespace is in the mesh; the waypoint refuses). The same from an unenrolled namespace (`-n default`) → connection refused or reset (STRICT).
- (d) Dispatcher replay (runbook) → finding in Slack; session row `user_id = system:serviceaccount:dispatcher:dispatcher`; `scripts/ask-hk --as-serviceaccount dispatcher/dispatcher --agent kagent/k8s-reader --context <id> "what did you conclude?"` continues it.
- (e) Backstage agents page lists the agents (exception rule). The UI chats.
- (f) `scripts/agent-audit.py --since 1h --format json` (behind the SELECT-only credential, per its docs) shows the new principals.
- (g) `kubectl -n istio-system logs deploy/istiod --since=10m | grep -ci "jwks.*failed"` → 0.

### Task 7: Ledger, docs, the deck row, memory, and the Claude Code shortcut

**Files:**
- Modify: `SPEC.md` (V91, V92; T109 done with the spike's rulings; T110 owner: Backstage plugin), `index.md` (known-gaps row: identity), `docs/presentations/whole-picture-presentation-brief.md` (screen 07 identity row, screen 13 item 2 → BUILT, "needs a decision" column), `CLAUDE.md` (the identity paragraph under Agents if any; the "ask hk" note), `docs/superpowers/specs/2026-10-13-callers-as-principals-and-eval-gate-design.md` (status line: shape chosen)
- Modify (outside the repo, owner-authorised): `~/.claude/CLAUDE.md` shortcut text: follow-ups use `ask-hk --context`; the kagent MCP server is retired; run `claude mcp remove kagent` (user scope) and record it.
- Memory: `dispatcher-and-promotion.md` → a new `callers-as-principals.md` fact file + index line.

- [ ] **Step 1: SPEC rows**

Add after V90:

```
V91: ∀ A2A request → controller user paths (/agents, /mcp, /api) carries `x-user-id` SET BY THE MESH from a verified JWT (Dex `email` @ aud `kubernetes` | k8s SA `sub` @ aud `kagent`) | comes from the UI principal (identity = controller default) | the worker principal (runtime). client-supplied identity ⊥ reaches the controller (waypoint RequestAuthentication outputClaimToHeaders; httproute-waypoint refuses `?user_id=`; UI route strips headers). ingress: kagent-mcp rule = allow-list ∧ requestPrincipals. CI: tests/identity/.
V92: ⊥ long-lived agent-API credential anywhere: automations = projected SA tokens (aud `kagent`, ≤1h), humans = kubelogin Dex ID tokens. git ∌ token, Vault ∌ token.
```

T109 row (done) summarising the shape chosen, the spike criteria results, the acceptance evidence; T110 `.` OWNER ACTION: `arigsela/backstage` kagent plugin sends the projected token (`KAGENT_TOKEN_FILE`), then remove the exception rule.

- [ ] **Step 2: Docs and the deck**

`index.md` known gaps: replace the "A2A trusts a self-declared X-User-Id" sentence with "callers are principals since 2026-10-13 (V91); still open: per-person UI login, sub-agent calls trusted from the workers". Brief: screen 07 identity row → BUILT with the mechanism in one line; screen 13 item 2 → `governance · identity (BUILT 2026-10-13)`; screen 14 "needs a decision" keeps "who classifies tools…", drops "where the dispatcher's table lives" if already decided; add an "As of" line like the one for the docs class.

- [ ] **Step 3: Commit, PR (docs-only → promotes itself), memory, shortcut**

```bash
git add SPEC.md index.md docs/ CLAUDE.md
git commit -m "docs(identity): callers are principals (V91, V92); T109 closed, T110 owner action"
```

Then the user-level shortcut and `claude mcp remove kagent`; write the memory file.
