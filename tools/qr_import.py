"""Serve one sing-box config over the LAN and print a one-time import QR."""

from __future__ import annotations

import argparse
import http.server
import ipaddress
import json
import os
import pathlib
import re
import secrets
import socket
import subprocess
import threading
import urllib.parse

import segno

DEFAULT_CONFIG = pathlib.Path("local/config-android.json")
DEFAULT_TTL = 1800
SERVED_PATH = "/config.json"


def detect_lan_ip() -> str | None:
    """Return a likely LAN IPv4, skipping TUN and virtual adapters on Windows."""
    if os.name == "nt":
        script = (
            "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
            "Get-NetIPAddress -AddressFamily IPv4 | "
            "Where-Object { $_.IPAddress -match '^(10\\.|192\\.168\\.|172\\.(1[6-9]|2[0-9]|3[01])\\.)' } | "
            "Select-Object InterfaceAlias,IPAddress | ConvertTo-Json -Compress"
        )
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-Command", script],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
            data = json.loads(proc.stdout) if proc.stdout.strip() else []
            if isinstance(data, dict):
                data = [data]
            skip = (
                "tun",
                "sing",
                "vethernet",
                "hyper-v",
                "vmware",
                "virtualbox",
                "loopback",
                "tailscale",
                "zerotier",
                "wsl",
                "docker",
            )
            for item in data:
                alias = str(item.get("InterfaceAlias", "")).lower()
                ip = str(item.get("IPAddress", ""))
                if any(word in alias for word in skip):
                    continue
                return ip
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
            pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
        if ip and not ip.startswith(("127.", "169.254.")):
            return ip
    except OSError:
        pass
    return None


def safe_name(name: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-")
    return value or "profile"


def build_links(host: str, port: int, name: str, token: str) -> tuple[str, str]:
    remote = f"http://{host}:{port}{SERVED_PATH}?token={urllib.parse.quote(token)}"
    query = urllib.parse.urlencode({"url": remote})
    link = f"sing-box://import-remote-profile?{query}#{urllib.parse.quote(name, safe='')}"
    return remote, link


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def __init__(self, *args: object, config: bytes, token: str, **kwargs: object) -> None:
        self.config = config
        self.token = token
        super().__init__(*args, **kwargs)

    def log_message(self, fmt: str, *args: object) -> None:
        path = self.path.split("?", 1)[0]
        print(f"{self.address_string()} {self.command} {path}", flush=True)

    def _kind(self) -> str:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/health":
            return "health"
        if parsed.path != SERVED_PATH:
            return "missing"
        query = urllib.parse.parse_qs(parsed.query)
        return "config" if query.get("token") == [self.token] else "forbidden"

    def _send(self, code: int, body: bytes = b"", ctype: str = "text/plain; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        if body:
            try:
                self.wfile.write(body)
            except BrokenPipeError:
                pass

    def _send_config_headers(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(self.config)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()

    def do_HEAD(self) -> None:
        kind = self._kind()
        if kind == "health":
            self._send(200)
        elif kind == "config":
            self._send_config_headers()
        elif kind == "forbidden":
            self._send(403, b"forbidden")
        else:
            self._send(404, b"not found")

    def do_GET(self) -> None:
        kind = self._kind()
        if kind == "health":
            self._send(200, b"ok")
        elif kind == "config":
            self._send(200, self.config, "application/json; charset=utf-8")
            print("served config", flush=True)
        elif kind == "forbidden":
            self._send(403, b"forbidden")
        else:
            self._send(404, b"not found")


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Serve one sing-box config over the LAN and print a one-time import QR for sing-box for Android."
    )
    parser.add_argument("config", nargs="?", default=str(DEFAULT_CONFIG), type=pathlib.Path)
    parser.add_argument("--host", help="LAN IPv4 address; detected by default")
    parser.add_argument("--port", type=int, default=0, help="TCP port; 0 picks a free port")
    parser.add_argument("--ttl", type=int, default=DEFAULT_TTL, help="seconds before the server stops; 0 disables")
    parser.add_argument("--name", help="profile name in the import link; defaults to the config stem")
    parser.add_argument("--qr-out", type=pathlib.Path, help="QR PNG path; defaults to local/<name>-qr.png")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.config.is_file():
        print(f"error: config not found: {args.config}", flush=True)
        return 2
    try:
        config_bytes = args.config.read_bytes()
    except OSError as exc:
        print(f"error: cannot read config: {exc}", flush=True)
        return 2

    name = args.name or args.config.stem
    host = args.host or detect_lan_ip()
    if not host:
        print("error: no LAN IPv4 address found; pass --host", flush=True)
        return 2
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        print(f"error: invalid host: {host}", flush=True)
        return 2
    if address.is_unspecified:
        print("error: use a concrete LAN address, not 0.0.0.0", flush=True)
        return 2
    if address.version != 4:
        print("error: only IPv4 addresses are supported", flush=True)
        return 2
    if args.ttl < 0:
        print("error: --ttl must be 0 or more", flush=True)
        return 2

    token = secrets.token_hex(16)
    try:
        server = Server(
            (host, args.port),
            lambda *a, **kw: Handler(*a, config=config_bytes, token=token, **kw),
        )
    except OSError as exc:
        print(f"error: cannot bind {host}:{args.port}: {exc}", flush=True)
        return 2
    port = server.server_address[1]
    remote, link = build_links(host, port, name, token)

    qr_path = args.qr_out or pathlib.Path("local") / f"{safe_name(name)}-qr.png"
    qr_path.parent.mkdir(parents=True, exist_ok=True)
    segno.make(link, error="m").save(qr_path, scale=8, border=4)

    print(f"config   {args.config}", flush=True)
    print(f"listen   {host}:{port}", flush=True)
    print(f"remote   {remote}", flush=True)
    print(f"import   {link}", flush=True)
    print(f"qr       {qr_path}", flush=True)
    print(f"ttl      {args.ttl}s" if args.ttl > 0 else "ttl      disabled", flush=True)
    if address.is_loopback:
        print("warning  loopback address; use adb reverse or pass a LAN address", flush=True)
    print("scan the QR, then press Ctrl+C to stop", flush=True)

    timer: threading.Timer | None = None
    if args.ttl > 0:
        timer = threading.Timer(args.ttl, server.shutdown)
        timer.daemon = True
        timer.start()
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        if timer is not None:
            timer.cancel()
        server.server_close()
    print("stopped", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
