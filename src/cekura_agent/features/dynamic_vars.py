"""Dynamic variables: discovery -> typed definitions with framework-specific source->sink.

Registration rule (Cekura docs): register anything the agent reads at runtime that varies
per run, regardless of delivery mechanism. Excluded automatically: placeholders in
non-executable files (rejected evidence), docstring examples, KB/README patterns.

Sinks (documented):
- LiveKit WebRTC tests: Cekura injects test-profile values into `ctx.job.metadata`;
  read via `cekura.get_simulation_data(ctx)["test_profile_data"]` AFTER `ctx.connect()`.
  Phone-based runs receive an EMPTY object — code must not depend on it for phone calls.
- Pipecat automated tests: Cekura merges `main_agent_variables` as TOP-LEVEL keys of the
  session body (`SessionParams.data` -> `session_data`); read via `session_data.get("<name>")`.
  `testing_agent_variables` stay on the simulated caller and never reach the agent.
"""

from __future__ import annotations

from ..models import DynamicVariableSpec, EvidenceKind, Framework
from ..scanner import InspectionResult

_EXAMPLES = {
    "phone": "+15550100001", "date": "2026-01-15", "time": "10:00",
    "name": "Jane Doe", "id": "ACC-12345", "zip": "94107", "email": "jane@example.com",
}


def _example_for(name: str) -> str:
    lowered = name.lower()
    for hint, example in _EXAMPLES.items():
        if hint in lowered:
            return example
    return "example-value"


def _sink_for(framework: Framework, name: str, source: str) -> str:
    if framework == Framework.LIVEKIT:
        return (
            f"WebRTC tests: cekura.get_simulation_data(ctx)['test_profile_data']['{name}'] "
            "(call after ctx.connect(); returns an EMPTY object for phone-based runs)"
            if source != "job_metadata"
            else f"ctx.job.metadata JSON key '{name}' (Cekura test profiles inject via dispatch metadata)"
        )
    if framework == Framework.PIPECAT:
        return (
            f"session_data.get('{name}') — Cekura merges test-profile main_agent_variables "
            "as top-level session body keys on the automated (WebRTC) route"
        )
    return "unknown framework sink"


def build_dynamic_variable_specs(inspection: InspectionResult) -> list[DynamicVariableSpec]:
    framework = inspection.matrix.framework
    emap = inspection.evidence_map
    by_name: dict[str, DynamicVariableSpec] = {}

    for ev in emap.of_kind(EvidenceKind.PROMPT_PLACEHOLDER):
        name = str(ev.symbol)
        context = str(ev.detail.get("context", ""))[:120].replace("\n", " ")
        spec = DynamicVariableSpec(
            name=name,
            description=(
                f"Prompt placeholder {{{{{name}}}}} read by the agent's system prompt "
                f"({ev.file}:{ev.line_start}). String. Used in context: \"{context}...\". "
                f"Example: \"{_example_for(name)}\". If missing, the raw placeholder text "
                "leaks into the conversation."
            ),
            var_type="string",
            example=_example_for(name),
            source=f"prompt placeholder in {ev.file}",
            sink=_sink_for(framework, name, "placeholder"),
            main_agent=True,
            evidence_ids=[ev.id],
        )
        by_name[name] = spec

    for ev in emap.of_kind(EvidenceKind.RUNTIME_INPUT):
        key = ev.detail.get("key")
        if not key:
            continue
        name = str(key)
        source = str(ev.detail.get("source", "runtime input"))
        if name in by_name:
            existing = by_name[name]
            existing.evidence_ids.append(ev.id)
            existing.description += (
                f" ALSO read structurally from {source} at {ev.file}:{ev.line_start} — "
                "the same value must be delivered on both paths."
            )
            existing.source += f" + {source}"
            continue
        by_name[name] = DynamicVariableSpec(
            name=name,
            description=(
                f"Structural runtime input `{name}` read from {source} at {ev.file}:{ev.line_start} "
                f"(never appears as a {{{{placeholder}}}}). String unless code casts it. "
                f"Example: \"{_example_for(name)}\". The agent's behaviour depends on it per call, "
                "so Cekura must generate/inject it for every simulated run."
            ),
            var_type="string",
            example=_example_for(name),
            source=f"{source} at {ev.file}:{ev.line_start}",
            sink=_sink_for(framework, name, source),
            main_agent=True,
            evidence_ids=[ev.id],
        )

    return [by_name[k] for k in sorted(by_name)]
