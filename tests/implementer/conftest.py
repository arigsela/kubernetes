# tests/implementer/conftest.py
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
import json

import yaml

IMPLEMENTER_READ_TOOLS = ["get_file_contents", "search_code", "list_commits", "get_commit",
                          "list_branches", "list_pull_requests", "pull_request_read"]
IMPLEMENTER_PROPOSE_TOOLS = ["create_branch", "create_or_update_file", "push_files",
                             "create_pull_request"]
# update_pull_request can close or retarget any PR (state, base): the contract forbids both and
# the flow never needs it (an existing branch is updated with create_or_update_file).
NEVER_LOADED = ["merge_pull_request", "update_pull_request", "update_pull_request_branch", "pull_request_review_write",
                "add_comment_to_pending_review", "delete_file", "create_repository",
                "delete_repository", "fork_repository", "create_pull_request_with_copilot"]
# Tool names the github-mcp-server v1.14.0 README lists for the repos and pull_requests
# toolsets. A name outside this list fails the server at startup (README, "Tool
# Configuration"), which would leave the agent silently without tools.
KNOWN_SERVER_TOOLS = {
    "add_comment_to_pending_review", "add_reply_to_pull_request_comment", "create_pull_request",
    "list_pull_requests", "merge_pull_request", "pull_request_read", "pull_request_review_write",
    "search_pull_requests", "update_pull_request", "update_pull_request_branch",
    "create_branch", "create_or_update_file", "create_repository", "delete_file",
    "delete_repository", "fork_repository", "get_commit", "get_file_contents",
    "get_latest_release", "get_release_by_tag", "get_tag", "list_branches", "list_commits",
    "list_releases", "list_repository_collaborators", "list_tags", "push_files", "search_code",
    "search_commits", "search_repositories",
}


def docs(rel):
    return [d for d in yaml.safe_load_all((REPO / rel).read_text()) if d]


def one(rel, kind, name=None):
    found = [d for d in docs(rel) if d.get("kind") == kind and (name is None or d["metadata"]["name"] == name)]
    assert len(found) == 1, (rel, kind, name)
    return found[0]


def taxonomy():
    cm = one("base-apps/admission-policies/agent-capability-taxonomy.yaml", "ConfigMap")
    return {k: json.loads(cm["data"][k]) for k in ("read", "propose", "write", "destructive")}
