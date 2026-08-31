Cekura Agent — Security Audit Baseline
=======================================

Audit date: 2026-08-31

Auditor: Devin automated adversarial pass
Scope: /Users/vivek/Desktop/cekura agent
Standards: OWASP ASVS 5.0, OWASP LLM Top 10 2025, NIST SP 800-218 SSDF

Repository freeze
-----------------
- Final release candidate SHA: `12f5e86`
- Branch: main
- Dirty state: clean
- Python: 3.12.13
- OS/arch: Darwin 24.6.0 / ARM64
- Package version: 0.1.0
- Test result: `102 passed in 11.76s`
- Ruff: `All checks passed!`

No production credentials were used during this audit.
External network was denied except for the PyPI version probe in
`_sdk_availability_blockers`, which is gated by `allow_network` and only
called in staging mode.
