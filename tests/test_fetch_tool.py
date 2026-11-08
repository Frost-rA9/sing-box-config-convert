"""The fetch tool writes a Clash file, sends the User-Agent, and rejects a non-Clash body."""

from __future__ import annotations

import http.server
import importlib.util
import threading
from pathlib import Path

import pytest

TOOL = Path(__file__).parents[1] / "tools" / "fetch_config.py"
CLASH = (
    'proxies:\n  - {name: "🇭🇰 香港 1", type: ss, server: 192.0.2.11, port: 10016, cipher: aes-128-gcm, password: pw}\n'
)


def _load_tool():
    spec = importlib.util.spec_from_file_location("fetch_config_tool", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Handler(http.server.BaseHTTPRequestHandler):
    body = CLASH
    user_agent = ""

    def do_GET(self):  # noqa: N802 - the name comes from the base class
        type(self).user_agent = self.headers.get("User-Agent", "")
        data = type(self).body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture()
def server(monkeypatch):
    """Serve one body on 127.0.0.1. NO_PROXY keeps a system proxy out of the way."""
    monkeypatch.setenv("NO_PROXY", "*")
    handler = type("Handler", (_Handler,), {"body": CLASH, "user_agent": ""})
    httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}/link/abc", handler
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_writes_the_file_and_prints_the_count(server, tmp_path: Path):
    url, handler = server
    tool = _load_tool()
    out = tmp_path / "sub.yaml"
    assert tool.main([url, "-o", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == CLASH
    assert handler.user_agent == tool.DEFAULT_USER_AGENT


def test_user_agent_is_configurable(server, tmp_path: Path):
    url, handler = server
    tool = _load_tool()
    assert tool.main([url, "-o", str(tmp_path / "sub.yaml"), "--user-agent", "clash-verge/v2.0.0"]) == 0
    assert handler.user_agent == "clash-verge/v2.0.0"


def test_rejects_a_non_clash_body(server, tmp_path: Path):
    url, handler = server
    handler.body = "ss://YWVzLTEyOC1nY206cHc=@192.0.2.10:8388#香港 1\n"
    tool = _load_tool()
    out = tmp_path / "sub.yaml"
    assert tool.main([url, "-o", str(out)]) == 2
    assert not out.exists()


def test_keeps_the_existing_file_without_force(server, tmp_path: Path):
    url, _ = server
    tool = _load_tool()
    out = tmp_path / "sub.yaml"
    out.write_text("proxies: []\n", encoding="utf-8")
    assert tool.main([url, "-o", str(out)]) == 2
    assert out.read_text(encoding="utf-8") == "proxies: []\n"
    assert tool.main([url, "-o", str(out), "--force"]) == 0
    assert out.read_text(encoding="utf-8") == CLASH
