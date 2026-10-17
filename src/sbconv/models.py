"""Intermediate structures that a node carries from load to clean to render."""

from __future__ import annotations

from dataclasses import dataclass

# Protocols a URI carrier can convert. The value is the name sing-box expects.
URI_SCHEMES: dict[str, str] = {
    "ss": "ss",
    "vmess": "vmess",
    "vless": "vless",
    "trojan": "trojan",
    "hysteria": "hysteria",
    "hysteria2": "hysteria2",
    "hy2": "hysteria2",
    "tuic": "tuic",
    "anytls": "anytls",
    "socks": "socks5",
    "socks5": "socks5",
    "http": "http",
    "https": "http",
    "wg": "wireguard",
    "wireguard": "wireguard",
}


@dataclass(slots=True)
class Node:
    """One node. The render stage parses the raw form by protocol."""

    name: str
    kind: str
    origin: str
    clash: dict | None = None
    uri: str | None = None
    dropped: str | None = None
    region: str | None = None
    plugin: str | None = None


@dataclass(slots=True)
class LoadResult:
    nodes: list[Node]
    carrier: str
    source: str
    skipped: list[tuple[str, str]]
