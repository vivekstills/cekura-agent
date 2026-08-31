"""Error taxonomy mapped to CLI exit codes.

Exit codes:
    0 -- success
    1 -- failure (bug, invalid input, verification failure, blocked dependency misuse)
    2 -- NEEDS_HUMAN (unsupported topology / ambiguity / approval required)
"""

from __future__ import annotations

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_NEEDS_HUMAN = 2


class AgentError(Exception):
    """Failure the agent cannot recover from. Exit code 1."""

    exit_code = EXIT_FAILURE

    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail


class NeedsHuman(AgentError):
    """Stable, machine-readable refusal. Exit code 2."""

    exit_code = EXIT_NEEDS_HUMAN

    def __init__(self, reason_code: str, message: str, *, detail: str | None = None) -> None:
        super().__init__(message, detail=detail)
        self.reason_code = reason_code


class SafetyViolation(AgentError):
    """Path traversal, secret leak or repo-boundary violation. Exit code 1."""


class BudgetExceeded(AgentError):
    """Model budget cap would be exceeded. Exit code 1."""


class PlanRejected(AgentError):
    """Semantic plan validation failed. Exit code 1."""


class PlatformContractError(AgentError):
    """Cekura platform state did not match expectations after apply. Exit code 1."""


class BlockedByAccess(AgentError):
    """Live path requires a credential/dependency that is absent. Exit code 1 with stable code."""

    def __init__(self, blocker_code: str, message: str) -> None:
        super().__init__(message)
        self.blocker_code = blocker_code
