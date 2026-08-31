"""Desired-state reconciliation for the Cekura platform.

Order of operations (per collection):
GET current -> normalize -> exact add/update/unchanged/delete diff -> (deletions need
explicit approval) -> snapshot previous state -> apply ONCE -> GET-after -> exact
value comparison (never counts). A MaybeCommitted failure re-GETs and compares instead
of blindly retrying, so a committed-but-timed-out mutation is never duplicated.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import CEKURA_DASHBOARD_URL
from ..errors import NeedsHuman, PlatformContractError
from ..models import CekuraDesiredState, DynamicVariableSpec, MockToolSpec
from .client import CekuraClient, MaybeCommitted

# ------------------------------------------------------------------ payload shaping


def mock_tool_payload(spec: MockToolSpec) -> dict[str, Any]:
    return {
        "name": spec.name,
        "description": spec.description,
        "mock_data": [{"input": v.input, "output": v.output} for v in spec.mock_data],
        "freetext_params": spec.freetext_params,
    }


def dynvar_payload(spec: DynamicVariableSpec) -> dict[str, Any]:
    return {"name": spec.name, "description": spec.description}


def _normalize_tool(raw: dict[str, Any]) -> dict[str, Any]:
    """Server responses may add ids/timestamps; compare only the contract fields."""
    return {
        "name": raw.get("name"),
        "description": raw.get("description", ""),
        "mock_data": [
            {"input": m.get("input", {}), "output": m.get("output", {})}
            for m in raw.get("mock_data", [])
        ],
        "freetext_params": raw.get("freetext_params", []),
    }


def _normalize_var(raw: dict[str, Any]) -> dict[str, Any]:
    return {"name": raw.get("name"), "description": raw.get("description", "")}


def _kb_names(items: Any) -> set[str]:
    """The platform returns knowledge_base_files as objects; normalize to file names."""
    names: set[str] = set()
    for item in items or []:
        if isinstance(item, str):
            names.add(Path(item).name)
        elif isinstance(item, dict):
            for key in ("name", "file_name", "filename", "file", "title"):
                value = item.get(key)
                if isinstance(value, str) and value:
                    names.add(Path(value).name)
                    break
    return names


def diff_named(current: list[dict], desired: list[dict]) -> dict[str, list[str]]:
    cur = {c["name"]: c for c in current}
    des = {d["name"]: d for d in desired}
    adds = sorted(des.keys() - cur.keys())
    deletes = sorted(cur.keys() - des.keys())
    updates, unchanged = [], []
    for name in sorted(des.keys() & cur.keys()):
        (updates if des[name] != cur[name] else unchanged).append(name)
    return {"add": adds, "update": updates, "unchanged": unchanged, "delete": deletes}


# ------------------------------------------------------------------ reconcile


def _by_name(items: list[dict]) -> dict[str, dict]:
    return {str(item.get("name")): item for item in items}


def _apply_mutation(client_call, verify_call, expected: list[dict], describe: str):
    """Apply once; on MaybeCommitted re-GET and compare (by name, order-insensitive)
    instead of retrying — a committed mutation must never be duplicated."""
    try:
        client_call()
    except MaybeCommitted:
        actual = verify_call()
        if _by_name(actual) != _by_name(expected):
            raise PlatformContractError(
                f"{describe}: mutation may have failed mid-flight and server state does not "
                "match desired state — manual reconciliation required"
            ) from None
        return {"recovered_from_timeout": True}
    return {}


def reconcile(client: CekuraClient, desired: CekuraDesiredState, *, apply: bool,
              approve_deletions: bool = False,
              kb_files_root: Path | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"applied": apply, "warnings": [], "verified": None}

    # ---- resolve / create the agent
    if desired.agent_id is not None:
        agent = client.get_agent(desired.agent_id)
        agent_id = desired.agent_id
        result["agent"] = {"action": "reuse", "id": agent_id, "name": agent.get("name")}
    else:
        if desired.agent is None:
            raise NeedsHuman("NO_AGENT_TARGET", "desired state has neither agent_id nor an agent spec")
        project = desired.agent.project
        if project is None:
            projects = client.list_projects()
            if len(projects) == 1:  # project-scoped API keys see exactly their own project
                project = int(projects[0]["id"])
                result["warnings"].append(
                    f"project auto-resolved to the key's single visible project "
                    f"{project} ({projects[0].get('name')})"
                )
            else:
                raise NeedsHuman(
                    "PROJECT_REQUIRED",
                    f"agent creation needs --project-id: the API key sees {len(projects)} project(s); "
                    "refusing to pick one arbitrarily",
                )
        import os

        config = dict(desired.agent.provider_config)
        credentials: dict[str, Any] = {"config": config}
        if desired.agent.provider_type == "livekit":
            # The platform requires a connection method for LiveKit agents. Use the
            # customer's own LiveKit credentials from the environment — never invent them.
            lk_url, lk_key, lk_secret = (os.environ.get("LIVEKIT_URL"),
                                         os.environ.get("LIVEKIT_API_KEY"),
                                         os.environ.get("LIVEKIT_API_SECRET"))
            if lk_url and lk_key and lk_secret:
                credentials["api_key"] = lk_key
                config.setdefault("url", lk_url)
                config.setdefault("api_secret", lk_secret)
            else:
                result["warnings"].append(
                    "no LIVEKIT_URL/LIVEKIT_API_KEY/LIVEKIT_API_SECRET in the environment; "
                    "agent creation may be rejected until a connection method is configured"
                )
        payload = {
            "name": desired.agent.name,
            "description": desired.agent.description,
            "project": project,
            "provider": {
                "type": desired.agent.provider_type,
                "credentials": credentials,
            },
        }
        if not apply:
            result["agent"] = {"action": "create (dry-run)", "payload_name": desired.agent.name}
            result["mock_tools_diff"] = diff_named([], [mock_tool_payload(t) for t in desired.mock_tools])
            result["dynamic_variables_diff"] = diff_named(
                [], [dynvar_payload(v) for v in desired.dynamic_variables])
            result["kb_planned_uploads"] = [k.path for k in desired.kb_uploads if k.approved]
            result["dashboard_url"] = desired.dashboard_url or CEKURA_DASHBOARD_URL
            return result
        from ..errors import AgentError, BlockedByAccess

        try:
            agent = client.create_agent(payload)
        except AgentError as exc:
            if "connection method" not in str(exc) and "credentials" not in str(exc):
                raise
            # The platform requires SOME connection method at creation time. Provider
            # credentials belong to the customer; rather than inventing them we fall back
            # to the no-config chat connection (sms) as an explicit, warned placeholder —
            # mock tools / dynamic variables / KB are independent of the connection type.
            placeholders = [
                {"type": "whatsapp"},
                {"type": "self_hosted",
                 "config": {"url": "wss://placeholder.invalid/replace-with-real-agent-endpoint"}},
            ]
            agent = None
            last_exc: AgentError | None = None
            for chat_details in placeholders:
                fallback = dict(payload)
                fallback["provider"] = dict(payload["provider"])
                fallback["provider"]["chat_agent_details"] = chat_details
                try:
                    agent = client.create_agent(fallback)
                    result["warnings"].append(
                        f"agent created with a PLACEHOLDER '{chat_details['type']}' chat connection "
                        "because no provider connection credentials were available — replace with the "
                        "real connection (LiveKit creds / Pipecat Cloud key / phone number) in the "
                        "dashboard before running tests"
                    )
                    break
                except AgentError as exc2:
                    last_exc = exc2
            if agent is None:
                raise BlockedByAccess(
                    "PROVIDER_CONNECTION_REQUIRED",
                    f"the platform requires provider connection credentials to create this agent "
                    f"(original error: {exc}; placeholder fallbacks also rejected: {last_exc}). "
                    "Provide them via the environment (e.g. LIVEKIT_URL/LIVEKIT_API_KEY/"
                    "LIVEKIT_API_SECRET) or create the agent in the dashboard and pass --agent-id.",
                ) from last_exc
        agent_id = int(agent["id"])
        result["agent"] = {"action": "created", "id": agent_id, "name": agent.get("name")}

    dashboard_url = f"{CEKURA_DASHBOARD_URL}/agents/{agent_id}"
    result["dashboard_url"] = dashboard_url

    # ---- mock tools (full-list replace semantics)
    current_tools = [_normalize_tool(t) for t in client.get_mock_tools(agent_id)]
    desired_tools = [mock_tool_payload(t) for t in desired.mock_tools]
    tools_diff = diff_named(current_tools, desired_tools)
    result["mock_tools_diff"] = tools_diff
    if tools_diff["delete"] and not approve_deletions:
        if apply:
            raise NeedsHuman(
                "DELETION_REQUIRES_APPROVAL",
                f"reconciling mock_tools would DELETE {tools_diff['delete']} from the platform "
                "(full-list replace). Re-run with --approve-deletions to confirm, or include them "
                "in the desired state.",
            )
        result["warnings"].append(
            f"apply would delete mock tools {tools_diff['delete']} — requires --approve-deletions"
        )
    # keep unapproved-deletion entries by merging them back in (defensive; only when approved=False)
    final_tools = desired_tools if approve_deletions else desired_tools + [
        t for t in current_tools if t["name"] in tools_diff["delete"]
    ]
    result["previous_state"] = {"mock_tools": current_tools}

    if apply:
        recovery = _apply_mutation(
            lambda: client.set_mock_tools(agent_id, final_tools),
            lambda: [_normalize_tool(t) for t in client.get_mock_tools(agent_id)],
            final_tools, "mock_tools PATCH",
        )
        if recovery:
            result["warnings"].append("mock_tools: recovered from timeout-after-commit via GET-after")

    # ---- dynamic variables (array upsert; platform has no delete route)
    current_vars = [_normalize_var(v) for v in client.get_dynamic_variables(agent_id)]
    desired_vars = [dynvar_payload(v) for v in desired.dynamic_variables]
    vars_diff = diff_named(current_vars, desired_vars)
    result["dynamic_variables_diff"] = vars_diff
    if vars_diff["delete"]:
        result["warnings"].append(
            f"dynamic variables {vars_diff['delete']} exist on the platform but not in the repo; "
            "the upsert API cannot delete — review manually in the dashboard"
        )
    result["previous_state"]["dynamic_variables"] = current_vars

    if apply and desired_vars:
        recovery = _apply_mutation(
            lambda: client.upsert_dynamic_variables(agent_id, desired_vars),
            lambda: [_normalize_var(v) for v in client.get_dynamic_variables(agent_id)],
            _merge_vars(current_vars, desired_vars), "dynamic-variables POST",
        )
        if recovery:
            result["warnings"].append("dynamic_variables: recovered from timeout via GET-after")

    # ---- knowledge base (approved entries only; upload-only, never delete)
    approved = [k for k in desired.kb_uploads if k.approved]
    existing_files = _kb_names(agent.get("knowledge_base_files"))
    to_upload = [k for k in approved if Path(k.path).name not in existing_files]
    result["kb_planned_uploads"] = [k.path for k in to_upload]
    skipped = [k.path for k in desired.kb_uploads if not k.approved]
    if skipped:
        result["warnings"].append(f"KB entries not approved, NOT uploaded: {skipped}")
    if apply and to_upload:
        if kb_files_root is None:
            raise NeedsHuman("KB_ROOT_MISSING", "KB uploads require the repo root for file content")
        from ..safety import ensure_within_root

        files: list[tuple[str, bytes]] = []
        for k in to_upload:
            resolved = ensure_within_root(kb_files_root, Path(k.path))
            if resolved.stat().st_size > 2_000_000:
                raise NeedsHuman("KB_FILE_TOO_LARGE", f"{k.path} exceeds 2 MB upload limit")
            files.append((resolved.name, resolved.read_bytes()))
        client.upload_knowledge_base(agent_id, files)

    # ---- GET-after exact verification (values, not counts)
    if apply:
        after_tools = [_normalize_tool(t) for t in client.get_mock_tools(agent_id)]
        after_vars = [_normalize_var(v) for v in client.get_dynamic_variables(agent_id)]
        after_agent = client.get_agent(agent_id)
        mismatches: list[str] = []
        if {t["name"]: t for t in after_tools} != {t["name"]: t for t in final_tools}:
            mismatches.append("mock_tools GET-after does not exactly match desired state")
        for var in desired_vars:
            actual = next((v for v in after_vars if v["name"] == var["name"]), None)
            if actual != var:
                mismatches.append(f"dynamic variable {var['name']} GET-after mismatch")
        kb_after = _kb_names(after_agent.get("knowledge_base_files"))
        for entry in approved:
            if Path(entry.path).name not in kb_after:
                mismatches.append(f"KB file {entry.path} missing after upload")
        result["verified"] = not mismatches
        result["mismatches"] = mismatches
        result["after"] = {"mock_tools": after_tools, "dynamic_variables": after_vars,
                           "knowledge_base_files": sorted(kb_after)}
        if mismatches:
            raise PlatformContractError(
                "platform state does not exactly match desired state after apply: "
                + "; ".join(mismatches),
                detail=str(result),
            )
    return result


def _merge_vars(current: list[dict], desired: list[dict]) -> list[dict]:
    merged = {v["name"]: v for v in current}
    for var in desired:
        merged[var["name"]] = var
    return sorted(merged.values(), key=lambda v: v["name"])
