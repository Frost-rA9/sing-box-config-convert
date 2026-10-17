"""Clean the node list and detect regions.

Remove information nodes by name before the deduplication by server:port. The
reverse order removes the real node, because two information nodes share a
server:port with the first real node here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlsplit

from .artifacts import NodeRules
from .models import Node
from .regions import match_chinese_name, match_flag, match_iso_code


@dataclass(slots=True)
class CleanReport:
    dropped: list[tuple[str, str]] = field(default_factory=list)
    renamed: list[tuple[str, str]] = field(default_factory=list)
    region_of: dict[str, str] = field(default_factory=dict)
    distribution: dict[str, int] = field(default_factory=dict)
    unknown: list[str] = field(default_factory=list)
    plugin_changed: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def unknown_count(self) -> int:
        return len(self.unknown)


def server_port(node: Node) -> tuple[str, str] | None:
    """Return the server and the port of a node, from either carrier."""
    if node.clash is not None:
        server = node.clash.get("server")
        port = node.clash.get("port")
        if server is None or port is None:
            return None
        return str(server), str(port)
    if node.uri:
        parts = urlsplit(node.uri)
        if parts.hostname is None or parts.port is None:
            return None
        return parts.hostname, str(parts.port)
    return None


def raw_plugin(node: Node) -> str | None:
    if node.clash is not None:
        plugin = node.clash.get("plugin")
        return str(plugin) if plugin else None
    if node.uri:
        query = parse_qs(urlsplit(node.uri).query)
        plugin = (query.get("plugin") or [None])[0]
        if not plugin:
            return None
        return plugin.split(";", 1)[0]
    return None


def normalize_plugin(plugin: str | None, alias: dict[str, str]) -> str | None:
    """Normalize a plugin name to the spelling that sing-box accepts."""
    if not plugin:
        return None
    key = plugin.strip().lower()
    for src, dst in alias.items():
        if key == src.lower():
            return dst
    return plugin


def apply_plugin_alias(node: Node, alias: dict[str, str]) -> tuple[str, str] | None:
    """Write the normalized plugin name and return (old, new), or None when nothing changed."""
    raw = raw_plugin(node)
    if raw is None:
        node.plugin = None
        return None
    fixed = normalize_plugin(raw, alias)
    node.plugin = fixed
    if fixed != raw:
        return raw, fixed
    return None


def detect_region(name: str, rules: NodeRules) -> tuple[str, str] | None:
    """Return (evidence, region code), checked in the order region_overrides, then region_keys."""
    for override in rules.region_overrides:
        if override.match_name in name:
            return f"override:{override.match_name}", override.region
    for key in rules.region_keys:
        if key.type == "chinese_name":
            hit = match_chinese_name(name)
        elif key.type == "flag_emoji":
            hit = match_flag(name)
        elif key.type == "iso_code":
            hit = match_iso_code(name)
        else:  # pragma: no cover - the schema limits the values
            continue
        if hit:
            return f"{key.type}:{hit[0]}", hit[1]
    return None


def clean(nodes: list[Node], rules: NodeRules) -> tuple[list[Node], CleanReport]:
    """Clean the nodes in place. Return the kept nodes and the report."""
    report = CleanReport()
    keywords = [k.lower() for k in rules.drop_name_keywords]

    kept: list[Node] = []
    for node in nodes:
        lowered = node.name.lower()
        hit = next((k for k in keywords if k in lowered), None)
        if hit is not None:
            node.dropped = f"名字含关键字 {hit}"
            report.dropped.append((node.name, node.dropped))
            continue
        kept.append(node)

    if rules.drop_duplicate_by:
        seen: dict[tuple[str, ...], str] = {}
        deduped: list[Node] = []
        for node in kept:
            sp = server_port(node)
            if sp is None:
                deduped.append(node)
                continue
            available = {"server": sp[0], "server_port": sp[1]}
            key = tuple(available.get(f, "") for f in rules.drop_duplicate_by)
            if key in seen:
                node.dropped = f"与 {seen[key]} 的 {'+'.join(rules.drop_duplicate_by)} 相同"
                report.dropped.append((node.name, node.dropped))
                continue
            seen[key] = node.name
            deduped.append(node)
        kept = deduped

    used: dict[str, int] = {}
    for node in kept:
        base = node.name
        if base in used:
            used[base] += 1
            node.name = f"{base} {used[base]}"
            report.renamed.append((base, node.name))
        else:
            used[base] = 1

    for node in kept:
        changed = apply_plugin_alias(node, rules.plugin_alias)
        if changed:
            report.plugin_changed.append((node.name, changed[0], changed[1]))

    for node in kept:
        hit = detect_region(node.name, rules)
        if hit is None:
            node.region = rules.fallback_region
            report.unknown.append(node.name)
        else:
            node.region = hit[1]
        report.region_of[node.name] = node.region
        report.distribution[node.region] = report.distribution.get(node.region, 0) + 1

    return kept, report
