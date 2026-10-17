"""Loader: a subscription URL or a Clash YAML file becomes a node list.

Three carriers become one list: Clash YAML, a Base64 link list, and a plain link
list.
"""

from __future__ import annotations

import base64
import binascii
from pathlib import Path

import requests
import yaml

from .models import URI_SCHEMES, LoadResult, Node

DEFAULT_UA = "clash.meta"
TIMEOUT = 30
MAX_BYTES = 10 * 1000 * 1000


class LoadError(Exception):
    """The carrier holds no readable nodes."""


def _read(source: str, user_agent: str) -> tuple[str, str]:
    if source.startswith(("http://", "https://")):
        resp = requests.get(source, headers={"User-Agent": user_agent}, timeout=TIMEOUT)
        resp.raise_for_status()
        if len(resp.content) > MAX_BYTES:
            raise LoadError(f"订阅超过 {MAX_BYTES} 字节上限")
        return resp.content.decode("utf-8", "replace"), "url"
    path = Path(source)
    if not path.is_file():
        raise LoadError(f"文件不存在：{source}")
    return path.read_text(encoding="utf-8", errors="replace"), "file"


def _try_base64(text: str) -> str | None:
    raw = text.strip()
    if not raw or "://" in raw:
        return None
    padded = raw + "=" * (-len(raw) % 4)
    try:
        decoded = base64.b64decode(padded, validate=True)
    except (binascii.Error, ValueError):
        return None
    try:
        out = decoded.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return out if "://" in out else None


def _parse_clash(text: str) -> list[Node] | None:
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    if not isinstance(doc, dict) or "proxies" not in doc:
        return None
    proxies = doc.get("proxies") or []
    if not isinstance(proxies, list):
        raise LoadError("proxies 段不是列表")
    nodes: list[Node] = []
    for item in proxies:
        if not isinstance(item, dict):
            raise LoadError("proxies 里有非字典项")
        name = str(item.get("name") or "").strip()
        kind = str(item.get("type") or "").strip().lower()
        if not name:
            raise LoadError("proxies 里有节点缺少 name")
        if not kind:
            raise LoadError(f"节点 {name!r} 缺少 type")
        payload = {k: v for k, v in item.items() if k not in ("name", "type")}
        nodes.append(Node(name=name, kind=kind, origin="clash", clash=payload))
    return nodes


def _parse_uris(text: str) -> tuple[list[Node], list[tuple[str, str]]]:
    nodes: list[Node] = []
    skipped: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        scheme = line.split("://", 1)[0].lower() if "://" in line else ""
        if scheme not in URI_SCHEMES:
            skipped.append((line[:60], f"不支持的协议 {scheme or '(无)'}"))
            continue
        name = line.split("#", 1)[1] if "#" in line else ""
        name = requests.utils.unquote(name).strip() or f"{scheme}-{len(nodes) + 1}"
        key = (name, scheme)
        if key in seen:
            skipped.append((name, "同名同协议重复"))
            continue
        seen.add(key)
        nodes.append(Node(name=name, kind=URI_SCHEMES[scheme], origin="uri", uri=line))
    return nodes, skipped


def load(source: str, user_agent: str = DEFAULT_UA) -> LoadResult:
    """Read one carrier and return the node list."""
    text, carrier = _read(source, user_agent)

    nodes = _parse_clash(text)
    if nodes is not None:
        if not nodes:
            raise LoadError("Clash 配置里没有 proxies")
        return LoadResult(nodes=nodes, carrier=f"{carrier}:clash", source=source, skipped=[])

    decoded = _try_base64(text)
    if decoded is not None:
        text = decoded
        carrier = f"{carrier}:base64"

    nodes, skipped = _parse_uris(text)
    if not nodes:
        raise LoadError(f"没有解析出节点；已跳过 {len(skipped)} 行")
    return LoadResult(nodes=nodes, carrier=f"{carrier}:uri", source=source, skipped=skipped)
