#!/usr/bin/env bash
# cekura-agent 3-minute demo (fully offline: fake planner, no network).
# Live variants are in SUBMISSION_CHECKLIST.md.
set -euo pipefail
cd "$(dirname "$0")"
CLI=".venv/bin/cekura-agent"
DEMO=$(mktemp -d)/livekit_basic
cp -R tests/fixtures/livekit_basic "$DEMO"

step() { printf '\n\033[1;36m== %s ==\033[0m\n' "$1"; }

step "1. inspect — framework, entrypoint, tools, variables, KB (read-only)"
$CLI inspect "$DEMO" | head -40

step "2. plan — constrained IntegrationPlan (validated, no writes)"
$CLI plan "$DEMO" --mode test --agent-id 42 | head -35

step "3. diff — exact dry-run patch"
$CLI diff "$DEMO" --mode test --agent-id 42

step "4. integrate --apply — autonomous run (patch + verify + rollback manifest)"
$CLI integrate "$DEMO" --mode test --agent-id 42 --apply | tail -22

step "5. feature configuration — mock tools / dynamic variables / KB desired state"
$CLI prepare-platform "$DEMO" --mode test --agent-id 42 | head -50

step "6. verify — lifecycle invariants on the patched repo"
$CLI verify "$DEMO" --mode test | tail -5

step "7. second apply — must be a zero-diff no-op"
$CLI integrate "$DEMO" --mode test --agent-id 42 --apply | grep -E "idempotence|exit code"

step "8. rollback — exact original hashes restored"
$CLI rollback "$DEMO"
$CLI verify "$DEMO" --mode test | tail -3 || true   # fails again: integration gone (expected)

step "9. safe refusal — unsupported repo exits 2 with a stable reason"
$CLI integrate tests/fixtures/readme_only --mode test | tail -6 || true

printf '\n\033[1;32mdemo complete\033[0m\n'
