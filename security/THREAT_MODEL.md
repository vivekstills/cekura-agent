Threat Model: cekura-agent
==========================

Assets
------
1. Customer source code in the inspected repository.
2. Git state and local disk under the repo root.
3. OPENROUTER_API_KEY and CEKURA_API_KEY (and customer LiveKit creds).
4. Cekura Agents, mock tools, dynamic variables, KB files on the platform.
5. Model request/response payloads and audit reports.
6. Audio/transcript metadata and tool call records.
7. Budget ledger and cumulative spend.

Trust Boundaries
----------------
```
User CLI host         (trusted, deterministic)
   |
   v
cekura-agent          (this code)
   |
   +-- subprocess: none
   +-- local filesystem: repo root + .cekura-agent subdir
   |
   v
Model planner         (OpenRouter / fake, untrusted by default)
   |
   v
Cekura API            (HTTPS, X-CEKURA-API-KEY)
   |
   v
LiveKit / Pipecat     (customer runtime, not touched by this tool)
```

Threat Actors
-------------
1. **Malicious repository**: files, symlinks, names, comments, docstrings,
   tool descriptions, dynamic-variable values, KB documents, and manifest
   paths are treated as attacker-controlled.
2. **Compromised / adversarial LLM**: model output may try to expand policy,
   change provider, approve itself, write outside the repo, or exfiltrate.
3. **Cekura platform / API failures**: wrong agent ID, 401/403, 5xx/timeout,
   partial commits, concurrent human edits.
4. **Local user / operator error**: running `apply` without review, wrong
   `--agent-id`, stale desired-state JSON, production mode.

Top Threats
-----------
1. Filesystem escape via path traversal or symlinks.
2. Source code / secret leakage into the model bundle.
3. Excessive model agency (model output drives writes, shell, or API calls).
4. Wrong-target or unapproved Cekura mutations, especially implicit deletions.
5. Mock tool fall-through to real backends.
6. Dynamic variable or KB cross-call / cross-tenant leakage.
7. Rollback failure or stale-hash write.
8. Secret leakage in logs, reports, build artifacts.
