"""Report assembly: one VerificationReport + human summary per run, honest statuses only."""

from __future__ import annotations

from .models import CheckResult, Status, VerificationReport

STATUS_ICON = {
    Status.IMPLEMENTED_AND_OFFLINE_VERIFIED: "[offline-verified]",
    Status.LIVE_VERIFIED: "[LIVE-verified]",
    Status.BLOCKED_BY_ACCESS_OR_DEPENDENCY: "[BLOCKED]",
    Status.NEEDS_HUMAN_UNSUPPORTED_TOPOLOGY: "[NEEDS-HUMAN]",
    Status.NOT_RUN: "[not-run]",
}


def human_summary(report: VerificationReport, extra_lines: list[str] | None = None) -> str:
    lines = [
        "",
        f"== cekura-agent run {report.run_id} ==",
        f"repo: {report.repo_root}",
        f"framework: {report.framework.value}   mode: {report.mode.value if report.mode else '-'}",
        "",
        "capability statuses:",
    ]
    for name, status in report.statuses.items():
        lines.append(f"  {STATUS_ICON[status]:<20} {name}")
    failed = [c for c in report.checks if not c.passed and c.severity == "error"]
    warns = [c for c in report.checks if not c.passed and c.severity == "warning"]
    lines.append("")
    lines.append(f"checks: {len(report.checks) - len(failed) - len(warns)} passed, "
                 f"{len(failed)} failed, {len(warns)} warnings")
    for check in failed:
        lines.append(f"  FAIL {check.name}: {check.detail}")
    for code in report.needs_human:
        lines.append(f"  NEEDS_HUMAN: {code}")
    for code in report.blockers:
        lines.append(f"  BLOCKED: {code}")
    if report.model_usage:
        cost = report.model_usage.get("cost_usd", 0)
        lines.append(f"model: {report.model_usage.get('model') or 'fake planner'}"
                     f" (cost ${cost:.4f})" if cost else
                     f"model: {report.model_usage.get('model') or 'fake planner (no cost)'}")
    lines.extend(extra_lines or [])
    lines.append(f"exit code: {report.exit_code}")
    return "\n".join(lines)


def add_check(report: VerificationReport, name: str, passed: bool, detail: str = "",
              severity: str = "error") -> None:
    report.checks.append(CheckResult(name=name, passed=passed, detail=detail, severity=severity))
