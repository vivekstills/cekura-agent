Security Findings: cekura-agent
===============================

Legend: severity (Critical, High, Medium, Low, Info); status (OPEN, FIXED,
ACCEPTED, NOT_RUN).

----------------------------------------------------------------------
ID: F-001
Title: Scanner followed repository symlinks and could read files outside repo
Severity: High
Status: FIXED
Confidence: confirmed
Asset: customer source code / local filesystem
Trust boundary: repository input -> scanner
Attacker prerequisites: ability to place a symlink in the repo
Reproduction (synthetic):
    mkdir -p /tmp/audit-repo
    ln -s /etc/passwd /tmp/audit-repo/passwd
    python -c "from cekura_agent.scanner import take_snapshot; take_snapshot(Path('/tmp/audit-repo'))"
Actual result before fix: the symlink target was opened, hashed, and its
contents could enter the evidence model bundle.
Expected secure result: no read outside the canonical repo root.
Root cause: `iter_repo_files` used `path.is_file()` which follows symlinks
and never resolved/validated the path.
CWE: CWE-59 (link resolution), CWE-22 (path traversal)
ASVS/LLM mapping: V5.1, LLM07 (excessive agency / data disclosure)
Fix: skip symlinks and resolve+validate every candidate path inside root.
Files changed: src/cekura_agent/safety.py, tests/test_safety.py
Regression test: `tests/test_safety.py::test_iter_repo_files_skips_symlinks_to_outside`
Release blocking: no

----------------------------------------------------------------------
ID: F-002
Title: KB upload in `apply-platform` could read arbitrary files via path traversal
Severity: High
Status: FIXED
Confidence: confirmed
Asset: local filesystem / Cekura knowledge base
Trust boundary: desired-state JSON -> platform reconciler
Attacker prerequisites: a user or script runs `apply-platform` with a crafted
`CekuraDesiredState` JSON whose `kb_uploads[].path` contains `..` or is absolute.
Reproduction (synthetic):
    edit desired-state JSON: kb_uploads[0].path = "../secret.txt"
    cekura-agent apply-platform --desired-state state.json --platform-mode offline --base-url <fake>
Actual result before fix: `kb_files_root / k.path` resolved outside repo and
uploaded arbitrary bytes as a KB file.
Expected secure result: path rejected; upload confined to repo root.
Root cause: `reconcile.py` read `(kb_files_root / k.path).read_bytes()`
without containment or size cap.
CWE: CWE-22
ASVS/LLM mapping: V5.1
Fix: resolve through `ensure_within_root`; enforce 2 MB cap; added regression
      tests for path traversal and oversized files.
Files changed: src/cekura_agent/platform/reconcile.py, tests/test_platform.py
Regression tests:
    `tests/test_platform.py::test_kb_upload_rejects_path_traversal`
    `tests/test_platform.py::test_kb_upload_rejects_oversized_file`
Release blocking: no

----------------------------------------------------------------------
ID: F-003
Title: Read sites outside `iter_repo_files` do not re-validate containment
Severity: Low
Status: ACCEPTED with residual risk
Confidence: static
Asset: source code
Trust boundary: internal model data -> read helpers
Attacker prerequisites: a bug or bypass that puts an escaped path into
`inspection.snapshot.files`, `Evidence.file`, or `Record.path`.
Actual result: `_read` in `adapters/__init__.py`, `scan.py`, `evidence.py`,
`orchestrator.py` construct `root / rel` directly.
Expected secure result: every read guarded by `ensure_within_root`.
Root cause: validation happens upstream (in `validate_plan` for writes and
`iter_repo_files` for repo listing) but not at the read sink.
Fix: for defence in depth, future hardening should route all reads through
`ensure_within_root` or a helper. After the F-001 fix, `iter_repo_files`
normalizes all repo-relative paths and symlinks are skipped, so the residual
risk is low.
Release blocking: no

----------------------------------------------------------------------
ID: F-004
Title: KB PII / prompt-injection / secret detection is limited
Severity: Medium
Status: OPEN (accepted with documented risk)
Confidence: static
Asset: knowledge base / uploaded documents
Trust boundary: customer documents -> Cekura evaluator KB
Attacker prerequisites: a repo contains a document with PII, prompt injection,
secrets, or incorrect facts that the operator then approves.
Actual result: `_privacy_flags` only checks emails, phones, and SSNs. It does
not detect API keys, tokens, credit cards, addresses, hidden text, conflicting
facts, or prompt-injection markers.
Expected secure result: stronger content scanning and a human approval gate.
Root cause: scoped first pass; the manifest is always owner-approval-required.
Fix (short-term): keep `owner_approval_required=True` and warn on any
`privacy_flags` (already done). Fix (long-term): add secret/PII/prompt-injection
heuristics and a document-review workflow.
Release blocking: no (gated by human approval)

----------------------------------------------------------------------
ID: F-005
Title: Dynamic variable names/values are not validated for length, Unicode, or reserved names
Severity: Low
Status: OPEN
Confidence: static
Asset: Cekura dynamic variables
Trust boundary: repo source -> platform upsert
Actual result: the upsert payload sends only `name` and `description`; the
values come from the runtime simulator, not the repo. However, no validation
prevents names like `__proto__` or very long Unicode strings.
Expected secure result: sanitize names and bound lengths.
Root cause: early feature scope.
Fix: add length / charset / reserved-name validation in `build_dynamic_variable_specs`.
Release blocking: no

----------------------------------------------------------------------
ID: F-006
Title: Full adversarial audit not completed for all 16 phases
Severity: Info
Status: NOT_RUN
Confidence: n/a
Asset: all
Attacker prerequisites: n/a
Actual result: fast submission security gate and targeted adversarial tests
completed; deep dependency SBOM, full concurrency/fault-injection, and runtime
LiveKit/Pipecat lifecycle adversarial tests were not run due to time.
Expected secure result: complete remaining phases with evidence.
Release blocking: no, provided remaining items are tracked and run before
production security-verified status is claimed.
