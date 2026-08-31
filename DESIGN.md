# Design

## Pipeline

```
inspect ──► plan ──► validate ──► patch ──► verify ──► platform reconcile ──► report
(scanner)  (Kimi K3 │ fake)      (adapters) (AST gates) (GET → diff → apply-once → GET-after)
```

1. **Scanner** (`scanner/`) — deterministic AST analysis. Produces:
   - `RepoSnapshot` (hash-stamped file list; every later stage checks the fingerprint),
   - `EvidenceMap` (every claim has file:line + snippet; rejected candidates stay visible with reasons),
   - `CapabilityMatrix` (supported / needs_human with stable reason codes).
2. **Planner** (`planner/`) — `--model-mode openrouter` sends a redacted evidence bundle to
   exact model `moonshotai/kimi-k3` and accepts ONLY strict `PlannerOutput` JSON (enum-closed
   action vocabulary, no tools, no shell, no file access). `--model-mode fake` is the
   deterministic reference planner used offline. Both paths flow through the **semantic
   validator**: stale fingerprints, unknown/rejected evidence, out-of-matrix actions, paths
   outside the repo, edits to tests, and mode contradictions are all rejected. Host-known
   values (mode, agent_id) are normalized, never trusted to model echo.
3. **Adapters** (`adapters/`) — deterministic host transformations, anchored by AST and applied
   as bottom-up line edits:
   - LiveKit: module-scope `LiveKitTracer`; `track_session(ctx, session, agent)` /
     `observe_session(ctx, session)` inserted strictly before the statement executing
     `session.start(...)` (handles `await`, `asyncio.create_task(...)`, plain calls).
   - Pipecat: per-call `PipecatTracer`; single-step `track/observe_and_create_task`
     replacement, or multi-step `track/observe_pipeline` + `PipelineTask(enable_tracing=True,
     enable_turn_tracking=True, <original kwargs>)` + `register_task_handlers` when custom
     kwargs exist. Aggregator pair presence is a hard gate (the SDK silently disables without it).
4. **Patch executor** (`patching.py`) — compare-before-write, per-run backups inside
   `<repo>/.cekura-agent/`, all-or-nothing apply, `RollbackManifest`, exact-hash rollback.
5. **Verification** (`verification/`) — in-memory syntax compile plus lifecycle invariants
   (tracer-before-start, track has agent arg, module-scope vs per-call tracer, tracing flags,
   handler-registration order, mode consistency, exactly-once, no direct-observe duplication,
   api_key from env) and a post-patch re-inspection.
6. **Features** (`features/`) — host-computed desired-state specs:
   - mock tools: exact runtime names/schemas; clearly synthetic success/empty/error variants
     with **distinct inputs** (platform maps each input to exactly one output);
   - dynamic variables: prompt placeholders + structural runtime inputs with framework-exact
     source→sink (LiveKit `get_simulation_data` / job metadata; Pipecat `session_data` keys);
   - KB: approval-gated manifest (checksums, privacy flags); discovery never uploads;
   - monitoring: explicit test/observe semantics + existing-instrumentation detection.
7. **Platform** (`platform/`) — typed client (`X-CEKURA-API-KEY`), faithful local fake server,
   reconciliation: GET → normalize → exact add/update/unchanged/delete diff → deletion
   approval → apply once → GET-after **exact value** comparison (never counts). Mutations are
   never blindly retried: a timeout-after-commit re-GETs and compares.
8. **Report** (`report.py`) — per-capability status labels
   (`IMPLEMENTED_AND_OFFLINE_VERIFIED` / `LIVE_VERIFIED` / `BLOCKED_BY_ACCESS_OR_DEPENDENCY` /
   `NEEDS_HUMAN_UNSUPPORTED_TOPOLOGY` / `NOT_RUN`), check results, stable reason/blocker codes,
   model usage + cost. Exit codes: 0 success, 1 failure, 2 NEEDS_HUMAN.

## Why the model never edits files

Kimi K3 chooses from a closed action vocabulary against cited evidence; a deterministic host
performs every edit and API call. A hostile or confused model output is inert: unknown fields,
invented files/evidence, out-of-mode actions and test edits are rejected before anything runs.

## Trust boundaries

- repo boundary: every write resolved under the repo root (`safety.ensure_within_root`)
- secrets: env-only, redacted in all output, scanned for before packaging
- network: `--model-mode fake` + `--platform-mode offline` make no external connections
  (asserted by a socket guard in tests); live modes are explicit flags
