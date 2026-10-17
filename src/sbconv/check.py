"""Static reference checks for tag references and rule-set names, which `sing-box check` ignores."""

from __future__ import annotations

from typing import Any

GROUP_TYPES = ("selector", "urltest")


def check(config: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Return (errors, warnings). A non-empty error list blocks delivery."""
    errors: list[str] = []
    warnings: list[str] = []

    outbounds = config.get("outbounds") or []
    tags = [o.get("tag") for o in outbounds]
    dup = {t for t in tags if t and tags.count(t) > 1}
    if dup:
        errors.append(f"outbound tag 重名：{sorted(dup)}")
    defined = {t for t in tags if t}

    group_members: dict[str, list[str]] = {}
    for out in outbounds:
        if out.get("type") not in GROUP_TYPES:
            continue
        members = out.get("outbounds") or []
        group_members[out["tag"]] = list(members)
        if not members:
            errors.append(f"分组 {out['tag']!r} 为空，内核会拒绝启动")
        for m in members:
            if m not in defined:
                errors.append(f"分组 {out['tag']!r} 引用了不存在的 tag {m!r}")
        default = out.get("default")
        if default:
            if default not in defined:
                errors.append(f"分组 {out['tag']!r} 的 default {default!r} 不存在")
            elif default not in members:
                # sing-box looks for `default` among that group's members only. A
                # test used `regions: [US]` with `default: US`, and the core
                # stopped with FATAL: default outbound not found: US.
                errors.append(f"分组 {out['tag']!r} 的 default {default!r} 不是本分组的成员")

    rule_sets = {r.get("tag") for r in (config.get("route", {}).get("rule_set") or [])}
    route = config.get("route") or {}
    for i, rule in enumerate(route.get("rules") or []):
        if rule.get("action") == "direct":
            errors.append(
                f"route.rules[{i}] 用了 action: direct，它在 sing-box 1.14.3 上是空操作，"
                "要写 outbound: Direct"
            )
        out = rule.get("outbound")
        if out and out not in defined:
            errors.append(f"route.rules[{i}] 的 outbound {out!r} 不存在")
        for tag in _as_list(rule.get("rule_set")):
            if tag not in rule_sets:
                errors.append(f"route.rules[{i}] 引用了未定义的 rule_set {tag!r}")
    final = route.get("final")
    if final and final not in defined:
        errors.append(f"route.final {final!r} 不存在")

    dns = config.get("dns") or {}
    dns_server_tags = {s.get("tag") for s in (dns.get("servers") or [])}
    for i, rule in enumerate(dns.get("rules") or []):
        server = rule.get("server")
        if server and server not in dns_server_tags:
            errors.append(f"dns.rules[{i}] 的 server {server!r} 不在 dns.servers 里")
        for tag in _as_list(rule.get("rule_set")):
            if tag not in rule_sets:
                errors.append(f"dns.rules[{i}] 引用了未定义的 rule_set {tag!r}")
    dns_final = dns.get("final")
    if dns_final and dns_final not in dns_server_tags:
        errors.append(f"dns.final {dns_final!r} 不在 dns.servers 里")
    resolver = (route.get("default_domain_resolver") or {}).get("server")
    if not resolver:
        errors.append("route.default_domain_resolver 缺失，内核会直接 FATAL")
    elif resolver not in dns_server_tags:
        errors.append(f"route.default_domain_resolver 指向的 {resolver!r} 不在 dns.servers 里")
    client = route.get("default_http_client")
    clients = {c.get("tag"): c for c in (config.get("http_clients") or [])}
    if client:
        if client not in clients:
            errors.append(f"route.default_http_client {client!r} 不在 http_clients 里")
    for tag, entry in clients.items():
        detour = entry.get("detour")
        if detour and detour not in defined:
            errors.append(f"http_clients[{tag!r}] 的 detour {detour!r} 不是已定义的出站")
    if not client:
        warnings.append("未设 route.default_http_client，内核会用默认出站抓规则集并报弃用警告")

    used_rule_sets: set[str] = set()
    for rule in list(route.get("rules") or []) + list(dns.get("rules") or []):
        used_rule_sets.update(_as_list(rule.get("rule_set")))
    unused = sorted(rule_sets - used_rule_sets)
    if unused:
        warnings.append(f"{len(unused)} 个规则集没有被任何规则引用，会白下载：{unused}")

    # The direct outbound carries no node, so exclude every outbound of that type.
    direct_tags = {o["tag"] for o in outbounds if o.get("type") == "direct"}
    node_tags = [o["tag"] for o in outbounds if o.get("type") not in GROUP_TYPES and o["tag"] not in direct_tags]
    referenced = {m for members in group_members.values() for m in members if m in node_tags}
    orphans = [t for t in node_tags if t not in referenced]
    if orphans:
        warnings.append(f"{len(orphans)} 个节点没有被任何分组引用：{orphans[:5]}")

    return errors, warnings


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return list(value)
