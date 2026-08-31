"""Runtime configuration: model, endpoints, budgets, env loading.

Secrets are read from the process environment (optionally hydrated from an
untracked .env via python-dotenv). They are never persisted, logged or echoed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_MODEL = "moonshotai/kimi-k3"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
CEKURA_BASE_URL = "https://api.cekura.ai"
CEKURA_DASHBOARD_URL = "https://dashboard.cekura.ai"

# moonshotai/kimi-k3 pricing per token (verified via OpenRouter /models 2026-08-31).
KIMI_K3_PROMPT_COST = 0.000003
KIMI_K3_COMPLETION_COST = 0.000015


@dataclass
class Settings:
    model: str = DEFAULT_MODEL
    model_mode: str = "fake"  # fake | openrouter
    platform_mode: str = "offline"  # offline | staging
    openrouter_base_url: str = OPENROUTER_BASE_URL
    cekura_base_url: str = CEKURA_BASE_URL
    per_run_cost_cap_usd: float = 5.0
    cumulative_cost_cap_usd: float = 180.0
    max_plan_tokens: int = 8000
    request_timeout_s: float = 120.0
    state_dir: Path = field(default_factory=lambda: Path.home() / ".cekura-agent")

    @property
    def openrouter_api_key(self) -> str | None:
        return os.environ.get("OPENROUTER_API_KEY") or None

    @property
    def cekura_api_key(self) -> str | None:
        return os.environ.get("CEKURA_API_KEY") or None


def load_settings(env_file: Path | None = None, **overrides: object) -> Settings:
    """Load .env (if present) then construct settings with CLI overrides."""
    load_dotenv(dotenv_path=env_file, override=False)
    settings = Settings()
    for key, value in overrides.items():
        if value is not None and hasattr(settings, key):
            setattr(settings, key, value)
    if base := os.environ.get("CEKURA_BASE_URL"):
        settings.cekura_base_url = base
    if base := os.environ.get("OPENROUTER_BASE_URL"):
        settings.openrouter_base_url = base
    return settings
