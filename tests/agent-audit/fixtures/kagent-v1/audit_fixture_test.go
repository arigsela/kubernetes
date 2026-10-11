package database

// Fixture generator for arigsela/kubernetes tests/agent-audit (kagent 1.0 port).
// Not built or run by this repo's CI; kept so events.json can be regenerated.
//
// Drives kagent v1.0.0-alpha11's REAL persistence path against a production-shaped
// database: migrations run as role kagent_owner into schema `kagent` (pgvector in
// `extensions`), sessions are created and made ready through the Client, and every
// A2A event is built with the same helpers the Go runtime uses (adka2a.ToA2APart,
// kagent's BuildHITLStatusMessage, apia2a.AttachHITL, a2aevent.Apply*) and saved
// with the runtime protocol (CreateRuntimeTask / UpdateSessionTask + settlement).
//
// To regenerate (scenarios change, or a new kagent alpha):
//
//  1. A pgvector/pgvector:0.8.7-pg18 Postgres, prepared as the plan's Task 3.4 does:
//     CREATE ROLE kagent_owner NOLOGIN;
//     CREATE ROLE kagent_user LOGIN PASSWORD '...' IN ROLE kagent_owner;
//     CREATE DATABASE kagent_v1;  -- then in kagent_v1:
//     CREATE SCHEMA kagent AUTHORIZATION kagent_owner; CREATE SCHEMA extensions;
//     CREATE EXTENSION vector WITH SCHEMA extensions; GRANT USAGE ON SCHEMA extensions TO kagent_owner;
//  2. Copy this file into kagent's go/core/internal/database/ at the tag and run
//     (golang:1.27 image works):
//
//     AUDIT_FIXTURE_DSN=postgres://kagent_user:...@host/kagent_v1?sslmode=disable \
//     AUDIT_FIXTURE_SESSIONS=/tmp/sessions.json \
//     go test -short -count=1 -run TestAuditFixture ./core/internal/database/
//
//     -short stops the package TestMain from starting its own container.
//  3. python export_fixture.py <repo> /tmp/sessions.json events.json <admin DSN>
//     (this directory) snapshots the rows through agent-audit.py's V1_EVENTS_SELECT.

import (
	"context"
	"encoding/json"
	"os"
	"testing"
	"time"

	"github.com/a2aproject/a2a-go/v2/a2a"
	"github.com/a2aproject/a2a-go/v2/a2aevent"
	a2apb "github.com/a2aproject/a2a-go/v2/a2apb/v1"
	"github.com/google/uuid"
	kagentadka2a "github.com/kagent-dev/kagent/go/adk/pkg/a2a"
	apia2a "github.com/kagent-dev/kagent/go/api/a2a"
	apiv1alpha1 "github.com/kagent-dev/kagent/go/api/gen/kagent/api/v1alpha1"
	"github.com/kagent-dev/kagent/go/core/pkg/migrations"
	"github.com/stretchr/testify/require"
	"google.golang.org/adk/v2/server/adka2a/v2"
	"google.golang.org/adk/v2/tool/toolconfirmation"
	"google.golang.org/genai"
	"strings"
)

// canonicalize mirrors go/adk/pkg/a2a/metadata.go (unexported there): the
// runtime copies adk_type -> kagent.dev/a2a/part-type and adk_usage_metadata ->
// kagent.dev/a2a/usage, then deletes every adk_* key before the event leaves.
func fxCanonMeta(m map[string]any) {
	if m == nil {
		return
	}
	if v, ok := m[adka2a.ToA2AMetaKey("usage_metadata")]; ok {
		m[apia2a.UsageMetadataKey] = v
	}
	for k := range m {
		if strings.HasPrefix(k, "adk_") {
			delete(m, k)
		}
	}
}

func fxCanonPart(p *a2a.Part) {
	if p == nil || p.Metadata == nil {
		return
	}
	if v, ok := p.Metadata[adka2a.ToA2AMetaKey("type")]; ok {
		p.Metadata[apia2a.PartTypeMetadataKey] = v
	}
	fxCanonMeta(p.Metadata)
}

func fxCanonMessage(m *a2a.Message) {
	if m == nil {
		return
	}
	fxCanonMeta(m.Metadata)
	for _, p := range m.Parts {
		fxCanonPart(p)
	}
}

func fxCanonEvent(ev a2a.Event) {
	switch e := ev.(type) {
	case *a2a.TaskArtifactUpdateEvent:
		fxCanonMeta(e.Metadata)
		if e.Artifact != nil {
			fxCanonMeta(e.Artifact.Metadata)
			for _, p := range e.Artifact.Parts {
				fxCanonPart(p)
			}
		}
	case *a2a.TaskStatusUpdateEvent:
		fxCanonMeta(e.Metadata)
		fxCanonMessage(e.Status.Message)
	}
}

type fxConv struct {
	t       *testing.T
	c       *Client
	session *apiv1alpha1.Session
	app     string
	task    *a2a.Task
	partial a2a.ArtifactID
}

func (cv *fxConv) save(next *a2a.Task, ev a2a.Event) {
	cv.t.Helper()
	// Substrate checkpoints the actor at every boundary (terminal / input-required).
	snapshot := &SessionTaskSnapshot{Atespace: "kagent", URI: "s3://snapshots/" + uuid.NewString(), ContentScope: "DATA"}
	require.NoError(cv.t, saveRuntimeTask(cv.t, cv.c, cv.session.Id, next, ev, snapshot))
	cv.task = next
	time.Sleep(5 * time.Millisecond) // distinct created_at per event, as in a live run
}

// ask starts a new A2A task, as a2a-go's handler does for a fresh SendMessage.
func (cv *fxConv) ask(question string) {
	msg := a2a.NewMessage(a2a.MessageRoleUser, a2a.NewTextPart(question))
	msg.ContextID = cv.session.ContextId
	task := a2a.NewSubmittedTask(a2a.TaskInfo{TaskID: a2a.NewTaskID(), ContextID: cv.session.ContextId}, msg)
	msg.TaskID = task.ID
	cv.save(task, task)
	cv.status(a2a.TaskStateWorking, nil)
}

func (cv *fxConv) eventMeta(usage *genai.GenerateContentResponseUsageMetadata) map[string]any {
	meta := map[string]any{
		adka2a.ToA2AMetaKey("app_name"):      cv.app,
		adka2a.ToA2AMetaKey("user_id"):       cv.session.Creator,
		adka2a.ToA2AMetaKey("session_id"):    cv.session.ContextId,
		adka2a.ToA2AMetaKey("invocation_id"): "e-" + uuid.NewString(),
		adka2a.ToA2AMetaKey("author"):        cv.app[strings.LastIndex(cv.app, "__NS__")+6:],
	}
	if usage != nil {
		raw, _ := json.Marshal(usage)
		var m map[string]any
		_ = json.Unmarshal(raw, &m)
		meta[adka2a.ToA2AMetaKey("usage_metadata")] = m
	}
	return meta
}

// emit converts GenAI parts exactly like the ADK executor (OutputArtifactPerEvent).
func (cv *fxConv) emit(parts []*genai.Part, usage *genai.GenerateContentResponseUsageMetadata, partial bool) {
	cv.t.Helper()
	a2aParts := make([]*a2a.Part, 0, len(parts))
	for _, p := range parts {
		converted, err := adka2a.ToA2APart(p, nil)
		require.NoError(cv.t, err)
		a2aParts = append(a2aParts, converted)
	}
	ev := a2a.NewArtifactEvent(cv.task, a2aParts...)
	if cv.partial != "" {
		ev.Artifact.ID = cv.partial
		ev.Append = partial
	}
	ev.LastChunk = !partial
	if partial {
		cv.partial = ev.Artifact.ID
	} else {
		cv.partial = ""
	}
	ev.Artifact.Metadata = cv.eventMeta(usage)
	apia2a.SetTimelinePosition(ev.Artifact, time.Now())
	fxCanonEvent(ev)
	next, err := a2aevent.ApplyArtifactUpdate(cv.task, ev)
	require.NoError(cv.t, err)
	cv.save(next, ev)
}

func (cv *fxConv) status(state a2a.TaskState, msg *a2a.Message) {
	cv.t.Helper()
	ev := a2a.NewStatusUpdateEvent(cv.task, state, msg)
	now := time.Now()
	ev.Status.Timestamp = &now
	if msg != nil {
		msg.TaskID, msg.ContextID = cv.task.ID, cv.task.ContextID
		apia2a.SetTimelinePosition(msg, now)
	}
	fxCanonEvent(ev)
	next, err := a2aevent.ApplyStatusUpdate(cv.task, ev)
	require.NoError(cv.t, err)
	cv.save(next, ev)
}

func fxCall(id, name string, args map[string]any) *genai.Part {
	return &genai.Part{FunctionCall: &genai.FunctionCall{ID: id, Name: name, Args: args}}
}

func fxResult(id, name string, response map[string]any) *genai.Part {
	return &genai.Part{FunctionResponse: &genai.FunctionResponse{ID: id, Name: name, Response: response}}
}

// gateFires reproduces tool.confirmCall + generateRequestConfirmationEvent + the
// input-required processor + KAgentExecutor's BuildHITLStatusMessage.
func (cv *fxConv) gateFires(call *genai.Part, activated bool) string {
	cv.t.Helper()
	fc := call.FunctionCall
	cv.emit([]*genai.Part{fxResult(fc.ID, fc.Name, map[string]any{
		"error": `error tool "` + fc.Name + `" requires confirmation, please approve or reject`,
	})}, nil, false)
	approvalID := "adk-" + uuid.NewString()
	confirm := &genai.Part{FunctionCall: &genai.FunctionCall{
		ID: approvalID, Name: toolconfirmation.FunctionCallName,
		Args: map[string]any{
			"originalFunctionCall": fc,
			"toolConfirmation": toolconfirmation.ToolConfirmation{
				Hint: "Please approve or reject the tool call " + fc.Name +
					"() by responding with a FunctionResponse with an expected ToolConfirmation payload.",
			},
		},
	}}
	internal, err := adka2a.ToA2APart(confirm, []string{approvalID})
	require.NoError(cv.t, err)
	msg := kagentadka2a.BuildHITLStatusMessage(a2a.NewMessage(a2a.MessageRoleAgent, internal), activated)
	cv.status(a2a.TaskStateInputRequired, msg)
	return approvalID
}

// decide is the client's HITL response, re-emitted by KAgentExecutor.Execute as a
// working status update "for history/audit".
func (cv *fxConv) decide(approvalID string, approved bool, reason string) {
	decision := a2a.NewMessage(a2a.MessageRoleUser, a2a.NewDataPart(map[string]any{"decision_type": map[bool]string{true: "approve", false: "reject"}[approved]}))
	require.NoError(cv.t, apia2a.AttachHITL(decision, apia2a.ToolApprovalResponse{
		Type:      apia2a.HITLTypeToolApprovalResponse,
		Approvals: []apia2a.ToolApproval{{ID: approvalID, Approved: approved, RejectionReason: reason}},
	}))
	cv.status(a2a.TaskStateWorking, decision)
}

func (cv *fxConv) answer(text string) {
	cv.emit([]*genai.Part{{Text: text}}, &genai.GenerateContentResponseUsageMetadata{
		PromptTokenCount: 1200, CandidatesTokenCount: 60, TotalTokenCount: 1260,
	}, false)
	cv.status(a2a.TaskStateCompleted, nil)
}

func TestAuditFixture(t *testing.T) {
	dsn := os.Getenv("AUDIT_FIXTURE_DSN")
	if dsn == "" {
		t.Skip("AUDIT_FIXTURE_DSN not set")
	}
	ctx := context.Background()
	require.NoError(t, migrations.RunUpAsRole(ctx, dsn, "kagent_owner",
		migrations.BuiltinSourcesInSchema(true, "kagent", "extensions")))
	pool, err := Connect(ctx, &PostgresConfig{URL: dsn, Role: "kagent_owner", Schema: "kagent",
		VectorSchema: "extensions", VectorEnabled: true})
	require.NoError(t, err)
	c := NewClient(pool, "extensions")

	for _, agent := range []string{"k8s-agent", "k8s-reader", "homelab-knowledge"} {
		rev := agent + "-rev-1"
		require.NoError(t, c.UpsertAgentDefinition(ctx, AgentDefinition{
			Namespace: "kagent", AgentName: agent, AgentUID: agent + "-uid", DesiredRevision: rev}))
		require.NoError(t, c.RecordRuntimeRevision(ctx, RuntimeRevision{
			Revision: rev, Namespace: "kagent", AgentName: agent, AgentUID: agent + "-uid",
			SourceSnapshot: []byte("{}"), AgentCard: &a2apb.AgentCard{}, EgressDestinations: []string{},
			ActorTemplateAtespace: "kagent", ActorTemplateName: rev + "-actor-template", ActorTemplateUID: rev + "-actor-uid",
		}, true))
	}
	newConv := func(agent, name string) *fxConv {
		req := &apiv1alpha1.Session{Id: uuid.NewString(), Creator: "ari@example.test", Name: name,
			Agent: &apiv1alpha1.ResourceReference{Namespace: "kagent", Name: agent}}
		s, _, err := c.CreateSession(ctx, req, uuid.NewString())
		require.NoError(t, err)
		s, err = markSessionReady(ctx, c, s.Id, "agent.example")
		require.NoError(t, err)
		return &fxConv{t: t, c: c, session: s, app: "kagent__NS__" + strings.ReplaceAll(agent, "-", "_")}
	}
	names := map[string]string{}

	// approved-delete: the gate fires, a human approves, the tool runs.
	cv := newConv("k8s-agent", "approved-delete")
	cv.ask("Delete the configmap audit-fixture in namespace scratch.")
	del := fxCall("toolu_approved_delete", "k8s_delete_resource", map[string]any{
		"resource_type": "configmap", "resource_name": "audit-fixture", "namespace": "scratch"})
	cv.emit([]*genai.Part{{Text: "I'll delete it."}, del}, nil, false)
	id := cv.gateFires(del, true)
	cv.decide(id, true, "")
	cv.emit([]*genai.Part{fxResult(del.FunctionCall.ID, del.FunctionCall.Name, map[string]any{
		"content": []any{map[string]any{"type": "text", "text": `configmap "audit-fixture" deleted`}}})}, nil, false)
	cv.answer("Deleted configmap scratch/audit-fixture.")
	names["approved-delete"] = cv.session.Id

	// unapproved-write: a write tool bound without requireApproval.
	cv = newConv("k8s-agent", "unapproved-write")
	cv.ask("Label the scratch namespace team=platform.")
	lbl := fxCall("toolu_unapproved_label", "k8s_label_resource", map[string]any{
		"resource_type": "namespace", "resource_name": "scratch", "labels": "team=platform",
		"command": "kubectl label ns scratch team=platform --token=" + "ghp_" + strings.Repeat("Q", 36)})
	cv.emit([]*genai.Part{lbl}, nil, false)
	cv.emit([]*genai.Part{fxResult(lbl.FunctionCall.ID, lbl.FunctionCall.Name, map[string]any{
		"content": []any{map[string]any{"type": "text", "text": "namespace/scratch labeled"}}})}, nil, false)
	cv.answer("Labelled.")
	names["unapproved-write"] = cv.session.Id

	// read-only: a reader session, later deleted by its user (tombstone keeps session.data).
	cv = newConv("k8s-reader", "read-only")
	cv.ask("Which pods in kube-system are not Running?")
	get := fxCall("toolu_read_pods", "k8s_get_resources", map[string]any{"resource_type": "pod", "namespace": "kube-system"})
	cv.emit([]*genai.Part{get}, nil, false)
	cv.emit([]*genai.Part{fxResult(get.FunctionCall.ID, get.FunctionCall.Name, map[string]any{
		"content": []any{map[string]any{"type": "text", "text": "kind: Secret\ndata:\n  password: c3VwZXJzZWNyZXQ="}}})}, nil, false)
	cv.answer("All kube-system pods are Running.")
	names["read-only"] = cv.session.Id
	require.NoError(t, deleteSession(ctx, c, cv.session.Id))

	// rejected-delete: the gate fires and the human rejects.
	cv = newConv("k8s-agent", "rejected-delete")
	cv.ask("Delete the deployment audit-fixture in namespace scratch.")
	rej := fxCall("toolu_rejected_delete", "k8s_delete_resource", map[string]any{
		"resource_type": "deployment", "resource_name": "audit-fixture", "namespace": "scratch"})
	cv.emit([]*genai.Part{rej}, nil, false)
	id = cv.gateFires(rej, true)
	cv.decide(id, false, "not today")
	cv.emit([]*genai.Part{fxResult(rej.FunctionCall.ID, rej.FunctionCall.Name, map[string]any{
		"error": `error tool "k8s_delete_resource" call is rejected`})}, nil, false)
	cv.answer("The deletion was rejected, nothing changed.")
	names["rejected-delete"] = cv.session.Id

	// pending-patch: the gate fires with HITL NOT negotiated (text-only status) and
	// nobody answers. The tool never runs.
	cv = newConv("k8s-agent", "pending-patch")
	cv.ask("Scale scratch/audit-fixture to 0.")
	pat := fxCall("toolu_pending_patch", "k8s_patch_resource", map[string]any{
		"resource_type": "deployment", "resource_name": "audit-fixture", "namespace": "scratch",
		"patch": `{"spec":{"replicas":0}}`})
	cv.emit([]*genai.Part{pat}, nil, false)
	cv.gateFires(pat, false)
	names["pending-patch"] = cv.session.Id

	// mixed: one approved delete and, in the SAME session, an unapproved patch.
	cv = newConv("k8s-agent", "mixed")
	cv.ask("Delete scratch/old and annotate scratch/new.")
	md := fxCall("toolu_mixed_delete", "k8s_delete_resource", map[string]any{
		"resource_type": "configmap", "resource_name": "old", "namespace": "scratch"})
	cv.emit([]*genai.Part{md}, nil, false)
	id = cv.gateFires(md, true)
	cv.decide(id, true, "")
	cv.emit([]*genai.Part{fxResult(md.FunctionCall.ID, md.FunctionCall.Name, map[string]any{"content": []any{}})}, nil, false)
	mp := fxCall("toolu_mixed_annotate", "k8s_annotate_resource", map[string]any{
		"resource_type": "configmap", "resource_name": "new", "namespace": "scratch", "annotations": "a=b"})
	cv.emit([]*genai.Part{mp}, nil, false)
	cv.emit([]*genai.Part{fxResult(mp.FunctionCall.ID, mp.FunctionCall.Name, map[string]any{"content": []any{}})}, nil, false)
	cv.answer("Done.")
	names["mixed"] = cv.session.Id

	// eval: homelab-knowledge Q&A, streamed (partial chunks, then the final replace).
	cv = newConv("homelab-knowledge", "eval")
	cv.ask("Which namespace runs Vault?")
	cv.emit([]*genai.Part{{Text: "Vault runs "}}, nil, true)
	cv.emit([]*genai.Part{{Text: "in the "}}, nil, true)
	cv.emit([]*genai.Part{{Text: "Vault runs in the `vault` namespace."}}, &genai.GenerateContentResponseUsageMetadata{
		PromptTokenCount: 900, CandidatesTokenCount: 12, TotalTokenCount: 912}, false)
	cv.status(a2a.TaskStateCompleted, nil)
	cv.ask("And its storage backend?")
	docs := fxCall("toolu_eval_docs", "read_app_docs", map[string]any{"app": "vault"})
	cv.emit([]*genai.Part{docs}, nil, false)
	cv.emit([]*genai.Part{fxResult(docs.FunctionCall.ID, docs.FunctionCall.Name, map[string]any{"content": []any{}})}, nil, false)
	cv.answer("Raft integrated storage on a PVC; the token is " + "hvs." + strings.Repeat("Z", 24))
	names["eval"] = cv.session.Id

	// idle-deleted: an unapproved write whose session the idle sweeper later removes.
	cv = newConv("k8s-agent", "idle-deleted")
	cv.ask("Restart scratch/audit-fixture.")
	rs := fxCall("toolu_idle_patch", "k8s_patch_resource", map[string]any{
		"resource_type": "deployment", "resource_name": "audit-fixture", "namespace": "scratch",
		"patch": `{"spec":{"template":{"metadata":{"annotations":{"restartedAt":"now"}}}}}`})
	cv.emit([]*genai.Part{rs}, nil, false)
	cv.emit([]*genai.Part{fxResult(rs.FunctionCall.ID, rs.FunctionCall.Name, map[string]any{"content": []any{}})}, nil, false)
	cv.answer("Restarted.")
	names["idle-deleted"] = cv.session.Id
	del2, err := c.BeginIdleSessionDeletion(ctx, cv.session.Id, time.Now().Add(time.Hour))
	require.NoError(t, err)
	executor := uuid.New()
	claimed, err := c.ClaimSessionOperation(ctx, cv.session.Id, del2.Operation.ID, executor)
	require.NoError(t, err)
	require.True(t, claimed)
	_, err = c.FinishSessionOperation(ctx, cv.session.Id, del2.Operation.ID, executor, "", "", "")
	require.NoError(t, err)

	out, _ := json.MarshalIndent(names, "", "  ")
	require.NoError(t, os.WriteFile(os.Getenv("AUDIT_FIXTURE_SESSIONS"), out, 0o644))
}
