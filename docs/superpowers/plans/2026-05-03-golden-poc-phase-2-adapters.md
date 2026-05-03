# Golden POC — Phase 2: Surface Adapters + PR Review Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the two shared surface adapters (Slack and GitHub webhook), build the custom k8s-yaml-lint skill (replacing Phase 0's placeholder in agentregistry), hand-write the PR Review XAgent, and demonstrate both agents end-to-end on their real surfaces.

**Architecture:** Both adapters are small Go services that watch XAgent resources via Kubernetes informers and route surface-specific traffic to per-agent HTTP endpoints (rendered by the XAgent Composition in Phase 1). slack-adapter subscribes to Slack Events API; github-webhook-adapter receives GitHub webhook deliveries. The k8s-yaml-lint skill is a small Python MCP server wrapping kube-linter, packaged as an OCI image and republished into agentregistry.

**Tech Stack:** Go 1.22 (adapters), client-go (informers), Slack Bolt SDK or slack-go, go-github, Python 3.12 + the official MCP Python SDK (k8s-yaml-lint), kube-linter, Docker for image builds, ECR (existing) for image hosting.

**Reference design:** `docs/superpowers/specs/2026-05-02-golden-ai-platform-poc-design.md` Section 6 (Slack/GitHub adapters), Section 7 (PR Review Agent).

**Dependencies:**
- Phase 0 complete (agentgateway, agentregistry, GitHub App credentials in Vault).
- Phase 1 complete (XAgent XRD + Composition + cluster-health agent running).

---

## Task 2.1: Build the slack-adapter service

**Files:**
- Create: `services/slack-adapter/Dockerfile`
- Create: `services/slack-adapter/go.mod`
- Create: `services/slack-adapter/main.go`
- Create: `services/slack-adapter/internal/informer/informer.go`
- Create: `services/slack-adapter/internal/router/router.go`
- Create: `services/slack-adapter/internal/slack/handler.go`
- Create: `services/slack-adapter/README.md`

The slack-adapter watches `XAgent` resources with `surface: slack`, builds a routing table from XAgent annotations (e.g., `platform.arigsela.com/slack-command: cluster-health`), and dispatches Slack `app_mention` events to the matching agent's HTTP endpoint.

- [ ] **Step 1: Initialize the Go module + dependencies**

```bash
mkdir -p services/slack-adapter/internal/{informer,router,slack}
cd services/slack-adapter
go mod init github.com/arigsela/kubernetes/services/slack-adapter
go get k8s.io/client-go@latest k8s.io/apimachinery@latest k8s.io/apiextensions-apiserver@latest \
       github.com/slack-go/slack@latest \
       github.com/go-logr/logr@latest go.uber.org/zap@latest
```

- [ ] **Step 2: Write `main.go`**

`services/slack-adapter/main.go`:
```go
package main

import (
	"context"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"go.uber.org/zap"
	"k8s.io/client-go/dynamic"
	"k8s.io/client-go/rest"

	"github.com/arigsela/kubernetes/services/slack-adapter/internal/informer"
	"github.com/arigsela/kubernetes/services/slack-adapter/internal/router"
	slackh "github.com/arigsela/kubernetes/services/slack-adapter/internal/slack"
)

func main() {
	logger, _ := zap.NewProduction()
	defer logger.Sync()

	cfg, err := rest.InClusterConfig()
	if err != nil {
		logger.Fatal("rest.InClusterConfig", zap.Error(err))
	}
	dyn, err := dynamic.NewForConfig(cfg)
	if err != nil {
		logger.Fatal("dynamic.NewForConfig", zap.Error(err))
	}

	rt := router.New()
	inf := informer.New(dyn, rt, logger)

	ctx, cancel := context.WithCancel(context.Background())
	go inf.Run(ctx)

	bot := slackh.New(
		os.Getenv("SLACK_BOT_TOKEN"),
		os.Getenv("SLACK_SIGNING_SECRET"),
		rt,
		logger,
	)

	mux := http.NewServeMux()
	mux.HandleFunc("/slack/events", bot.HandleEvents)
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
	})

	srv := &http.Server{
		Addr:              ":8080",
		Handler:           mux,
		ReadHeaderTimeout: 5 * time.Second,
	}

	go func() {
		logger.Info("slack-adapter listening on :8080")
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			logger.Fatal("http.ListenAndServe", zap.Error(err))
		}
	}()

	sig := make(chan os.Signal, 1)
	signal.Notify(sig, os.Interrupt, syscall.SIGTERM)
	<-sig
	logger.Info("shutting down")
	cancel()
	shutdownCtx, c := context.WithTimeout(context.Background(), 10*time.Second)
	defer c()
	_ = srv.Shutdown(shutdownCtx)
}
```

- [ ] **Step 3: Write the XAgent informer**

`services/slack-adapter/internal/informer/informer.go`:
```go
package informer

import (
	"context"
	"time"

	"go.uber.org/zap"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/client-go/dynamic"
	"k8s.io/client-go/dynamic/dynamicinformer"
	"k8s.io/client-go/tools/cache"

	"github.com/arigsela/kubernetes/services/slack-adapter/internal/router"
)

var xagentGVR = schema.GroupVersionResource{
	Group:    "platform.arigsela.com",
	Version:  "v1alpha1",
	Resource: "xagents",
}

type Informer struct {
	dyn    dynamic.Interface
	router *router.Router
	log    *zap.Logger
}

func New(dyn dynamic.Interface, r *router.Router, log *zap.Logger) *Informer {
	return &Informer{dyn: dyn, router: r, log: log}
}

func (i *Informer) Run(ctx context.Context) {
	factory := dynamicinformer.NewDynamicSharedInformerFactory(i.dyn, 30*time.Second)
	gen := factory.ForResource(xagentGVR).Informer()

	_, _ = gen.AddEventHandler(cache.ResourceEventHandlerFuncs{
		AddFunc:    i.upsert,
		UpdateFunc: func(_, obj interface{}) { i.upsert(obj) },
		DeleteFunc: i.delete,
	})

	factory.Start(ctx.Done())
	if !cache.WaitForCacheSync(ctx.Done(), gen.HasSynced) {
		i.log.Error("informer cache failed to sync")
		return
	}
	<-ctx.Done()
}

func (i *Informer) upsert(obj interface{}) {
	m, ok := obj.(*metav1Lite)
	if !ok {
		return
	}
	if m.Spec.Surface != "slack" {
		return
	}
	cmd, ok := m.Metadata.Annotations["platform.arigsela.com/slack-command"]
	if !ok {
		return
	}
	i.router.Set(cmd, router.Target{
		Namespace: m.Metadata.Namespace,
		Name:      m.Metadata.Name,
		Endpoint:  endpointFor(m.Metadata.Namespace, m.Metadata.Name),
	})
	i.log.Info("registered", zap.String("cmd", cmd), zap.String("agent", m.Metadata.Name))
}

func (i *Informer) delete(obj interface{}) {
	m, ok := obj.(*metav1Lite)
	if !ok {
		return
	}
	if cmd, ok := m.Metadata.Annotations["platform.arigsela.com/slack-command"]; ok {
		i.router.Delete(cmd)
	}
}

func endpointFor(ns, name string) string {
	return "http://" + name + "." + ns + ".svc.cluster.local/v1/messages"
}

// metav1Lite is a minimal projection used by the dynamic informer.
// (For brevity here; in real code, use unstructured.Unstructured + helpers.)
type metav1Lite struct {
	Metadata metav1.ObjectMeta `json:"metadata"`
	Spec     struct {
		Surface string `json:"surface"`
	} `json:"spec"`
}
```

(For brevity this uses a typed projection; in production code, use `unstructured.Unstructured` and `metav1.ObjectMetaAccessor` helpers — fix this if your linter complains during build.)

- [ ] **Step 4: Write the router**

`services/slack-adapter/internal/router/router.go`:
```go
package router

import "sync"

type Target struct {
	Namespace string
	Name      string
	Endpoint  string
}

type Router struct {
	mu    sync.RWMutex
	byCmd map[string]Target
}

func New() *Router { return &Router{byCmd: make(map[string]Target)} }

func (r *Router) Set(cmd string, t Target) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.byCmd[cmd] = t
}

func (r *Router) Delete(cmd string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	delete(r.byCmd, cmd)
}

func (r *Router) Lookup(cmd string) (Target, bool) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	t, ok := r.byCmd[cmd]
	return t, ok
}
```

- [ ] **Step 5: Write the Slack handler**

`services/slack-adapter/internal/slack/handler.go`:
```go
package slack

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"time"

	"go.uber.org/zap"
	slackgo "github.com/slack-go/slack"
	"github.com/slack-go/slack/slackevents"

	"github.com/arigsela/kubernetes/services/slack-adapter/internal/router"
)

type Handler struct {
	api    *slackgo.Client
	signSecret string
	router *router.Router
	log    *zap.Logger
	httpc  *http.Client
}

func New(token, signSecret string, r *router.Router, log *zap.Logger) *Handler {
	return &Handler{
		api: slackgo.New(token),
		signSecret: signSecret,
		router: r,
		log: log,
		httpc: &http.Client{Timeout: 60 * time.Second},
	}
}

func (h *Handler) HandleEvents(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(r.Body)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	sv, err := slackgo.NewSecretsVerifier(r.Header, h.signSecret)
	if err != nil {
		http.Error(w, err.Error(), http.StatusUnauthorized)
		return
	}
	if _, err := sv.Write(body); err != nil {
		http.Error(w, err.Error(), http.StatusUnauthorized)
		return
	}
	if err := sv.Ensure(); err != nil {
		http.Error(w, err.Error(), http.StatusUnauthorized)
		return
	}

	ev, err := slackevents.ParseEvent(body, slackevents.OptionNoVerifyToken())
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	switch ev.Type {
	case slackevents.URLVerification:
		var rc *slackevents.ChallengeResponse
		if err := json.Unmarshal(body, &rc); err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		w.Header().Set("Content-Type", "text/plain")
		_, _ = w.Write([]byte(rc.Challenge))
	case slackevents.CallbackEvent:
		switch e := ev.InnerEvent.Data.(type) {
		case *slackevents.AppMentionEvent:
			h.handleMention(e)
		}
		w.WriteHeader(http.StatusOK)
	default:
		w.WriteHeader(http.StatusOK)
	}
}

func (h *Handler) handleMention(e *slackevents.AppMentionEvent) {
	// Strip the bot mention prefix and split into command + rest of message.
	text := strings.TrimSpace(e.Text)
	if i := strings.Index(text, ">"); i > -1 {
		text = strings.TrimSpace(text[i+1:])
	}
	parts := strings.SplitN(text, " ", 2)
	if len(parts) == 0 {
		return
	}
	cmd := parts[0]
	userMsg := ""
	if len(parts) > 1 {
		userMsg = parts[1]
	}

	target, ok := h.router.Lookup(cmd)
	if !ok {
		_, _, _ = h.api.PostMessage(e.Channel, slackgo.MsgOptionText(
			"No agent registered for `"+cmd+"`. Available agents are routed via the `platform.arigsela.com/slack-command` annotation.",
			false,
		))
		return
	}

	body := map[string]any{
		"messages": []map[string]string{{"role": "user", "content": userMsg}},
	}
	bb, _ := json.Marshal(body)
	resp, err := h.httpc.Post(target.Endpoint, "application/json", bytes.NewReader(bb))
	if err != nil {
		h.log.Error("agent call failed", zap.Error(err), zap.String("agent", target.Name))
		_, _, _ = h.api.PostMessage(e.Channel, slackgo.MsgOptionText("Agent unreachable: "+err.Error(), false), slackgo.MsgOptionTS(e.TimeStamp))
		return
	}
	defer resp.Body.Close()
	respBody, _ := io.ReadAll(resp.Body)

	// Expect kagent's response format: { "content": [{"type":"text","text":"..."}] }
	var parsed struct {
		Content []struct {
			Text string `json:"text"`
		} `json:"content"`
	}
	_ = json.Unmarshal(respBody, &parsed)
	out := ""
	for _, c := range parsed.Content {
		out += c.Text + "\n"
	}
	if out == "" {
		out = string(respBody)
	}

	_, _, _ = h.api.PostMessage(e.Channel, slackgo.MsgOptionText(out, false), slackgo.MsgOptionTS(e.TimeStamp))
}
```

- [ ] **Step 6: Write the Dockerfile**

`services/slack-adapter/Dockerfile`:
```dockerfile
FROM golang:1.22-alpine AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -o /out/slack-adapter ./

FROM gcr.io/distroless/static-debian12
COPY --from=build /out/slack-adapter /slack-adapter
USER 65532:65532
ENTRYPOINT ["/slack-adapter"]
```

- [ ] **Step 7: Build, test locally, and push the image**

```bash
cd services/slack-adapter
go build ./...      # compile check
go vet ./...

# Build and push to ECR (existing ecr-auth pattern is configured cluster-side)
IMG=<your-ecr-repo>/slack-adapter:v0.1.0
docker build -t "$IMG" .
docker push "$IMG"
```

Record the resolved image reference for use in Task 2.2.

- [ ] **Step 8: Commit**

```bash
git add services/slack-adapter/
git commit -m "feat(slack-adapter): Go service watching XAgents and routing Slack mentions (Phase 2)"
```

---

## Task 2.2: Deploy slack-adapter as a base-app

**Files:**
- Create: `base-apps/slack-adapter.yaml`
- Create: `base-apps/slack-adapter/namespace.yaml`
- Create: `base-apps/slack-adapter/secret-store.yaml`
- Create: `base-apps/slack-adapter/external-secret.yaml`
- Create: `base-apps/slack-adapter/rbac.yaml`
- Create: `base-apps/slack-adapter/deployment.yaml`
- Create: `base-apps/slack-adapter/service.yaml`
- Create: `base-apps/slack-adapter/ingress.yaml`

- [ ] **Step 1: Stage Slack credentials in Vault**

(After completing Task 2.3 Step 1 — creating the Slack app — return here.)

```bash
vault kv put k8s-secrets/slack-adapter \
  bot_token=<xoxb-... from Slack app config> \
  signing_secret=<from Slack app config>
```

- [ ] **Step 2: Create namespace and SecretStore**

`base-apps/slack-adapter/namespace.yaml`:
```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: slack-adapter
```

`base-apps/slack-adapter/secret-store.yaml`:
```yaml
apiVersion: external-secrets.io/v1beta1
kind: SecretStore
metadata:
  name: vault-backend
  namespace: slack-adapter
spec:
  provider:
    vault:
      server: "http://vault.vault.svc.cluster.local:8200"
      path: "k8s-secrets"
      version: "v2"
      auth:
        kubernetes:
          mountPath: "kubernetes"
          role: "slack-adapter"
          serviceAccountRef:
            name: "default"
```

```bash
vault write auth/kubernetes/role/slack-adapter \
  bound_service_account_names=default,slack-adapter \
  bound_service_account_namespaces=slack-adapter \
  policies=k8s-secrets-read \
  ttl=24h
```

- [ ] **Step 3: ExternalSecret**

`base-apps/slack-adapter/external-secret.yaml`:
```yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: slack-adapter
  namespace: slack-adapter
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: vault-backend
    kind: SecretStore
  target:
    name: slack-adapter
    creationPolicy: Owner
  data:
    - {secretKey: SLACK_BOT_TOKEN,      remoteRef: {key: slack-adapter, property: bot_token}}
    - {secretKey: SLACK_SIGNING_SECRET, remoteRef: {key: slack-adapter, property: signing_secret}}
```

- [ ] **Step 4: RBAC for the XAgent informer**

`base-apps/slack-adapter/rbac.yaml`:
```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: slack-adapter
  namespace: slack-adapter
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: slack-adapter-xagent-watch
rules:
  - apiGroups: ["platform.arigsela.com"]
    resources: ["xagents"]
    verbs: ["get", "list", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: slack-adapter-xagent-watch
subjects:
  - kind: ServiceAccount
    name: slack-adapter
    namespace: slack-adapter
roleRef:
  kind: ClusterRole
  name: slack-adapter-xagent-watch
  apiGroup: rbac.authorization.k8s.io
```

- [ ] **Step 5: Deployment + Service + Ingress**

`base-apps/slack-adapter/deployment.yaml`:
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: slack-adapter
  namespace: slack-adapter
  labels:
    app.kubernetes.io/name: slack-adapter
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: slack-adapter
  template:
    metadata:
      labels:
        app.kubernetes.io/name: slack-adapter
    spec:
      serviceAccountName: slack-adapter
      imagePullSecrets:
        - name: ecr-auth
      containers:
        - name: app
          image: <your-ecr-repo>/slack-adapter:v0.1.0  # from Task 2.1 Step 7
          ports:
            - {containerPort: 8080, name: http}
          envFrom:
            - secretRef:
                name: slack-adapter
          resources:
            requests: {cpu: 50m, memory: 64Mi}
            limits:   {cpu: 500m, memory: 256Mi}
          livenessProbe:
            httpGet: {path: /healthz, port: 8080}
            initialDelaySeconds: 10
          readinessProbe:
            httpGet: {path: /healthz, port: 8080}
            initialDelaySeconds: 3
```

`base-apps/slack-adapter/service.yaml`:
```yaml
apiVersion: v1
kind: Service
metadata:
  name: slack-adapter
  namespace: slack-adapter
spec:
  type: ClusterIP
  selector:
    app.kubernetes.io/name: slack-adapter
  ports:
    - {name: http, port: 80, targetPort: 8080}
```

`base-apps/slack-adapter/ingress.yaml`:
```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: slack-adapter
  namespace: slack-adapter
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
spec:
  ingressClassName: nginx
  tls:
    - hosts: [slack.<base-domain>]
      secretName: slack-adapter-tls
  rules:
    - host: slack.<base-domain>
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: slack-adapter
                port: {number: 80}
```

- [ ] **Step 6: ArgoCD application**

`base-apps/slack-adapter.yaml`:
```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: slack-adapter
  namespace: argo-cd
spec:
  project: default
  source:
    repoURL: https://github.com/arigsela/kubernetes
    targetRevision: main
    path: base-apps/slack-adapter
  destination:
    server: https://kubernetes.default.svc
    namespace: slack-adapter
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
      - ServerSideApply=true
```

- [ ] **Step 7: Commit**

```bash
git add base-apps/slack-adapter.yaml base-apps/slack-adapter/
git commit -m "feat(slack-adapter): deploy as base-app (Phase 2)"
```

After ArgoCD sync: `kubectl get pods -n slack-adapter` shows the pod Running and `kubectl logs` shows informer cache synced.

---

## Task 2.3: Set up Slack app + wire to cluster-health agent

**Files:**
- Modify: `base-apps/agents/cluster-health/agent.yaml` (add slack-command annotation)

- [ ] **Step 1: Create the Slack app**

Go to https://api.slack.com/apps → "Create New App" → "From scratch":
- App name: `golden-poc-bot`
- Workspace: your workspace

In the new app:
- **OAuth & Permissions** → Bot Token Scopes: `app_mentions:read`, `chat:write`, `chat:write.public`. Install the app to the workspace. Copy the **Bot User OAuth Token** (starts with `xoxb-`).
- **Basic Information** → App Credentials → copy the **Signing Secret**.
- **Event Subscriptions** → Enable Events.
  - Request URL: `https://slack.<base-domain>/slack/events`
  - Subscribe to bot events: `app_mention`
  - Save Changes (Slack will verify the URL — needs Task 2.2 deployed and reachable; iterate if needed).

- [ ] **Step 2: Push tokens to Vault**

```bash
vault kv put k8s-secrets/slack-adapter \
  bot_token=<xoxb-...> \
  signing_secret=<...>
```

Restart the slack-adapter pod to pick up new envs:
```bash
kubectl rollout restart deployment/slack-adapter -n slack-adapter
```

- [ ] **Step 3: Tag cluster-health XAgent with the slash command**

Modify `base-apps/agents/cluster-health/agent.yaml` to add the annotation:

```yaml
metadata:
  name: cluster-health
  namespace: agents
  annotations:
    platform.arigsela.com/base-domain: "<base-domain>"
    platform.arigsela.com/slack-command: "cluster-health"  # new
```

- [ ] **Step 4: Update the Vault placeholder with a real bot token (from Step 1)**

Phase 1 staged a placeholder under `k8s-secrets/agents/cluster-health` for `slack_bot_token`. The agent itself doesn't actually need a Slack token (the adapter handles all Slack I/O), but the ExternalSecret rendered by the Composition exists. Either:

**Option A:** Leave the placeholder; the secret is unused by the agent.
**Option B:** Remove the slack-token requirement from the Composition for `surface: slack` (since the adapter holds it). Cleaner long term but requires re-running `crossplane render` to verify.

For Phase 2, **Option A** is fine. Mark this for cleanup in Phase 4.

- [ ] **Step 5: Commit**

```bash
git add base-apps/agents/cluster-health/agent.yaml
git commit -m "feat(agent): wire cluster-health to slack-command 'cluster-health' (Phase 2)"
```

- [ ] **Step 6: End-to-end Slack test**

In Slack, in any channel where the bot is invited:
```
@golden-poc-bot cluster-health agents
```

Expected: a thread reply with the agent's Markdown analysis of the `agents` namespace.

If the bot doesn't respond:
- Check `kubectl logs -n slack-adapter -l app.kubernetes.io/name=slack-adapter --tail=50` for routing or HTTP errors.
- Verify the Slack Events Request URL was successfully verified (Slack app config page).

---

## Task 2.4: Build the github-webhook-adapter service

**Files:**
- Create: `services/github-webhook-adapter/Dockerfile`
- Create: `services/github-webhook-adapter/go.mod`
- Create: `services/github-webhook-adapter/main.go`
- Create: `services/github-webhook-adapter/internal/informer/informer.go`
- Create: `services/github-webhook-adapter/internal/router/router.go`
- Create: `services/github-webhook-adapter/internal/github/handler.go`
- Create: `services/github-webhook-adapter/internal/github/app_auth.go`
- Create: `services/github-webhook-adapter/README.md`

This adapter receives GitHub webhook deliveries, looks up the matching agent (by `platform.arigsela.com/github-repo` annotation), invokes the agent with the PR diff + context, parses the response into line-anchored review comments, and posts them via the GitHub API using a GitHub App-authenticated client.

- [ ] **Step 1: Initialize and write `main.go`**

```bash
mkdir -p services/github-webhook-adapter/internal/{informer,router,github}
cd services/github-webhook-adapter
go mod init github.com/arigsela/kubernetes/services/github-webhook-adapter
go get k8s.io/client-go@latest k8s.io/apimachinery@latest \
       github.com/google/go-github/v66@latest \
       github.com/bradleyfalzon/ghinstallation/v2@latest \
       github.com/golang-jwt/jwt/v5@latest \
       go.uber.org/zap@latest
```

`services/github-webhook-adapter/main.go`:
```go
package main

import (
	"context"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"go.uber.org/zap"
	"k8s.io/client-go/dynamic"
	"k8s.io/client-go/rest"

	"github.com/arigsela/kubernetes/services/github-webhook-adapter/internal/github"
	"github.com/arigsela/kubernetes/services/github-webhook-adapter/internal/informer"
	"github.com/arigsela/kubernetes/services/github-webhook-adapter/internal/router"
)

func main() {
	logger, _ := zap.NewProduction()
	defer logger.Sync()

	cfg, err := rest.InClusterConfig()
	if err != nil { logger.Fatal("rest", zap.Error(err)) }
	dyn, err := dynamic.NewForConfig(cfg)
	if err != nil { logger.Fatal("dynamic", zap.Error(err)) }

	rt := router.New()
	go informer.New(dyn, rt, logger).Run(context.Background())

	h := github.NewHandler(
		os.Getenv("GITHUB_APP_ID"),
		os.Getenv("GITHUB_INSTALLATION_ID"),
		[]byte(os.Getenv("GITHUB_PRIVATE_KEY")),
		os.Getenv("GITHUB_WEBHOOK_SECRET"),
		rt,
		logger,
	)

	mux := http.NewServeMux()
	mux.HandleFunc("/webhook", h.HandleWebhook)
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(200) })
	srv := &http.Server{Addr: ":8080", Handler: mux, ReadHeaderTimeout: 5 * time.Second}

	go func() {
		logger.Info("github-webhook-adapter listening on :8080")
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			logger.Fatal("listen", zap.Error(err))
		}
	}()

	sig := make(chan os.Signal, 1)
	signal.Notify(sig, os.Interrupt, syscall.SIGTERM)
	<-sig
	c, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	_ = srv.Shutdown(c)
}
```

- [ ] **Step 2: Write the informer**

`services/github-webhook-adapter/internal/informer/informer.go`:
```go
package informer

import (
	"context"
	"time"

	"go.uber.org/zap"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/client-go/dynamic"
	"k8s.io/client-go/dynamic/dynamicinformer"
	"k8s.io/client-go/tools/cache"

	"github.com/arigsela/kubernetes/services/github-webhook-adapter/internal/router"
)

var xagentGVR = schema.GroupVersionResource{
	Group: "platform.arigsela.com", Version: "v1alpha1", Resource: "xagents",
}

type Informer struct {
	dyn    dynamic.Interface
	router *router.Router
	log    *zap.Logger
}

func New(dyn dynamic.Interface, r *router.Router, l *zap.Logger) *Informer {
	return &Informer{dyn: dyn, router: r, log: l}
}

func (i *Informer) Run(ctx context.Context) {
	f := dynamicinformer.NewDynamicSharedInformerFactory(i.dyn, 30*time.Second)
	gen := f.ForResource(xagentGVR).Informer()
	_, _ = gen.AddEventHandler(cache.ResourceEventHandlerFuncs{
		AddFunc:    i.upsert,
		UpdateFunc: func(_, obj interface{}) { i.upsert(obj) },
		DeleteFunc: i.delete,
	})
	f.Start(ctx.Done())
	if !cache.WaitForCacheSync(ctx.Done(), gen.HasSynced) {
		i.log.Error("cache sync failed")
		return
	}
	<-ctx.Done()
}

func (i *Informer) upsert(obj interface{}) {
	// (Use unstructured.Unstructured + accessor helpers; abbreviated here.)
	// Filter: spec.surface == "github-webhook" AND
	// metadata.annotations["platform.arigsela.com/github-repo"] is set.
	// Register: repo -> Target{Namespace, Name, Endpoint}
}
func (i *Informer) delete(obj interface{}) {
	// Inverse of upsert.
}
```

(The skeleton above is incomplete by design — fill in the unstructured-access boilerplate during implementation. The pattern matches `slack-adapter/internal/informer/informer.go` closely.)

- [ ] **Step 3: Write the router**

`services/github-webhook-adapter/internal/router/router.go`:
```go
package router

import "sync"

type Target struct {
	Namespace string
	Name      string
	Endpoint  string  // http://<name>.<ns>.svc.cluster.local/v1/messages
}

type Router struct {
	mu     sync.RWMutex
	byRepo map[string]Target  // key: "owner/repo"
}

func New() *Router { return &Router{byRepo: map[string]Target{}} }

func (r *Router) Set(repo string, t Target) {
	r.mu.Lock(); defer r.mu.Unlock()
	r.byRepo[repo] = t
}
func (r *Router) Delete(repo string) {
	r.mu.Lock(); defer r.mu.Unlock()
	delete(r.byRepo, repo)
}
func (r *Router) Lookup(repo string) (Target, bool) {
	r.mu.RLock(); defer r.mu.RUnlock()
	t, ok := r.byRepo[repo]
	return t, ok
}
```

- [ ] **Step 4: Write the GitHub App auth helper**

`services/github-webhook-adapter/internal/github/app_auth.go`:
```go
package github

import (
	"net/http"
	"strconv"

	"github.com/bradleyfalzon/ghinstallation/v2"
	gh "github.com/google/go-github/v66/github"
)

// NewClient returns a github client authenticated as the App's installation.
func NewClient(appID, installationID string, privateKeyPEM []byte) (*gh.Client, error) {
	aid, err := strconv.ParseInt(appID, 10, 64)
	if err != nil { return nil, err }
	iid, err := strconv.ParseInt(installationID, 10, 64)
	if err != nil { return nil, err }
	tr, err := ghinstallation.New(http.DefaultTransport, aid, iid, privateKeyPEM)
	if err != nil { return nil, err }
	return gh.NewClient(&http.Client{Transport: tr}), nil
}
```

- [ ] **Step 5: Write the webhook handler**

`services/github-webhook-adapter/internal/github/handler.go`:
```go
package github

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"time"

	"go.uber.org/zap"
	gh "github.com/google/go-github/v66/github"

	"github.com/arigsela/kubernetes/services/github-webhook-adapter/internal/router"
)

type Handler struct {
	client *gh.Client
	secret []byte
	router *router.Router
	log    *zap.Logger
	httpc  *http.Client
}

func NewHandler(appID, instID string, key []byte, secret string, r *router.Router, l *zap.Logger) *Handler {
	c, err := NewClient(appID, instID, key)
	if err != nil { l.Fatal("github auth", zap.Error(err)) }
	return &Handler{
		client: c,
		secret: []byte(secret),
		router: r,
		log: l,
		httpc: &http.Client{Timeout: 90 * time.Second},
	}
}

func (h *Handler) HandleWebhook(w http.ResponseWriter, r *http.Request) {
	payload, err := gh.ValidatePayload(r, h.secret)
	if err != nil {
		http.Error(w, err.Error(), http.StatusUnauthorized); return
	}
	event, err := gh.ParseWebHook(gh.WebHookType(r), payload)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest); return
	}

	switch e := event.(type) {
	case *gh.PullRequestEvent:
		if e.GetAction() != "opened" && e.GetAction() != "synchronize" {
			w.WriteHeader(http.StatusOK); return
		}
		go h.processPR(context.Background(), e)
	}
	w.WriteHeader(http.StatusOK)
}

type ReviewComment struct {
	Path     string `json:"path"`
	Line     int    `json:"line"`
	Severity string `json:"severity"`  // CRITICAL | HIGH | MEDIUM | LOW
	Message  string `json:"message"`
	Suggestion string `json:"suggestion"`
}

type AgentResponse struct {
	Comments []ReviewComment `json:"comments"`
	OverallNote string `json:"overall_note,omitempty"`  // when no risks
}

func (h *Handler) processPR(ctx context.Context, e *gh.PullRequestEvent) {
	owner := e.GetRepo().GetOwner().GetLogin()
	repo := e.GetRepo().GetName()
	num := e.GetPullRequest().GetNumber()
	full := owner + "/" + repo

	target, ok := h.router.Lookup(full)
	if !ok {
		h.log.Info("no agent for repo", zap.String("repo", full))
		return
	}

	// Fetch the full diff via the github-mcp tool by way of the agent itself.
	// We pass minimal context; the agent uses its github-mcp skill to fetch detail.
	prompt := map[string]any{
		"messages": []map[string]string{{
			"role": "user",
			"content": "Review PR #" + itoa(num) + " in " + full + ". Return JSON matching the shape: " +
				`{"comments":[{"path":"...","line":N,"severity":"HIGH","message":"...","suggestion":"..."}],"overall_note":"..."}`,
		}},
	}
	bb, _ := json.Marshal(prompt)
	resp, err := h.httpc.Post(target.Endpoint, "application/json", bytes.NewReader(bb))
	if err != nil { h.log.Error("agent call", zap.Error(err)); return }
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	// kagent wraps response: {content: [{type:"text", text: "..."}]}.
	// Extract the text and parse as AgentResponse.
	var k struct {
		Content []struct{ Text string `json:"text"` } `json:"content"`
	}
	_ = json.Unmarshal(body, &k)
	textBody := ""
	for _, c := range k.Content { textBody += c.Text }

	// Extract the JSON block from the LLM's reply.
	textBody = strings.TrimSpace(textBody)
	if i := strings.Index(textBody, "{"); i > 0 { textBody = textBody[i:] }
	if i := strings.LastIndex(textBody, "}"); i > 0 { textBody = textBody[:i+1] }

	var ar AgentResponse
	if err := json.Unmarshal([]byte(textBody), &ar); err != nil {
		h.log.Error("agent response parse", zap.Error(err), zap.String("body", textBody))
		// Fallback: post the raw text as an issue comment.
		_, _, _ = h.client.Issues.CreateComment(ctx, owner, repo, num, &gh.IssueComment{Body: gh.String(textBody)})
		return
	}

	// Build a single GitHub review with all comments.
	if len(ar.Comments) == 0 {
		body := ar.OverallNote
		if body == "" { body = "No risks identified." }
		_, _, _ = h.client.PullRequests.CreateReview(ctx, owner, repo, num, &gh.PullRequestReviewRequest{
			Body: gh.String(body),
			Event: gh.String("COMMENT"),
		})
		return
	}

	commits, _, err := h.client.PullRequests.ListCommits(ctx, owner, repo, num, &gh.ListOptions{PerPage: 100})
	if err != nil || len(commits) == 0 { h.log.Error("list commits", zap.Error(err)); return }
	headSHA := commits[len(commits)-1].GetSHA()

	draft := []*gh.DraftReviewComment{}
	for _, c := range ar.Comments {
		body := "[" + c.Severity + "] " + c.Message
		if c.Suggestion != "" { body += "\n\n_Suggestion:_ " + c.Suggestion }
		draft = append(draft, &gh.DraftReviewComment{
			Path: gh.String(c.Path),
			Line: gh.Int(c.Line),
			Body: gh.String(body),
		})
	}
	_, _, _ = h.client.PullRequests.CreateReview(ctx, owner, repo, num, &gh.PullRequestReviewRequest{
		CommitID: gh.String(headSHA),
		Body: gh.String("Automated review from PR Review Agent"),
		Event: gh.String("COMMENT"),
		Comments: draft,
	})
}

func itoa(n int) string { return strings.TrimSpace(strings.Repeat(" ", 0)) + fmtInt(n) }
func fmtInt(n int) string {
	if n == 0 { return "0" }
	neg := n < 0
	if neg { n = -n }
	b := []byte{}
	for n > 0 {
		b = append([]byte{byte('0' + n%10)}, b...)
		n /= 10
	}
	if neg { b = append([]byte{'-'}, b...) }
	return string(b)
}
```

(Several helpers above are intentionally minimalist — replace `itoa` with `strconv.Itoa(num)` in production. Kept here to avoid extra imports in the plan.)

- [ ] **Step 6: Dockerfile + build + push**

`services/github-webhook-adapter/Dockerfile`:
```dockerfile
FROM golang:1.22-alpine AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -o /out/github-webhook-adapter ./

FROM gcr.io/distroless/static-debian12
COPY --from=build /out/github-webhook-adapter /github-webhook-adapter
USER 65532:65532
ENTRYPOINT ["/github-webhook-adapter"]
```

```bash
cd services/github-webhook-adapter
go build ./... && go vet ./...
IMG=<your-ecr-repo>/github-webhook-adapter:v0.1.0
docker build -t "$IMG" .
docker push "$IMG"
```

- [ ] **Step 7: Commit**

```bash
git add services/github-webhook-adapter/
git commit -m "feat(github-webhook-adapter): Go service for PR webhook routing (Phase 2)"
```

---

## Task 2.5: Deploy github-webhook-adapter as a base-app

**Files:**
- Create: `base-apps/github-webhook-adapter.yaml`
- Create: `base-apps/github-webhook-adapter/{namespace,secret-store,external-secret,rbac,deployment,service,ingress}.yaml`

Same shape as the slack-adapter base-app structure (Task 2.2). Differences:
- ExternalSecret pulls `app_id`, `installation_id`, `webhook_secret`, `private_key` from `k8s-secrets/github-webhook-adapter` (staged in Phase 0 Task 0.3).
- Ingress hostname: `github-webhook.<base-domain>` (matches the GitHub App's webhook URL).
- ClusterRole grants watch on `xagents` (same as slack-adapter).

- [ ] **Step 1: Create the namespace + SecretStore + ExternalSecret**

(Mirror slack-adapter Task 2.2 Steps 2–3; substitute `slack-adapter` → `github-webhook-adapter`. The ExternalSecret data block:)

```yaml
data:
  - {secretKey: GITHUB_APP_ID,          remoteRef: {key: github-webhook-adapter, property: app_id}}
  - {secretKey: GITHUB_INSTALLATION_ID, remoteRef: {key: github-webhook-adapter, property: installation_id}}
  - {secretKey: GITHUB_WEBHOOK_SECRET,  remoteRef: {key: github-webhook-adapter, property: webhook_secret}}
  - {secretKey: GITHUB_PRIVATE_KEY,     remoteRef: {key: github-webhook-adapter, property: private_key}}
```

- [ ] **Step 2: Vault role**

```bash
vault write auth/kubernetes/role/github-webhook-adapter \
  bound_service_account_names=default,github-webhook-adapter \
  bound_service_account_namespaces=github-webhook-adapter \
  policies=k8s-secrets-read \
  ttl=24h
```

- [ ] **Step 3: RBAC, Deployment, Service, Ingress**

Identical structure to slack-adapter except:
- Ingress host: `github-webhook.<base-domain>`
- Image: `<your-ecr-repo>/github-webhook-adapter:v0.1.0`

- [ ] **Step 4: Commit**

```bash
git add base-apps/github-webhook-adapter.yaml base-apps/github-webhook-adapter/
git commit -m "feat(github-webhook-adapter): deploy as base-app (Phase 2)"
```

After ArgoCD sync: `kubectl get pods -n github-webhook-adapter` shows Running.

- [ ] **Step 5: Verify webhook endpoint reachable from GitHub**

```bash
curl -fsS -X POST https://github-webhook.<base-domain>/webhook \
  -H "X-GitHub-Event: ping" \
  -d '{}' \
  -i 2>&1 | head -5
# Expected: HTTP 401 (signature missing) — proves the endpoint is reachable.
```

If the URL isn't reachable from the public internet (homelab NAT issues), set up a Cloudflare Tunnel or smee.io proxy now. The GitHub App's webhook URL must be publicly reachable.

---

## Task 2.6: Build the custom k8s-yaml-lint skill (MCP server)

**Files:**
- Create: `services/k8s-yaml-lint/Dockerfile`
- Create: `services/k8s-yaml-lint/pyproject.toml`
- Create: `services/k8s-yaml-lint/server.py`
- Create: `services/k8s-yaml-lint/README.md`

A small MCP server that wraps `kube-linter` and exposes a `lint_yaml` tool.

- [ ] **Step 1: Write the MCP server**

`services/k8s-yaml-lint/server.py`:
```python
"""k8s-yaml-lint MCP server: wraps kube-linter as an MCP tool."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

server = Server("k8s-yaml-lint")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="lint_yaml",
            description=(
                "Run kube-linter against the provided Kubernetes YAML. "
                "Returns structured findings with severity, file, line, check name, and message."
            ),
            inputSchema={
                "type": "object",
                "required": ["yaml"],
                "properties": {
                    "yaml": {
                        "type": "string",
                        "description": "Multi-document YAML containing one or more Kubernetes resources.",
                    },
                    "checks": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional kube-linter check names to enable; defaults to all built-in checks.",
                    },
                },
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name != "lint_yaml":
        raise ValueError(f"unknown tool: {name}")
    yaml_text = arguments["yaml"]
    checks = arguments.get("checks") or []

    with tempfile.TemporaryDirectory() as td:
        yaml_path = Path(td) / "input.yaml"
        yaml_path.write_text(yaml_text)
        cmd = ["kube-linter", "lint", "--format", "json", str(yaml_path)]
        if checks:
            cmd += ["--include", ",".join(checks)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        # kube-linter exits non-zero on findings; that's fine.
        try:
            payload = json.loads(proc.stdout) if proc.stdout else {"Reports": []}
        except json.JSONDecodeError:
            payload = {"raw_stdout": proc.stdout, "raw_stderr": proc.stderr}

    return [TextContent(type="text", text=json.dumps(payload, indent=2))]


def main():
    import asyncio
    asyncio.run(_run())


async def _run():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: pyproject.toml + Dockerfile**

`services/k8s-yaml-lint/pyproject.toml`:
```toml
[project]
name = "k8s-yaml-lint"
version = "1.0.0"
requires-python = ">=3.12"
dependencies = ["mcp>=1.0.0"]

[project.scripts]
k8s-yaml-lint = "server:main"
```

`services/k8s-yaml-lint/Dockerfile`:
```dockerfile
FROM python:3.12-slim AS base
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -fsSL -o /usr/local/bin/kube-linter https://github.com/stackrox/kube-linter/releases/latest/download/kube-linter-linux \
    && chmod +x /usr/local/bin/kube-linter \
    && apt-get purge -y curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml server.py ./
RUN pip install --no-cache-dir -e .

USER 65532:65532
ENTRYPOINT ["python", "server.py"]
```

- [ ] **Step 3: Build and push the image**

```bash
cd services/k8s-yaml-lint
IMG=<your-ecr-repo>/k8s-yaml-lint:v1
docker build -t "$IMG" .
docker push "$IMG"
```

- [ ] **Step 4: Republish into agentregistry (replaces Phase 0 placeholder)**

```bash
arctl push skill \
  --name k8s-yaml-lint \
  --version v1 \
  --type mcp-server \
  --image <your-ecr-repo>/k8s-yaml-lint:v1 \
  --tags k8s,lint,custom \
  --description "Structural YAML/Kustomize linting via kube-linter."
```

- [ ] **Step 5: Commit**

```bash
git add services/k8s-yaml-lint/
git commit -m "feat(k8s-yaml-lint): custom MCP skill wrapping kube-linter (Phase 2)"
```

---

## Task 2.7: Hand-write the PR Review XAgent

**Files:**
- Create: `base-apps/agents/pr-review/agent.yaml`
- Create: `base-apps/agents/pr-review/catalog-info.yaml`
- Modify: Vault entry `k8s-secrets/agents/pr-review` to mirror Phase 0's GitHub App creds (so the per-agent ExternalSecret can resolve)

- [ ] **Step 1: Stage GitHub App creds under the per-agent Vault path**

The Composition's per-agent ExternalSecret reads from `k8s-secrets/agents/<name>` for `github-webhook` surface. Either:
- **Option A:** Copy the App credentials from `k8s-secrets/github-webhook-adapter` to `k8s-secrets/agents/pr-review`.
- **Option B:** Adjust the Composition to read from a shared path for `github-webhook` surface (cleaner; do this in Phase 4 cleanup).

For Phase 2, Option A:
```bash
APP_ID=$(vault kv get -field=app_id k8s-secrets/github-webhook-adapter)
INSTALL_ID=$(vault kv get -field=installation_id k8s-secrets/github-webhook-adapter)
PRIV_KEY=$(vault kv get -field=private_key k8s-secrets/github-webhook-adapter)
vault kv put k8s-secrets/agents/pr-review \
  github_app_id="$APP_ID" \
  github_installation_id="$INSTALL_ID" \
  github_private_key="$PRIV_KEY"
```

- [ ] **Step 2: Write the XAgent**

`base-apps/agents/pr-review/agent.yaml`:
```yaml
apiVersion: platform.arigsela.com/v1alpha1
kind: XAgent
metadata:
  name: pr-review
  namespace: agents
  annotations:
    platform.arigsela.com/base-domain: "<base-domain>"
    platform.arigsela.com/github-repo: arigsela/kubernetes
spec:
  description: "Posts inline review comments on opened PRs with risk callouts."
  systemPrompt: |
    You are the PR Review Agent for arigsela/kubernetes. For each PR diff:

    Categorize risks as:
      CRITICAL — secret exposure, mass deletion, prod-affecting RBAC widening
      HIGH     — CRD removals, broad RBAC changes, removed health checks,
                 image:latest, forceDestroy on persistent storage
      MEDIUM   — replicas reduced below 2, removed resource limits, namespace changes
      LOW      — style, comments, cosmetic

    Use github-mcp to fetch the diff. Use lint to run structural checks.

    Respond ONLY with valid JSON matching this schema:
      {
        "comments": [
          {"path":"...","line":N,"severity":"HIGH","message":"one-line explanation",
           "suggestion":"one-line fix"}
        ],
        "overall_note": "string, used only when comments=[]"
      }

    Do NOT approve or merge. You are advisory only.
  skills:
    - ref: oci://agentregistry.agentregistry.svc/skills/github-mcp:v1
      alias: github
    - ref: oci://agentregistry.agentregistry.svc/skills/k8s-yaml-lint:v1
      alias: lint
  surface: github-webhook
```

- [ ] **Step 3: Backstage Component**

`base-apps/agents/pr-review/catalog-info.yaml`:
```yaml
apiVersion: backstage.io/v1alpha1
kind: Component
metadata:
  name: pr-review-agent
  description: "Posts inline review comments on opened PRs."
  annotations:
    backstage.io/kubernetes-id: pr-review
    kagent.dev/agent-name: pr-review
    langfuse.platform.arigsela.com/project: golden-poc
    agent.platform.arigsela.com/try-url: https://pr-review.<base-domain>/v1/messages
spec:
  type: agent
  lifecycle: experimental
  owner: platform-team
```

- [ ] **Step 4: Commit**

```bash
git add base-apps/agents/pr-review/
git commit -m "feat(agent): pr-review XAgent + Backstage Component (Phase 2)"
```

After ArgoCD sync: `kubectl get xagent pr-review -n agents` shows Synced/Ready and all child resources exist.

---

## Task 2.8: End-to-end test on a real PR

**Files:** None (verification-only)

- [ ] **Step 1: Open a deliberately-risky PR against arigsela/kubernetes**

Create a feature branch and a PR that includes one of:
- A Deployment with `replicas: 1` and a removed `livenessProbe`
- A `forceDestroy: true` on an S3 bucket Composition output
- A new ClusterRole with `verbs: ["*"]` on `secrets`

Push and open the PR via GitHub UI or `gh pr create`.

- [ ] **Step 2: Watch the github-webhook-adapter logs**

```bash
kubectl logs -n github-webhook-adapter -l app.kubernetes.io/name=github-webhook-adapter -f
# Expected: "PR opened" log within ~5s of pushing the PR; agent call; then "review created"
```

- [ ] **Step 3: Verify review comments appear on the PR**

Refresh the PR page in GitHub. Expected: a review with inline comments at the lines containing the risky changes, with severity prefixes.

If the review fails to post but the agent was called: check `kubectl logs -n agents -l kagent.dev/agent-name=pr-review` for the LLM's actual response — it may not have produced valid JSON. Iterate on the system prompt.

- [ ] **Step 4: Verify trace in Langfuse**

Open Langfuse → project `golden-poc` → Traces → filter by `pr-review`. Expected: one trace per PR event with the full conversation.

---

## Task 2.9: Phase 2 acceptance + handoff to Phase 3

**Files:**
- Modify: `docs/superpowers/plans/2026-05-03-phase-0-preflight-results.md`

- [ ] **Step 1: Append Phase 2 status**

```markdown
## Phase 2 — Status

- [x] slack-adapter built, deployed, watching XAgents.
- [x] cluster-health agent reachable via `@golden-poc-bot cluster-health <ns>` in Slack.
- [x] github-webhook-adapter built, deployed, watching XAgents.
- [x] Custom k8s-yaml-lint skill v1 built and pushed to agentregistry (replaces Phase 0 placeholder).
- [x] pr-review XAgent live; reviews real PRs on arigsela/kubernetes with inline comments.
- [x] Both agents observable in Langfuse.

**Phase 3 ready to start.**
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/plans/2026-05-03-phase-0-preflight-results.md
git commit -m "docs(golden-poc): Phase 2 verification complete"
```

Phase 2 complete. Both demo agents work end-to-end on their real surfaces. Phase 3 layers on Backstage's Software Template + per-agent UX so engineers can self-serve future agents instead of hand-writing YAML.
