"""Model budget ledger: per-run and cumulative caps, persisted between runs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .config import KIMI_K3_COMPLETION_COST, KIMI_K3_PROMPT_COST, Settings
from .errors import BudgetExceeded


@dataclass
class UsageEvent:
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    provider: str = ""
    request_id: str = ""


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    # Only kimi-k3 pricing is pinned; unknown models use kimi-k3 rates as a conservative bound.
    del model
    return prompt_tokens * KIMI_K3_PROMPT_COST + completion_tokens * KIMI_K3_COMPLETION_COST


class BudgetLedger:
    """Persists cumulative spend to `<state_dir>/ledger.json` and enforces caps."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.path = settings.state_dir / "ledger.json"
        self.run_spend_usd = 0.0
        self._cumulative = self._load()

    def _load(self) -> float:
        try:
            return float(json.loads(self.path.read_text())["cumulative_usd"])
        except (OSError, ValueError, KeyError):
            return 0.0

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"cumulative_usd": round(self._cumulative, 6)}))

    @property
    def cumulative_usd(self) -> float:
        return self._cumulative

    def precheck(self, projected_cost_usd: float) -> None:
        if self.run_spend_usd + projected_cost_usd > self.settings.per_run_cost_cap_usd:
            raise BudgetExceeded(
                f"per-run cap {self.settings.per_run_cost_cap_usd:.2f} USD would be exceeded"
            )
        if self._cumulative + projected_cost_usd > self.settings.cumulative_cost_cap_usd:
            raise BudgetExceeded(
                f"cumulative cap {self.settings.cumulative_cost_cap_usd:.2f} USD would be exceeded"
            )

    def record(self, event: UsageEvent) -> None:
        self.run_spend_usd += event.cost_usd
        self._cumulative += event.cost_usd
        self._save()

    def summary(self) -> dict[str, float]:
        return {
            "run_spend_usd": round(self.run_spend_usd, 6),
            "cumulative_usd": round(self._cumulative, 6),
            "per_run_cap_usd": self.settings.per_run_cost_cap_usd,
            "cumulative_cap_usd": self.settings.cumulative_cost_cap_usd,
        }
