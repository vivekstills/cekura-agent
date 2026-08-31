"""Faithful local fake of the Cekura platform API for offline verification.

Implements the exact routes/semantics the reconciler depends on:
- GET/POST  /test_framework/v2/aiagents/
- GET/PATCH /test_framework/v2/aiagents/{id}/          (incl. ?ql={mock_tools}, full-list mock_tools)
- GET/POST  /test_framework/v1/aiagents/{id}/dynamic-variables/   (array upsert by name)
- POST      /test_framework/v2/aiagents/{id}/upload_knowledge_base/  (multipart)
Plus fault injection: 401 on bad key, queued errors, "hiccup" (apply mutation then
return 504 — the timeout-after-commit case) and post-write corruption.
"""

from __future__ import annotations

import json
import re
import threading
from email.parser import BytesParser
from email.policy import default as email_default_policy
from http.server import BaseHTTPRequestHandler, HTTPServer

AGENT_RE = re.compile(r"^/test_framework/v2/aiagents/(\d+)/$")
DYNVAR_RE = re.compile(r"^/test_framework/v1/aiagents/(\d+)/dynamic-variables/$")
KB_RE = re.compile(r"^/test_framework/v2/aiagents/(\d+)/upload_knowledge_base/$")


class _Handler(BaseHTTPRequestHandler):
    server: FakeCekuraServer

    # --------------------------------------------------------- plumbing

    def _send(self, status: int, payload: object) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length) if length else b""

    def _authorized(self) -> bool:
        return self.headers.get("X-CEKURA-API-KEY") == self.server.api_key

    def _pre(self, mutating: bool) -> int | None:
        """Returns a status to fail with, or None to proceed."""
        self.server.requests.append({"method": self.command, "path": self.path,
                                     "headers": dict(self.headers)})
        if not self._authorized():
            return 401
        if self.server.error_queue:
            return self.server.error_queue.pop(0)
        del mutating
        return None

    def log_message(self, *args):
        pass

    # --------------------------------------------------------- GET

    def do_GET(self):  # noqa: N802
        fail = self._pre(mutating=False)
        if fail:
            return self._send(fail, {"error": f"injected {fail}"})
        path = self.path.split("?")[0]
        if path == "/user/v1/projects/":
            return self._send(200, {"results": self.server.projects})
        if path == "/test_framework/v2/aiagents/":
            return self._send(200, {"results": list(self.server.agents.values())})
        if m := AGENT_RE.match(path):
            agent = self.server.agents.get(int(m.group(1)))
            return self._send(200, agent) if agent else self._send(404, {"error": "no agent"})
        if m := DYNVAR_RE.match(path):
            agent = self.server.agents.get(int(m.group(1)))
            if not agent:
                return self._send(404, {"error": "no agent"})
            return self._send(200, agent.get("dynamic_variables", []))
        return self._send(404, {"error": f"unknown path {path}"})

    # --------------------------------------------------------- POST / PATCH

    def do_POST(self):  # noqa: N802
        fail = self._pre(mutating=True)
        if fail:
            return self._send(fail, {"error": f"injected {fail}"})
        path = self.path.split("?")[0]

        if path == "/test_framework/v2/aiagents/":
            payload = json.loads(self._body() or b"{}")
            agent_id = self.server.next_id
            self.server.next_id += 1
            agent = {
                "id": agent_id,
                "name": payload.get("name"),
                "description": payload.get("description", ""),
                "project": payload.get("project"),
                "provider": payload.get("provider", {}),
                "mock_tools": payload.get("mock_tools", []),
                "dynamic_variables": [],
                "knowledge_base_files": [],
            }
            self.server.agents[agent_id] = agent
            return self._finish_mutation(201, agent)

        if m := DYNVAR_RE.match(path):
            agent = self.server.agents.get(int(m.group(1)))
            if not agent:
                return self._send(404, {"error": "no agent"})
            incoming = json.loads(self._body() or b"[]")
            current = {v["name"]: v for v in agent.get("dynamic_variables", [])}
            for var in incoming:  # upsert semantics: create or update, never delete
                current[var["name"]] = {"name": var["name"], "description": var.get("description", "")}
            agent["dynamic_variables"] = list(current.values())
            return self._finish_mutation(201, agent["dynamic_variables"])

        if m := KB_RE.match(path):
            agent = self.server.agents.get(int(m.group(1)))
            if not agent:
                return self._send(404, {"error": "no agent"})
            content_type = self.headers.get("Content-Type", "")
            raw = (f"Content-Type: {content_type}\r\n\r\n").encode() + self._body()
            msg = BytesParser(policy=email_default_policy).parsebytes(raw)
            names = [part.get_filename() for part in msg.iter_parts() if part.get_filename()]
            existing = {f["name"] for f in agent["knowledge_base_files"]}
            for name in names:  # faithful to the real API: objects, not bare strings
                if name not in existing:
                    agent["knowledge_base_files"].append({"id": self.server.next_id, "name": name})
                    self.server.next_id += 1
            return self._finish_mutation(200, {"uploaded": names,
                                               "knowledge_base_files": agent["knowledge_base_files"]})

        return self._send(404, {"error": f"unknown path {path}"})

    def do_PATCH(self):  # noqa: N802
        fail = self._pre(mutating=True)
        if fail:
            return self._send(fail, {"error": f"injected {fail}"})
        path = self.path.split("?")[0]
        if m := AGENT_RE.match(path):
            agent = self.server.agents.get(int(m.group(1)))
            if not agent:
                return self._send(404, {"error": "no agent"})
            payload = json.loads(self._body() or b"{}")
            for key, value in payload.items():
                if key == "mock_tools":
                    agent["mock_tools"] = value  # full-list replace, exactly like the real API
                    if self.server.corrupt_next_write:
                        self.server.corrupt_next_write = False
                        if agent["mock_tools"] and agent["mock_tools"][0].get("mock_data"):
                            agent["mock_tools"][0]["mock_data"][0]["output"]["corrupted"] = "yes"
                elif key == "provider":
                    agent.setdefault("provider", {}).update(value)
                else:
                    agent[key] = value
            return self._finish_mutation(200, agent)
        return self._send(404, {"error": f"unknown path {path}"})

    def _finish_mutation(self, status: int, payload: object):
        """Timeout-after-commit injection: the mutation already happened above."""
        if self.server.hiccup_next_mutation:
            self.server.hiccup_next_mutation = False
            return self._send(504, {"error": "gateway timeout (mutation committed anyway)"})
        return self._send(status, payload)


class FakeCekuraServer(HTTPServer):
    def __init__(self, api_key: str = "test-cekura-key"):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.api_key = api_key
        self.agents: dict[int, dict] = {}
        self.projects: list[dict] = [{"id": 1, "name": "Test Project"}]
        self.next_id = 101
        self.requests: list[dict] = []
        self.error_queue: list[int] = []
        self.hiccup_next_mutation = False
        self.corrupt_next_write = False
        self._thread: threading.Thread | None = None

    # convenience for tests / offline demo
    def seed_agent(self, **overrides) -> dict:
        agent = {
            "id": self.next_id, "name": "seeded", "description": "seeded agent",
            "project": 1, "provider": {"type": "livekit", "credentials": {"config": {}}},
            "mock_tools": [], "dynamic_variables": [], "knowledge_base_files": [],
        }
        agent.update(overrides)
        self.agents[agent["id"]] = agent
        self.next_id += 1
        return agent

    @property
    def url(self) -> str:
        host, port = self.server_address
        return f"http://{host}:{port}"

    def start(self) -> FakeCekuraServer:
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self.shutdown()
        if self._thread:
            self._thread.join(timeout=2)

    def mutation_count(self) -> int:
        return sum(1 for r in self.requests if r["method"] in {"POST", "PATCH"})
