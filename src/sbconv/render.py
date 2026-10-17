"""Renderer: an artifact plus nodes become a sing-box configuration. The code is deterministic, and the agent writes no JSON."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from .artifacts import Artifact, GroupSpec
from .clean import CleanReport
from .models import Node

METHOD_ALIAS = {
    "chacha20-poly1305": "chacha20-ietf-poly1305",
    "xchacha20-poly1305": "xchacha20-ietf-poly1305",
}

# The renderer appends these outbounds, so a group may reference them directly.
# The tag is capitalized to sit next to Proxy in a client's outbound list.
BUILTIN_OUTBOUNDS = frozenset({"Direct"})


class RenderError(Exception):
    """An artifact and its nodes do not agree."""


def _obfs_opts(opts: dict[str, Any]) -> str:
    parts = []
    if opts.get("mode"):
        parts.append(f"obfs={opts['mode']}")
    if opts.get("host"):
        parts.append(f"obfs-host={opts['host']}")
    return ";".join(parts)


def _ss_from_clash(node: Node) -> dict[str, Any]:
    clash = node.clash or {}
    out: dict[str, Any] = {
        "type": "shadowsocks",
        "tag": node.name,
        "server": str(clash.get("server") or ""),
        "server_port": int(clash.get("port") or 0),
        "method": METHOD_ALIAS.get(str(clash.get("cipher") or ""), str(clash.get("cipher") or "")),
        "password": str(clash.get("password") or ""),
    }
    if node.plugin:
        out["plugin"] = node.plugin
        opts = _obfs_opts(clash.get("plugin-opts") or {})
        if opts:
            out["plugin_opts"] = opts
    if clash.get("tfo"):
        out["tcp_fast_open"] = True
    if clash.get("mptcp"):
        out["tcp_multi_path"] = True
    return out


def _ss_from_uri(node: Node) -> dict[str, Any]:
    import base64
    import binascii

    parts = urlsplit(node.uri or "")
    userinfo = unquote(parts.username or "")
    password = unquote(parts.password or "")
    if not password:
        padded = userinfo + "=" * (-len(userinfo) % 4)
        try:
            decoded = base64.b64decode(padded).decode("utf-8")
        except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
            raise RenderError(f"节点 {node.name!r} 的 ss 链接解不出 method:password") from exc
        userinfo, _, password = decoded.partition(":")
    query = parse_qs(parts.query)
    out: dict[str, Any] = {
        "type": "shadowsocks",
        "tag": node.name,
        "server": parts.hostname or "",
        "server_port": parts.port or 0,
        "method": METHOD_ALIAS.get(userinfo, userinfo),
        "password": password,
    }
    if node.plugin:
        out["plugin"] = node.plugin
        raw = (query.get("plugin") or [""])[0]
        _, _, opts = raw.partition(";")
        if opts:
            out["plugin_opts"] = opts
    return out


def build_node_outbound(node: Node) -> dict[str, Any]:
    if node.kind != "ss":
        raise RenderError(f"暂不支持协议 {node.kind}（节点 {node.name!r}）")
    if node.origin == "clash":
        out = _ss_from_clash(node)
    else:
        out = _ss_from_uri(node)
    if not out["server"] or not out["server_port"] or not out["password"]:
        raise RenderError(f"节点 {node.name!r} 缺少 server / port / password")
    return out


def proxy_mode(inbounds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build proxy-mode inbounds: a TUN that captures no traffic, plus the mixed inbound.

    A client that runs the core as a Windows service writes a bare
    `set_system_proxy` to the service account, so the desktop user never sees it.
    The TUN platform path impersonates the logged-on user instead
    (SagerNet/sing-box#4447). The TUN installs no route, so only applications that
    honour the system proxy use this configuration.
    """
    listener = next(
        (i for i in inbounds if i.get("type") in ("mixed", "http") and i.get("listen_port")), None
    )
    if listener is None:
        raise RenderError("代理模式需要一个带 listen_port 的 mixed 或 http 入站")
    server = listener.get("listen") or "127.0.0.1"

    out: list[dict[str, Any]] = []
    for item in inbounds:
        if item.get("type") == "tun":
            carrier = {**item, "auto_route": False, "strict_route": False, "dns_mode": "disabled"}
            carrier["platform"] = {
                "http_proxy": {"enabled": True, "server": server, "server_port": listener["listen_port"]}
            }
            out.append(carrier)
            continue
        if item.get("type") in ("mixed", "http"):
            item = {k: v for k, v in item.items() if k != "set_system_proxy"}
        out.append(item)
    return out


def resolve_groups(artifact: Artifact, nodes: list[Node]) -> list[dict[str, Any]]:
    """Resolve the groups that the artifact declares, in declaration order.

    A referenced group joins this group as a tag, not as expanded nodes. This is
    the meaning of Proxy = [Auto, HK, ...].
    """
    by_region: dict[str, list[str]] = {}
    for node in nodes:
        by_region.setdefault(node.region or artifact.nodes.fallback_region, []).append(node.name)

    all_tags = [n.name for n in nodes]
    region_of = {n.name: (n.region or artifact.nodes.fallback_region) for n in nodes}
    declared: dict[str, list[str]] = {}
    outbounds: list[dict[str, Any]] = []

    for group in artifact.groups:
        members: list[str] = []
        # Order: the references to other groups first, then the nodes that
        # regions picks.
        for tag in group.groups:
            if tag not in declared and tag not in BUILTIN_OUTBOUNDS:
                raise RenderError(f"分组 {group.tag!r} 引用了未声明在前的分组 {tag!r}")
            members.append(tag)

        picked: list[str] = []
        for code in group.regions:
            picked.extend(all_tags if code == "*" else by_region.get(code, []))
        if group.exclude_regions:
            picked = [m for m in picked if region_of.get(m) not in group.exclude_regions]
        if group.include:
            rx = re.compile(group.include)
            picked = [m for m in picked if rx.search(m)]
        if group.exclude:
            rx = re.compile(group.exclude)
            picked = [m for m in picked if not rx.search(m)]
        members.extend(picked)

        seen: set[str] = set()
        unique = []
        for m in members:
            if m not in seen:
                seen.add(m)
                unique.append(m)
        declared[group.tag] = unique
        outbounds.append(_group_outbound(group, unique))
    return outbounds


def _group_outbound(group: GroupSpec, members: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {"type": group.type, "tag": group.tag, "outbounds": members}
    if group.default:
        out["default"] = group.default
    if group.type == "urltest":
        out["url"] = group.url or "https://www.gstatic.com/generate_204"
        out["interval"] = group.interval or "5m"
        if group.tolerance is not None:
            out["tolerance"] = group.tolerance
    return out


def build_dns(artifact: Artifact) -> dict[str, Any]:
    servers = [s.model_dump(exclude_none=True) for s in artifact.dns.servers]
    dns: dict[str, Any] = {"servers": servers, "rules": list(artifact.dns.rules)}
    if artifact.dns.final:
        dns["final"] = artifact.dns.final
    if artifact.meta.strategy:
        dns["strategy"] = artifact.meta.strategy
    return dns


def build_route(artifact: Artifact) -> dict[str, Any]:
    # Key order follows the schema, so the output reads like the documentation.
    route: dict[str, Any] = {"rules": list(artifact.route.rules)}
    if artifact.rule_sets:
        route["rule_set"] = [r.model_dump() for r in artifact.rule_sets]
    if artifact.route.final:
        route["final"] = artifact.route.final
    route["auto_detect_interface"] = artifact.route.auto_detect_interface
    route["default_domain_resolver"] = {"server": artifact.route.default_domain_resolver}
    if artifact.route.default_http_client:
        route["default_http_client"] = artifact.route.default_http_client
    return route


def render(artifact: Artifact, nodes: list[Node], report: CleanReport) -> tuple[dict[str, Any], list[str]]:
    """Return (config, warnings)."""
    warnings: list[str] = []
    node_outbounds = []
    renderable: list[Node] = []
    for node in nodes:
        try:
            node_outbounds.append(build_node_outbound(node))
        except RenderError as exc:
            warnings.append(str(exc))
            continue
        renderable.append(node)

    # A group references only the nodes that reached the outbounds list.
    groups = resolve_groups(artifact, renderable)
    if not node_outbounds:
        raise RenderError("没有任何节点可以渲染")

    resolver = artifact.route.default_domain_resolver
    direct = {"type": "direct", "tag": "Direct", "domain_resolver": {"server": resolver}}
    outbounds: list[dict[str, Any]] = []
    outbounds.extend(node_outbounds)
    outbounds.extend(groups)
    outbounds.append(direct)

    config: dict[str, Any] = {"log": {"level": "warn"}, "dns": build_dns(artifact)}
    if artifact.http_clients:
        config["http_clients"] = [c.model_dump(exclude_none=True) for c in artifact.http_clients]
    config["inbounds"] = list(artifact.inbounds)
    config["outbounds"] = outbounds
    config["route"] = build_route(artifact)
    if artifact.http_clients:
        config["experimental"] = {"cache_file": {"enabled": True, "store_fakeip": True}}
    if report.unknown:
        warnings.append(f"{len(report.unknown)} 个节点未识别地区，归入 {artifact.nodes.fallback_region}")
    return config, warnings
