Attack Surface: cekura-agent
============================

CLI entrypoints (src/cekura_agent/cli.py)
-----------------------------------------
- `inspect <repo> --json`
- `plan <repo> --mode <test|observe> --model-mode <fake|openrouter> --agent-id`
- `diff <repo> ...`
- `integrate <repo> ...` (dry-run by default, `--apply` to write)
- `verify <repo>`
- `prepare-platform <repo> --mode ... --agent-id --project-id`
- `apply-platform --desired-state <file> --platform-mode <offline|staging>`
- `rollback <repo> --run-id --force`

Each entrypoint accepts a `Path` to the repo or a JSON file. The repo must
exist and be a directory; desired-state files must be files.

Data inputs (all untrusted)
---------------------------
- Source files in the repo (Python, .toml, .txt, .md).
- File names and directory layout.
- Symlinks and hard links.
- README, comments, docstrings, string literals.
- Function tool definitions and dynamic variable placeholders.
- KB documents.
- Desired-state JSON, including `kb_uploads[].path` and `repo_root`.

Network clients
---------------
- `httpx` -> OpenRouter `chat/completions` (planner client).
- `httpx` -> Cekura API for agents, mock tools, dynamic variables, KB upload.
- `httpx` -> PyPI version probe (only in staging mode with `allow_network=True`).

Subprocess / command execution
------------------------------
- No `os.system`, `subprocess`, `Popen`, or `shell=True` found in `src/`.
- No `eval`, `exec`, `compile` of model output. `compile()` is used only to
  check Python syntax in `verification/lifecycle.py`.
- `ast.literal_eval` is used only for static AST constant values in
  `scanner/evidence.py`.

File outputs
------------
- Patched customer source files under repo root (via `patching.py`).
- Backups under `<repo>/.cekura-agent/backups/<run_id>/`.
- Rollback manifests under `<repo>/.cekura-agent/runs/`.
- Budget ledger under `~/.cekura-agent/ledger.json`.
- CLI JSON reports (when `--json`, `--out`, `--report` are used).
- Build artifacts (`dist/`) excluded by `.gitignore`.

External hosts
--------------
- `openrouter.ai` (https)
- `api.cekura.ai` (https)
- `dashboard.cekura.ai` (reported URL only)
- `pypi.org` (optional, staging mode)
