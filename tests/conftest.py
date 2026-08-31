"""Shared test infrastructure: fixture repos + local fake OpenRouter server."""

from __future__ import annotations

import json
import shutil
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from cekura_agent.config import Settings

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def copy_fixture(tmp_path):
    """Copy a fixture repo into tmp so tests may mutate it."""

    def _copy(name: str) -> Path:
        dest = tmp_path / name
        shutil.copytree(FIXTURES / name, dest)
        return dest

    return _copy


class _RecordingHandler(BaseHTTPRequestHandler):
    server: FakeOpenRouter

    def do_POST(self):  # noqa: N802 (http.server API)
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        self.server.requests.append({
            "path": self.path,
            "headers": dict(self.headers),
            "body": body,
        })
        status, payload = self.server.next_response()
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):  # silence
        pass


class FakeOpenRouter(HTTPServer):
    """Local stand-in for openrouter.ai. Queue (status, payload) tuples per request."""

    def __init__(self):
        super().__init__(("127.0.0.1", 0), _RecordingHandler)
        self.requests: list[dict] = []
        self.responses: list[tuple[int, dict]] = []

    def queue(self, status: int, payload: dict) -> None:
        self.responses.append((status, payload))

    def queue_completion(self, content: object, *, model: str = "moonshotai/kimi-k3",
                         prompt_tokens: int = 1200, completion_tokens: int = 300) -> None:
        text = content if isinstance(content, str) else json.dumps(content)
        self.queue(200, {
            "id": "gen-fake-1",
            "model": model,
            "provider": "fake-provider",
            "choices": [{"message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
        })

    def next_response(self) -> tuple[int, dict]:
        if self.responses:
            return self.responses.pop(0)
        return 500, {"error": "no response queued"}

    @property
    def url(self) -> str:
        host, port = self.server_address
        return f"http://{host}:{port}"


@pytest.fixture
def fake_openrouter():
    server = FakeOpenRouter()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    thread.join(timeout=2)


@pytest.fixture
def settings_factory(tmp_path, monkeypatch):
    """Settings pointing at local fakes with a tiny retry delay and isolated ledger."""

    def _make(**overrides) -> Settings:
        settings = Settings(state_dir=tmp_path / "state")
        for key, value in overrides.items():
            setattr(settings, key, value)
        return settings

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("CEKURA_API_KEY", raising=False)
    return _make
