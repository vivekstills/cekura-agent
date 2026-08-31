"""Typed Cekura platform client.

Auth: `X-CEKURA-API-KEY` header (never Bearer/Api-Key), redacted everywhere.
Retry policy:
- reads (GET): bounded retry on 429/5xx/timeouts;
- mutations (POST/PATCH/upload): NO blind retry — a timeout/5xx after a mutation may
  have committed. We raise MaybeCommitted and the reconciler re-GETs and compares.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from ..errors import AgentError, BlockedByAccess

RETRYABLE = {429, 500, 502, 503, 504}


class MaybeCommitted(AgentError):
    """A mutating call failed in a way that may still have been applied server-side."""


class CekuraClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 30.0,
                 retry_delay_s: float = 0.5) -> None:
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.timeout = timeout
        self.retry_delay_s = retry_delay_s

    def __repr__(self) -> str:  # never leak the key
        return f"CekuraClient(base_url={self.base_url!r}, api_key=[REDACTED])"

    # ------------------------------------------------------------- transport

    def _headers(self) -> dict[str, str]:
        return {"X-CEKURA-API-KEY": self._api_key}

    def _get(self, path: str, params: dict | None = None) -> Any:
        url = f"{self.base_url}{path}"
        last = ""
        for attempt in range(3):
            if attempt:
                time.sleep(self.retry_delay_s * attempt)
            try:
                resp = httpx.get(url, params=params, headers=self._headers(), timeout=self.timeout)
            except httpx.TransportError as exc:
                last = f"transport: {exc}"
                continue
            if resp.status_code in (401, 403):
                raise BlockedByAccess("CEKURA_UNAUTHORIZED", "Cekura rejected the API key")
            if resp.status_code == 404:
                raise AgentError(f"not found: GET {path}")
            if resp.status_code in RETRYABLE:
                last = f"http {resp.status_code}"
                continue
            if resp.status_code != 200:
                raise AgentError(f"GET {path} -> http {resp.status_code}: {resp.text[:200]}")
            return resp.json()
        raise AgentError(f"GET {path} unavailable after retries ({last})")

    def _mutate(self, method: str, path: str, *, json_body: Any = None,
                files: list[tuple[str, tuple[str, bytes]]] | None = None) -> Any:
        url = f"{self.base_url}{path}"
        try:
            resp = httpx.request(method, url, json=json_body, files=files,
                                 headers=self._headers(), timeout=self.timeout)
        except httpx.TransportError as exc:
            raise MaybeCommitted(f"{method} {path} timed out/failed mid-flight: {exc}") from exc
        if resp.status_code in (401, 403):
            raise BlockedByAccess("CEKURA_UNAUTHORIZED", "Cekura rejected the API key")
        if resp.status_code == 404:
            raise AgentError(f"not found: {method} {path}")
        if resp.status_code in RETRYABLE:
            raise MaybeCommitted(f"{method} {path} -> http {resp.status_code} (may have committed)")
        if resp.status_code not in (200, 201):
            raise AgentError(f"{method} {path} -> http {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    # ------------------------------------------------------------- projects

    def list_projects(self) -> list[dict]:
        data = self._get("/user/v1/projects/")
        return data.get("results", data) if isinstance(data, dict) else data

    # ------------------------------------------------------------- agents

    def list_agents(self) -> list[dict]:
        data = self._get("/test_framework/v2/aiagents/")
        return data.get("results", data) if isinstance(data, dict) else data

    def get_agent(self, agent_id: int) -> dict:
        return self._get(f"/test_framework/v2/aiagents/{agent_id}/")

    def create_agent(self, payload: dict) -> dict:
        return self._mutate("POST", "/test_framework/v2/aiagents/", json_body=payload)

    def patch_agent(self, agent_id: int, payload: dict) -> dict:
        return self._mutate("PATCH", f"/test_framework/v2/aiagents/{agent_id}/", json_body=payload)

    # ------------------------------------------------------------- mock tools

    def get_mock_tools(self, agent_id: int) -> list[dict]:
        data = self._get(f"/test_framework/v2/aiagents/{agent_id}/", params={"ql": "{mock_tools}"})
        return data.get("mock_tools") or []

    def set_mock_tools(self, agent_id: int, tools: list[dict]) -> list[dict]:
        data = self.patch_agent(agent_id, {"mock_tools": tools})
        return data.get("mock_tools") or []

    # ------------------------------------------------------------- dynamic variables

    def get_dynamic_variables(self, agent_id: int) -> list[dict]:
        data = self._get(f"/test_framework/v1/aiagents/{agent_id}/dynamic-variables/")
        return data if isinstance(data, list) else data.get("results", [])

    def upsert_dynamic_variables(self, agent_id: int, variables: list[dict]) -> list[dict]:
        data = self._mutate("POST", f"/test_framework/v1/aiagents/{agent_id}/dynamic-variables/",
                            json_body=variables)
        return data if isinstance(data, list) else data.get("results", [])

    # ------------------------------------------------------------- knowledge base

    def upload_knowledge_base(self, agent_id: int, files: list[tuple[str, bytes]]) -> dict:
        payload = [("files", (name, content)) for name, content in files]
        return self._mutate("POST", f"/test_framework/v2/aiagents/{agent_id}/upload_knowledge_base/",
                            files=payload)
