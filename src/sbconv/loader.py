"""Loader: one Clash YAML file becomes a node list.

The file is the only input. The loader fetches no URL and parses no link list,
so the same file always gives the same nodes.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from .models import LoadResult, Node


class LoadError(Exception):
    """The file holds no readable nodes."""


def _read(source: str) -> str:
    if source.startswith(("http://", "https://")):
        raise LoadError(f"只读本地 Clash 配置文件，不抓取订阅链接：{source}")
    path = Path(source)
    if not path.is_file():
        raise LoadError(f"文件不存在：{source}")
    return path.read_text(encoding="utf-8", errors="replace")


def _parse_clash(text: str) -> tuple[list[Node], dict[str, str]]:
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise LoadError(f"不是合法的 YAML：{exc}") from exc
    if not isinstance(doc, dict) or "proxies" not in doc:
        raise LoadError("不是 Clash 配置：缺少 proxies 段")
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
        nodes.append(Node(name=name, kind=kind, clash=payload))
    hosts = {str(k): str(v) for k, v in (doc.get("hosts") or {}).items()}
    return nodes, hosts


def load(source: str) -> LoadResult:
    """Read the Clash file and return the node list."""
    nodes, hosts = _parse_clash(_read(source))
    if not nodes:
        raise LoadError("Clash 配置里没有 proxies")
    return LoadResult(nodes=nodes, hosts=hosts)
