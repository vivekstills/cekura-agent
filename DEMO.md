# cekura-agent live demo (one page)

## 1. What it does (30 seconds)

`cekura-agent` integrates the Cekura SDK into LiveKit/Pipecat customer repos. It places the tracer correctly, extracts mock tools, dynamic variables and KB documents, and reconciles the Cekura dashboard — all behind dry-run defaults and exact rollback.

## 2. Setup

```bash
python3 -m venv /tmp/cekura-demo && source /tmp/cekura-demo/bin/activate
pip install -e "/Users/vivek/Desktop/cekura agent"
export OPENROUTER_API_KEY=<key>   # for real Kimi
export CEKURA_API_KEY=<key>       # for live Cekura sync
```

## 3. Demo repo

```bash
REPO="/Users/vivek/Desktop/cekura agent/tests/fixtures/pipecat_single"
```

Small, supported, matches the live dashboard agent 22338.

## 4. Three-minute walkthrough

```bash
# 1. Inspect (read-only)
cekura-agent inspect "$REPO"
# Expected: pipecat, supported, 1 tool, 2 variables, 1 KB

# 2. Plan with Kimi K3
cekura-agent plan "$REPO" --mode test --agent-id 42 --model-mode openrouter
# Expected: valid plan with pipecat_single_step; cost ~$0.014

# 3. Plan offline (no key)
cekura-agent plan "$REPO" --mode test --agent-id 42 --model-mode fake
# Expected: same shape, $0 cost

# 4. Dry-run diff
cekura-agent diff "$REPO" --mode test --agent-id 42
# Expected: tracer, dep and env placeholder added; PipelineTask wrapped

# 5. Apply
cekura-agent integrate "$REPO" --mode test --agent-id 42 --apply
# Expected: 15 checks passed, exit 0

# 6. Verify
cekura-agent verify "$REPO" --mode test
# Expected: exit 0

# 7. Prepare Cekura desired state
cekura-agent prepare-platform "$REPO" --mode test --agent-id 42 --out /tmp/desired.json
# Expected: JSON with mock tools, variables, KB, dashboard_url

# 8. Sync with Cekura (requires key)
cekura-agent apply-platform --desired-state /tmp/desired.json --platform-mode staging --apply
# Expected: verified=true; tools/variables created; no unapproved KB upload

# 9. Idempotent second run
cekura-agent integrate "$REPO" --mode test --agent-id 42 --apply
# Expected: no-op, exit 0

# 10. Rollback
cekura-agent rollback "$REPO"
# Expected: .env.example, bot.py, requirements.txt restored
```

## 5. LiveKit vs. Pipecat

- **LiveKit:** `LiveKitTracer` + `track_session(...)` immediately before `session.start()`.
- **Pipecat:** per-call `PipecatTracer` + `track_and_create_task(pipeline, context, ...)` replaces raw `PipelineTask`.

## 6. Safe NEEDS_HUMAN refusal

```bash
cekura-agent integrate "/Users/vivek/Desktop/cekura agent/tests/fixtures/readme_only" --mode test
# Expected: exit 2, NEEDS_HUMAN: NO_FRAMEWORK
```

## 7. Dashboard link

**https://dashboard.cekura.ai/agents/22338**

Point out: `pipecat_single (pipecat)`, mock tool `order_lookup` (3 mappings), variables `caller_name` + `patient_id`, KB `pricing_guide.md`, `tracing_enabled: true`.

## 8. Offline fallback

```bash
# start a local fake Cekura server
python -c "from cekura_agent.platform.fake_server import FakeCekuraServer; s=FakeCekuraServer().start(); print(s.url); import time; time.sleep(300)"

# then run with fake planner + fake server
cekura-agent plan "$REPO" --mode test --model-mode fake
CEKURA_API_KEY=test-cekura-key cekura-agent apply-platform --desired-state /tmp/desired.json \
  --platform-mode offline --base-url <fake-url> --apply
```

## 9. Cleanup

```bash
cekura-agent rollback "$REPO"
deactivate && rm -rf /tmp/cekura-demo /tmp/desired.json
```

## 10. Likely Q&A

1. **Framework?** AST scan of imports + entrypoints; ambiguous/unsupported topologies refused.
2. **Overwrite code?** Dry-run default; apply only on matching hashes; rollback restores originals.
3. **Bad plan?** Schema, action, path and evidence validation reject it.
4. **No keys?** `--model-mode fake` and `--platform-mode offline` run locally.
5. **KB safety?** Approval-gated, 2 MB cap, path containment, PII flags.

## 11. Closing (20 seconds)

`cekura-agent` turns a fragile multi-step Cekura integration into one auditable command — Kimi planning, validated patches, verification, dashboard sync and safe rollback.
