# Decisions

1. **Planner-JSON-only model.** Kimi K3 (`moonshotai/kimi-k3`, verified on OpenRouter with
   `tools`/`structured_outputs`) plans; the host executes. Reliability and safety beat
   free-form agent edits for a repeatable integration task.
2. **SDK pin `cekura[...] >=1.6.5`.** Verified live on PyPI (both `livekit` and `pipecat`
   extras). Cekura's own skills repo pins 1.2.0/1.4.1 — stale relative to the current docs
   (which use >=1.6.5); we follow the docs and verify resolvability at run time in staging mode.
3. **Host-known params are normalized, not model-echoed.** The live Kimi canary omitted the
   `mode` param on the pipecat action; we `setdefault` host-known values (mode, agent_id) and
   still reject contradictions. (Found via a real $0.01 canary run.)
4. **Mock-data inputs must be pairwise distinct.** Discovered against the real platform:
   `PATCH mock_tools` 400s when two variants share one input ("a tool must map each input to
   exactly one output"). Zero-param tools therefore get a single success variant.
5. **`knowledge_base_files` are objects.** The real API returns file objects, not names; the
   reconciler normalizes and the fake server mirrors the real shape.
6. **Agent creation needs a connection method.** Real-API validation requires provider
   credentials or a connection. Policy: use customer LiveKit creds from env when present;
   otherwise try the no-config chat placeholders (whatsapp, then self_hosted where allowed)
   with an explicit warning; otherwise raise `PROVIDER_CONNECTION_REQUIRED`. LiveKit accepts
   no placeholder without creds → honest blocker. Pipecat accepted `whatsapp` → live agent 22338.
7. **Project auto-resolution.** API keys are project-scoped; `GET /user/v1/projects/` returning
   exactly one project resolves it (warned); multiple → `PROJECT_REQUIRED` refusal.
8. **`PipelineWorker` (newer pipecat runner API) is refused, not guessed.** The documented
   Cekura contract targets `PipelineTask`; repos on the worker API get
   `PIPECAT_WORKER_API` NEEDS_HUMAN until SDK support is confirmed. Found in current
   pipecat-examples.
9. **Both LiveKit and Pipecat mock tools are auto-injected by the SDK.** The platform
   `mock_tools` registration is the source of truth; the SDKs intercept tool calls at runtime.
   We still ship a typed `MockToolRouter` for custom/override endpoints, but it is only activated
   when both `CEKURA_USE_MOCK_TOOLS=1` and `CEKURA_MOCK_ENDPOINT_BASE` are explicitly set.
10. **Dynamic-variable exclusions.** Placeholders in non-executable files (README/docs) are
    recorded as rejected evidence and excluded from registration; structural runtime inputs
    (`ctx.job.metadata` keys, `session_data` keys) are registered even without `{{ }}`.
11. **No `both` mode.** `--mode test|observe` is explicit; mixing methods on one session is a
    documented no-no (duplicate records / wrong capture profile).
12. **Deletions require approval.** `mock_tools` is full-list-replace on the platform; the
    reconciler diffs first and refuses unapproved deletions (`DELETION_REQUIRES_APPROVAL`).
13. **In-memory syntax checks.** `py_compile` wrote `__pycache__` into a customer repo during
    testing; replaced with in-memory `compile()` (caught by the rollback hash gate).
