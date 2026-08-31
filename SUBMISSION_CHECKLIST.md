# Submission checklist

## Install & verify (fresh terminal)
```bash
cd "cekura agent"
python3.12 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest            # expected: 99 passed
.venv/bin/ruff check src tests
```

## 3-minute offline demo
```bash
./demo.sh                   # inspect → plan → dry-run diff → apply → features → verify
                            # → no-op second apply → rollback → safe refusal
```

## Live demo (real keys in .env)
```bash
# 1) platform: register mock tools + dynamic variables + KB on the real dashboard
.venv/bin/cekura-agent prepare-platform tests/fixtures/pipecat_single --mode test --out reports/desired.json
.venv/bin/cekura-agent apply-platform --desired-state reports/desired.json --platform-mode staging --apply
# -> open the printed dashboard URL (existing proof: https://dashboard.cekura.ai/agents/22338)

# 2) autonomous run with LIVE platform verification + real Kimi K3 planning
.venv/bin/cekura-agent integrate <repo> --mode test --model-mode openrouter \
    --platform-mode staging --agent-id <id> --apply --e2e --report reports/run.json
```

## What to check
- [ ] `pytest` 99 passed; ruff clean
- [ ] `eval/matrix.md` — offline E2E passes + honest classification of the six CEK-8066 repos
- [ ] Dashboard agent 22338 shows mock tools, dynamic variables, KB (GET-after verified)
- [ ] Second `integrate --apply` is a zero-diff no-op; `rollback` restores exact hashes
- [ ] No secrets in the tree (`.env` is untracked; redaction + scan gates in tests)
- [ ] Status labels distinguish offline-verified / LIVE-verified / blocked / needs-human / not-run

## Known blockers & next steps (honest)
- LiveKit platform agent creation needs customer LiveKit creds (`PROVIDER_CONNECTION_REQUIRED`)
- Scenario-run E2E (`scenario_run_e2e`) is NOT_RUN — wire `run_scenarios_*` next
- LiveKit JS/TS repos out of v1 scope; `PipelineWorker` API pending SDK confirmation
- Rotate the OpenRouter + Cekura keys after the trial (shared in chat)

## AI assistance disclosure
Built with an AI coding agent (Devin); every slice human-gated per the five-hour plan;
all documented behaviors verified by the test suite or live runs recorded in EVALUATION.md.
