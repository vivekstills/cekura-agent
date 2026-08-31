"""Mock tools: discovery -> synthetic variants -> Cekura desired-state objects + routing boundary.

- Tool names/schemas come verbatim from evidence (exact match matters: any casing or
  underscore mismatch on the platform silently never matches at runtime).
- Mock data is CLEARLY synthetic ("MOCK-..." values, 555 phone numbers) so it can never
  be mistaken for production data and never reaches a live backend.
- LiveKit and Pipecat test mode: the Cekura SDK auto-injects platform mocks registered on
  the dashboard. No routing code is needed in the customer repo.
- The explicit `MockToolRouter` below is an optional override for custom endpoints and is
  never activated implicitly; if no endpoint is configured, the SDK handles interception.
"""

from __future__ import annotations

import os
from typing import Any, Protocol

import httpx

from ..models import EvidenceKind, MockDataVariant, MockToolSpec
from ..scanner import InspectionResult

FREETEXT_NAMES = {"notes", "reason", "comment", "comments", "message", "description", "details"}

_TYPE_DEFAULTS: dict[str, Any] = {
    "str": "MOCK-VALUE", "string": "MOCK-VALUE",
    "int": 1, "integer": 1, "float": 1.0, "number": 1.0,
    "bool": True, "boolean": True, "any": "MOCK-VALUE",
}


def synthetic_value(name: str, type_name: str, variant: int = 0) -> Any:
    """Clearly-synthetic value; `variant` yields DISTINCT inputs per mock entry
    (the platform maps each input to exactly one output)."""
    lowered = name.lower()
    if "phone" in lowered:
        return f"+1555010{1 + variant:04d}"  # 555 fictional range
    if "date" in lowered:
        return f"2026-01-{15 + variant:02d}"
    if "time" in lowered:
        return f"{10 + variant}:00"
    if "email" in lowered:
        return f"mock.caller{variant + 1}@example.com"
    if "name" in lowered:
        return f"Mock Caller {variant + 1}"
    if "zip" in lowered or "postal" in lowered:
        return f"{variant:05d}"
    if lowered.endswith("_id") or lowered == "id" or "account" in lowered:
        return f"MOCK-ID-{variant + 1:04d}"
    if "amount" in lowered or "price" in lowered:
        return 10.0 * (variant + 1)
    base = _TYPE_DEFAULTS.get(type_name.lower(), "MOCK-VALUE")
    if isinstance(base, str):
        return base if variant == 0 else f"{base}-{('EMPTY', 'ERROR')[min(variant - 1, 1)]}"
    if isinstance(base, bool):
        return base
    if isinstance(base, (int, float)):
        return type(base)(base + variant)
    return base


def _params_of(detail: dict[str, Any]) -> dict[str, str]:
    params = detail.get("parameters") or {}
    normalized: dict[str, str] = {}
    for key, value in params.items():
        if isinstance(value, dict):  # JSON-schema property {"type": "string", ...}
            normalized[key] = str(value.get("type", "any"))
        else:
            normalized[key] = str(value)
    return normalized


def build_mock_tool_specs(inspection: InspectionResult) -> list[MockToolSpec]:
    by_name: dict[str, MockToolSpec] = {}
    for ev in inspection.evidence_map.of_kind(EvidenceKind.TOOL_DEF):
        name = str(ev.detail.get("name") or ev.symbol)
        params = _params_of(ev.detail)
        existing = by_name.get(name)
        if existing is not None and not params:
            existing.evidence_ids.append(ev.id)
            continue  # keep the richer (schema-bearing) definition

        success_output = {"status": "ok", "result": f"MOCK result for {name}", "mock": True}
        error_output = {"status": "error", "error": f"MOCK simulated failure of {name}", "mock": True}
        empty_output = {"status": "ok", "result": None, "mock": True,
                        "message": "MOCK empty result (no records found)"}
        if params:
            # distinct input per variant: the platform maps each input to exactly one output
            variants = [
                MockDataVariant(variant=label,
                                input={p: synthetic_value(p, t, i) for p, t in params.items()},
                                output=output)
                for i, (label, output) in enumerate(
                    [("success", success_output), ("empty", empty_output), ("error", error_output)])
            ]
        else:
            variants = [MockDataVariant(variant="success", input={}, output=success_output)]
        spec = MockToolSpec(
            name=name,
            description=str(ev.detail.get("description") or f"Mock for tool {name}"),
            parameters_schema={p: {"type": t} for p, t in params.items()},
            mock_data=variants,
            freetext_params=sorted(p for p in params if p.lower() in FREETEXT_NAMES),
            evidence_ids=[ev.id] + (existing.evidence_ids if existing else []),
        )
        by_name[name] = spec
    return [by_name[k] for k in sorted(by_name)]


def verify_mock_names(specs: list[MockToolSpec], inspection: InspectionResult) -> list[str]:
    """Exact-name cross-check; returns mismatches (must be empty)."""
    discovered = {
        str(e.detail.get("name") or e.symbol)
        for e in inspection.evidence_map.of_kind(EvidenceKind.TOOL_DEF)
    }
    return sorted({s.name for s in specs} ^ discovered)


# ------------------------------------------------------------------ routing boundary


class MockToolRouter(Protocol):
    """What a repo-side adapter must implement to route tool calls to mocks during tests."""

    def invoke(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...


class CekuraMockToolRouter:
    """Routes to Cekura's documented runtime mock endpoint.

    POST {base}/test_framework/v1/mock-tools/{tool_name}/invoke/
    Only constructed via `resolve_pipecat_router` — never used implicitly.
    """

    def __init__(self, base_url: str, api_key: str | None = None, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def invoke(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        headers = {"X-CEKURA-API-KEY": self.api_key} if self.api_key else {}
        resp = httpx.post(
            f"{self.base_url}/test_framework/v1/mock-tools/{tool_name}/invoke/",
            json=arguments, headers=headers, timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()


class LocalFakeMockToolRouter:
    """Test double: serves the spec's own variants; never touches the network."""

    def __init__(self, specs: list[MockToolSpec]) -> None:
        self._by_name = {s.name: s for s in specs}

    def invoke(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        spec = self._by_name.get(tool_name)
        if spec is None:
            raise KeyError(f"no mock configured for tool {tool_name!r} (exact-name match required)")
        del arguments
        return dict(spec.mock_data[0].output)


def resolve_pipecat_router(specs: list[MockToolSpec],
                           env: dict[str, str] | None = None) -> MockToolRouter | None:
    """Optional explicit router for Pipecat mock tools.

    By default the Cekura Pipecat SDK auto-injects dashboard-registered mocks, so this
    returns None. An explicit router is only returned when both CEKURA_USE_MOCK_TOOLS=1
    and a CEKURA_MOCK_ENDPOINT_BASE are configured.
    """
    env = dict(os.environ) if env is None else env
    if env.get("CEKURA_USE_MOCK_TOOLS") != "1":
        return None
    endpoint = env.get("CEKURA_MOCK_ENDPOINT_BASE")
    if not endpoint or not endpoint.startswith(("http://", "https://")):
        # SDK auto-intercepts when no explicit endpoint is provided.
        return None
    del specs
    return CekuraMockToolRouter(endpoint, api_key=env.get("CEKURA_API_KEY"))
