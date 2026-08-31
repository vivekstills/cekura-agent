Security Test Matrix: cekura-agent
==================================

Control | Test | Result | Evidence
--------|------|--------|----------
PHASE 0 — Freeze and baseline
Commit SHA recorded | `git rev-parse --short HEAD` | PASS | `ba4fa80`
Dirty state clean | `git status --short` | PASS | no output
Existing lint | `ruff check src tests` | PASS | "All checks passed!"
Existing tests | `python -m pytest` | PASS | `102 passed in 11.76s`
Python/OS version | `python --version`, `uname -a` | PASS | 3.12.13 / Darwin ARM64

PHASE 2 — Static code review
No `shell=True` | `grep shell=True` | PASS | no matches
No `eval/exec` of model output | `grep eval\(|exec\(` | PASS | only `compile` (syntax check) and `ast.literal_eval`
No unsafe deserialization | `grep pickle|yaml.load` | PASS | no matches
No disabled TLS | `grep verify=False` | PASS | no matches
No hardcoded secrets | `grep sk-or-v1|BEGIN PRIVATE` | PASS | only env/placeholder references and test canaries
Secret redaction | `tests/test_safety.py::test_redaction` | PASS | 4/4 tests

PHASE 4 — Filesystem containment
Relative `../` traversal blocked | `tests/test_safety.py` + manual | PASS | `SafetyViolation` raised
Absolute path outside blocked | `tests/test_safety.py` + manual | PASS | `SafetyViolation` raised
Symlink to outside skipped | `tests/test_safety.py::test_iter_repo_files_skips_symlinks_to_outside` | PASS | 5/5 tests
Large files skipped | `iter_repo_files` 2 MB cap | PASS | unit test
SKIP dirs respected | `tests/test_safety.py::test_iter_repo_files_skips_dirs` | PASS | 5/5 tests
Patch compare-before-write | `tests/test_patching.py` | PASS | 6/6 tests
Exact rollback | `tests/test_patching.py` | PASS | 6/6 tests

PHASE 5 — Command execution
No subprocess / shell in source | `grep` | PASS | no matches

PHASE 6 — LLM / prompt injection
Model cannot change framework/mode | `build_plan` normalizes + `validate_plan` | PASS | tested
Model cannot write outside repo | `validate_plan._check_path` | PASS | tested with `../outside.txt`
Model cannot choose provider/model | settings not writable by model | PASS | settings are host-only
Model cannot approve itself | no approval API in model path | PASS | no such mechanism
Invalid action type rejected | `PlannerOutput` extra="forbid" + enum | PASS | tested
Unknown field rejected | `PlannerOutput` extra="forbid" | PASS | tested

PHASE 8 — Cekura platform
Implicit mock-tool deletion blocked | `tests/test_platform.py::test_unintended_deletion_blocked` | PASS | 17/17 tests
Wrong agent_id fails | `tests/test_platform.py::test_wrong_agent_id_fails` | PASS | 17/17 tests
Bad key unauthorized | `tests/test_platform.py::test_bad_key_unauthorized` | PASS | 17/17 tests
Timeout-after-commit recovery | `tests/test_platform.py::test_timeout_after_commit_recovers_without_duplicate` | PASS | 17/17 tests
Exact GET-after verification | `tests/test_platform.py::test_corrupted_platform_state_fails_exact_verification` | PASS | 17/17 tests
CLI offline guard | `tests/test_platform.py::test_apply_platform_cli_guards` | PASS | 17/17 tests
KB upload path traversal | `tests/test_platform.py::test_kb_upload_rejects_path_traversal` | PASS | 17/17 tests
KB upload size cap | `tests/test_platform.py::test_kb_upload_rejects_oversized_file` | PASS | 17/17 tests

PHASE 9 — Mock tools
No live backend fall-through | `tests/test_features.py::test_local_router_returns_mock_data` | PASS | 28/28 tests
Synthetic mock data only | `build_mock_tool_specs` | PASS | 28/28 tests

PHASE 10 — Dynamic variables
Per-agent scoping | `client.py` agent_id in URL | PASS | static

PHASE 13 — Data protection
No secrets in wheel | custom scan of `dist/*.whl` | PASS | `[]`
Wheel file list | unzip listing | PASS | only source files, no .env/credentials

PHASE 15 — Packaging
Build from clean env | `python -m build --sdist --wheel` | PASS | artifacts generated

Incomplete / NOT_RUN
--------------------
- Full dependency SBOM / pip-audit (PHASE 3): NOT_RUN
- Full repository-containment adversarial fixture suite (PHASE 4.3–4.8): NOT_RUN
- Full concurrency / cancellation / fault injection (PHASE 14): NOT_RUN
- LiveKit / Pipecat lifecycle adversarial runtime tests (PHASE 12): NOT_RUN
- Complete prompt-injection corpus (PHASE 6.10): PARTIAL
- Complete KB prompt-injection / PII / provenance tests (PHASE 11): PARTIAL
