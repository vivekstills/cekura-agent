"""OpenRouter planner client for exact model moonshotai/kimi-k3.

Strict-JSON planning only; the model gets no tools, no shell and no file access.
All tests run against a local fake server; the live path needs OPENROUTER_API_KEY.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from ..budget import BudgetLedger, UsageEvent, estimate_cost
from ..config import Settings
from ..errors import AgentError, BlockedByAccess, PlanRejected
from ..models import Framework, Mode, PlanAction
from .prompts import SYSTEM_PROMPT

RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class PlannerOutput(BaseModel):
    """The only thing the model is allowed to produce."""

    model_config = ConfigDict(extra="forbid")

    framework: Framework
    mode: Mode
    actions: list[PlanAction]
    notes: str = ""


class OpenRouterPlanner:
    name = "openrouter"

    def __init__(self, settings: Settings, ledger: BudgetLedger) -> None:
        self.settings = settings
        self.ledger = ledger
        self.retry_base_delay_s = 0.5

    def plan(self, bundle: dict[str, Any], inspection: object, mode: Mode,
             agent_id: int | None) -> tuple[list[PlanAction], str, dict[str, Any]]:
        del inspection, agent_id  # the bundle already encodes everything the model may see
        api_key = self.settings.openrouter_api_key
        if not api_key:
            raise BlockedByAccess(
                "OPENROUTER_KEY_MISSING",
                "OPENROUTER_API_KEY is not set; use --model-mode fake or provide the key via .env",
            )

        user_content = json.dumps(bundle, indent=1)
        est_prompt_tokens = (len(SYSTEM_PROMPT) + len(user_content)) // 3
        self.ledger.precheck(
            estimate_cost(self.settings.model, est_prompt_tokens, self.settings.max_plan_tokens)
        )

        body = {
            "model": self.settings.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.1,
            "max_tokens": self.settings.max_plan_tokens,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "X-Title": "cekura-agent",
        }

        data = self._post_with_retries(body, headers)
        output = self._parse(data)
        usage = data.get("usage") or {}
        cost = estimate_cost(
            self.settings.model, int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))
        )
        self.ledger.record(UsageEvent(
            model=str(data.get("model") or self.settings.model),
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
            cost_usd=cost,
            provider=str(data.get("provider") or ""),
            request_id=str(data.get("id") or ""),
        ))
        meta = {
            "planner": "openrouter",
            "model": data.get("model") or self.settings.model,
            "provider": data.get("provider"),
            "request_id": data.get("id"),
            "usage": usage,
            "cost_usd": round(cost, 6),
        }
        return output.actions, output.notes, meta

    # ------------------------------------------------------------------ internals

    def _post_with_retries(self, body: dict, headers: dict) -> dict:
        url = f"{self.settings.openrouter_base_url}/chat/completions"
        last_error = ""
        for attempt in range(3):
            if attempt:
                time.sleep(self.retry_base_delay_s * (2 ** (attempt - 1)))
            try:
                resp = httpx.post(url, json=body, headers=headers, timeout=self.settings.request_timeout_s)
            except httpx.TimeoutException:
                last_error = "timeout"
                continue
            except httpx.TransportError as exc:
                last_error = f"transport error: {exc}"
                continue
            if resp.status_code in (401, 403):
                raise BlockedByAccess("OPENROUTER_UNAUTHORIZED", "OpenRouter rejected the API key")
            if resp.status_code in RETRYABLE_STATUS:
                last_error = f"http {resp.status_code}"
                continue
            if resp.status_code != 200:
                raise AgentError(f"openrouter returned http {resp.status_code}")
            try:
                return resp.json()
            except json.JSONDecodeError as exc:
                raise AgentError(f"openrouter returned non-JSON body: {exc}") from exc
        raise AgentError(f"openrouter unavailable after retries ({last_error})")

    @staticmethod
    def _parse(data: dict) -> PlannerOutput:
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise PlanRejected(f"unexpected completion shape: {exc}") from exc
        cleaned = _FENCE_RE.sub("", content or "").strip()
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise PlanRejected(f"model did not return valid JSON: {exc}") from exc
        try:
            return PlannerOutput.model_validate(parsed)
        except ValidationError as exc:
            raise PlanRejected(f"model output violates the plan schema: {exc}") from exc
