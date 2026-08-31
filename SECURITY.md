# Security

## Secrets
- All credentials come from the environment (hydrated from an untracked, `chmod 600` `.env`);
  never hardcoded, never committed, never echoed. `.gitignore` covers `.env`, `eval/repos/`,
  `reports/`, `.cekura-agent/`.
- Every CLI output and report passes through `safety.redact()` (OpenRouter keys, generic API
  keys, bearer tokens, `X-CEKURA-API-KEY` values, private key blocks).
- `safety.scan_paths_for_secrets` runs over every patched file after apply
  (`post:no_secrets_in_diff` check) and over the tree before packaging.
- The generated code reads `CEKURA_API_KEY` via `os.getenv` — a lifecycle verifier fails any
  literal `api_key="..."`.
- **Rotation note:** the OpenRouter key and the Cekura API key used for this trial were shared
  in chat; rotate both after the trial.

## Boundaries
- Repo boundary: every write path is resolved and must stay under the target repo root
  (`SafetyViolation` otherwise). Plans referencing `..`/absolute paths or test files are
  rejected before execution.
- The model (Kimi K3) has no shell, no file tools, no API tools — it returns JSON that a
  validator filters against evidence. Hostile output is inert.
- Dry-run is the default for `integrate` and `apply-platform`; `--apply` is explicit.
- Offline modes make no external connections (socket-guard test enforces loopback-only).
- Platform mutations are never blindly retried (duplicate-commit protection); deletions
  require `--approve-deletions`.
- Rollback: per-run backups + manifest inside the target repo; `rollback` restores exact
  hashes and deletes files the patch created; refuses (without `--force`) if the repo changed
  after the patch.

## Budget
- Per-run and cumulative USD caps enforced before every model call; usage ledger persisted at
  `~/.cekura-agent/ledger.json`. Kimi K3 pricing pinned from OpenRouter (2026-08-31).
