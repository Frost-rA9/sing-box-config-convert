#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Clash 订阅 -> sing-box 配置：三阶段确定性转换器。

    [1] --stage nodes   sub/raw-*.yaml                        -> build/nodes.json
    [2] --stage base    build/nodes.json + base/ + policy.json -> build/base.json
    [3] --stage final   build/base.json + custom/ + policy.json -> out/config.*.json
        不带 --stage 则三阶段连跑。

职责边界：本脚本只做机械映射，不做意图判断。所有意图来自 base/ custom/ policy.json。
不变量由 tools/verify.py 校验，见 AGENTS.md。
"""

import argparse
import glob
import json
import os
import sys
from collections import OrderedDict

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUB_DIR = os.path.join(ROOT, "sub")
BASE_DIR = os.path.join(ROOT, "base")
CUSTOM_DIR = os.path.join(ROOT, "custom")
BUILD_DIR = os.path.join(ROOT, "build")
OUT_DIR = os.path.join(ROOT, "out")

BUILTIN = {"direct", "reject"}
DEFAULT_OUTBOUND = "Proxy"


def log(msg):
    print(msg, file=sys.stderr)


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def strip_meta(obj):
    """去掉以 $ 开头的说明/锚点键（它们只在声明文件里有意义）。"""
    return {k: v for k, v in obj.items() if not k.startswith("$")}


# ================================================================ [1] nodes

def stage_nodes(sub_path=None, report=None):
    if sub_path:
        path = sub_path
    else:
        files = sorted(glob.glob(os.path.join(SUB_DIR, "raw-*.yaml")))
        if not files:
            raise SystemExit("sub/ 下没有 raw-*.yaml 订阅快照（或用 --sub 指定）")
        path = files[-1]

    with open(path, encoding="utf-8") as f:
        sub = yaml.safe_load(f)

    nodes = []
    for p in sub["proxies"]:
        tls = {"enabled": True}
        if p.get("sni"):
            tls["server_name"] = p["sni"]
        if p.get("alpn"):
            tls["alpn"] = p["alpn"]
        if p.get("skip-cert-verify") is not None:
            tls["insecure"] = bool(p["skip-cert-verify"])
        if p.get("client-fingerprint"):
            tls["utls"] = {"enabled": True, "fingerprint": p["client-fingerprint"]}
        nodes.append({
            "type": p["type"],
            "tag": p["name"],
            "server": p["server"],
            "server_port": int(p["port"]),
            "password": p["password"],
            "tls": tls,
        })

    # 订阅里出现过的分类目标 —— 作为 agent「按需加自定义」的候选清单
    targets = OrderedDict()
    for raw in sub.get("rules", []):
        parts = [x.strip() for x in raw.split(",")]
        if parts[0].upper() in ("MATCH", "GEOIP"):
            continue
        if len(parts) > 2:
            targets[parts[2]] = targets.get(parts[2], 0) + 1

    out = {"$source": os.path.basename(path), "count": len(nodes), "nodes": nodes}
    write_json(os.path.join(BUILD_DIR, "nodes.json"), out)

    proto = {}
    for n in nodes:
        proto[n["type"]] = proto.get(n["type"], 0) + 1
    log(f"[ok] build/nodes.json  <- {out['$source']}")
    log(f"     节点 {len(nodes)} 个，协议: {proto}")
    if targets:
        log(f"[i] 订阅里出现过的分类目标 {len(targets)} 个（当前都走 final，"
            f"需要独立策略就在 custom/ 里加组 + 映射）:")
        log("     " + ", ".join(f"{k}({v})" for k, v in
                                sorted(targets.items(), key=lambda kv: -kv[1])))
    log("[i] 订阅的 %d 条内联规则不进入产物 —— 基础 5 组下它们与粗粒度分类得到同样的路由结果"
        % len(sub.get("rules", [])))
    return out


# ================================================================ 组装

def expand_members(members, node_tags, group_tags, report, owner):
    known = set(node_tags) | group_tags | BUILTIN
    out = []
    for m in members:
        if m == "@all":
            out.extend(node_tags)
        elif m.startswith("@"):
            name = m[1:]
            if name in BUILTIN or name in group_tags:
                out.append(name)
            else:
                report["bad_refs"].append(f"{owner} -> {m}")
        elif m in known:
            out.append(m)
        else:
            report["bad_refs"].append(f"{owner} -> {m}")
    return list(OrderedDict.fromkeys(out))


def detect_group_cycles(groups, report):
    """组之间循环引用会让内核起不来 —— 只报错，不参与排序。"""
    tags = {g["tag"] for g in groups}
    deps = {g["tag"]: [m for m in g["members"] if m in tags] for g in groups}
    state, stack = {}, []

    def visit(tag):
        if state.get(tag) == 2:
            return
        if state.get(tag) == 1:
            i = stack.index(tag)
            report["bad_refs"].append("分组循环引用: " + " -> ".join(stack[i:] + [tag]))
            return
        state[tag] = 1
        stack.append(tag)
        for dep in deps[tag]:
            visit(dep)
        stack.pop()
        state[tag] = 2

    for g in groups:
        visit(g["tag"])


def order_groups(groups, report):
    """产物顺序 = 声明里的 order 字段（也是 GUI「组」页的卡片顺序）。

    sing-box 不要求 outbounds 按依赖序排列（引用按 tag 解析，前向引用合法），
    所以这里不做拓扑排序 —— 顺序完全由声明决定：order 升序，同值按声明序，缺省 1000。
    """
    detect_group_cycles(groups, report)
    return [g for _, g in sorted(enumerate(groups),
                                 key=lambda kv: (kv[1].get("order", 1000), kv[0]))]


def assemble_outbounds(nodes, groups, report):
    node_tags = [n["tag"] for n in nodes]
    group_tags = {g["tag"] for g in groups}
    outbounds = list(nodes)

    ordered = order_groups(groups, report)
    used = set()
    for g in ordered:
        members = expand_members(g["members"], node_tags, group_tags, report, g["tag"])
        if g["type"] == "select":
            ob = {"type": "selector", "tag": g["tag"], "outbounds": members}
        elif g["type"] == "urltest":
            ob = {"type": "urltest", "tag": g["tag"], "outbounds": members,
                  "url": g.get("url") or "https://www.gstatic.com/generate_204",
                  "interval": g.get("interval", "3m"),
                  "tolerance": g.get("tolerance", 50)}
        else:
            report["bad_refs"].append(f"未知分组类型: {g['tag']}({g['type']})")
            continue
        if g.get("default"):
            ob["default"] = g["default"][1:] if g["default"].startswith("@") else g["default"]
        if g.get("interrupt_exist_connections") is not None:
            ob["interrupt_exist_connections"] = g["interrupt_exist_connections"]
        used.update(members)
        outbounds.append(ob)

    # 内置出站：只补真正被引用到的（direct 总是补，规则里常用）
    referenced = {m[1:] for g in groups for m in g["members"] if m.startswith("@")}
    for tag in ("direct", "reject"):
        if tag in used or tag in referenced or tag == "direct":
            # sing-box 没有 reject 出站类型（reject 是规则动作），内置拦截出站只能是 block
            outbounds.append({"type": "block" if tag == "reject" else tag, "tag": tag})

    return outbounds, {g["tag"] for g in ordered}


def collect_custom_rules(custom_targets, custom_rules, report):
    """返回 [(position, rule_obj)]。position ∈ head / before-catchall / tail。"""
    items = []
    for name, spec in (custom_targets.get("targets") or {}).items():
        if not spec.get("rule_set"):
            report["bad_refs"].append(f"custom target {name} 没有 rule_set")
            continue
        rule = {"rule_set": spec["rule_set"], "action": "route",
                "outbound": spec.get("outbound") or DEFAULT_OUTBOUND}
        items.append((spec.get("position", "before-catchall"), rule))
    for rule in (custom_rules.get("rules") or []):
        pos = rule.get("position", "before-catchall")
        items.append((pos, strip_meta(rule)))
    return items


def assemble_rules(policy, base_cfg, custom_targets, custom_rules, report):
    base_rules = [strip_meta(r) for r in base_cfg["rules"]]
    anchors = [i for i, r in enumerate(base_cfg["rules"]) if r.get("$anchor") == "catchall"]
    cut = anchors[0] if anchors else len(base_rules)
    pre, anchor, post = base_rules[:cut], base_rules[cut:cut + 1], base_rules[cut + 1:]

    items = collect_custom_rules(custom_targets, custom_rules, report)
    head = [r for p, r in items if p == "head"]
    mid = [r for p, r in items if p in ("before-catchall", None)]
    tail = [r for p, r in items if p == "tail"]

    rules = [{"protocol": "dns", "action": "hijack-dns"}]
    rules += [strip_meta(r) for r in policy["route"].get("head_rules", [])]
    rules += head
    rules += pre
    rules += mid
    rules += anchor
    rules += post
    rules += tail
    rules += [strip_meta(r) for r in policy["route"].get("extra_rules", [])]

    report["custom_rules"] = len(items)
    return rules


def collect_rule_sets(policy, base_cfg, rules, report):
    needed = set()
    for r in rules:
        needed.update(r.get("rule_set", []))
    for r in policy["dns"].get("rules", []):
        needed.update(r.get("rule_set", []))
    needed.update(policy["inbounds"]["tun"].get("route_exclude_address_set", []))

    base_urls = base_cfg["rule_set_base"]
    rs_cfg = base_cfg["rule_set"]
    rule_sets, manifest = [], {}
    for tag in sorted(needed):
        kind = tag.split("-", 1)[0]
        base = base_urls.get(kind)
        if not base:
            report["bad_refs"].append(f"rule-set 无法推导 URL（未知前缀）: {tag}")
            continue
        url = f"{base}{tag}.srs"
        bootstrap = f"{rs_cfg['bootstrap_dir']}/{tag}.srs"
        rule_sets.append({
            "type": "remote", "tag": tag, "format": rs_cfg["format"], "url": url,
            # 相对路径按「进程工作目录」解析，必须写绝对路径
            "initial_path": os.path.abspath(os.path.join(ROOT, bootstrap)).replace("\\", "/"),
            "update_interval": rs_cfg["update_interval"],
            "http_client": rs_cfg["http_client"],
        })
        manifest[tag] = {"url": url, "initial_path": bootstrap}
    return rule_sets, manifest


def build_body(policy, outbounds, rule_sets, rules, final):
    body = OrderedDict()
    body["log"] = policy["log"]
    body["experimental"] = policy["experimental"]
    body["http_clients"] = policy["http_clients"]
    body["outbounds"] = outbounds
    body["dns"] = OrderedDict([
        ("servers", list(policy["dns"]["servers"].values())),
        ("rules", policy["dns"]["rules"]),
        ("final", policy["dns"]["final"]),
        ("strategy", policy["dns"]["strategy"]),
    ])
    route = OrderedDict()
    route["rules"] = rules
    route["rule_set"] = rule_sets
    route["final"] = final
    for k, v in policy["route"].items():
        if k not in ("rules", "rule_set", "final", "head_rules", "extra_rules") and not k.startswith("$"):
            route[k] = v
    body["route"] = route
    return body


# ================================================================ [2] base

def stage_base(report=None):
    report = report if report is not None else new_report()
    nodes_path = os.path.join(BUILD_DIR, "nodes.json")
    if not os.path.exists(nodes_path):
        raise SystemExit("缺少 build/nodes.json，先跑 --stage nodes")
    nodes = load_json(nodes_path)["nodes"]
    policy = load_json(os.path.join(ROOT, "policy.json"))
    base_groups = load_json(os.path.join(BASE_DIR, "groups.json"))["groups"]
    base_cfg = load_json(os.path.join(BASE_DIR, "rules.json"))

    outbounds, group_tags = assemble_outbounds(nodes, base_groups, report)
    rules = assemble_rules(policy, base_cfg, {}, {}, report)
    rule_sets, _ = collect_rule_sets(policy, base_cfg, rules, report)

    body = build_body(policy, outbounds, rule_sets, rules, base_cfg["final"])
    body = OrderedDict([("$note", "基础配置（无 inbounds、无 custom）—— 由 --stage base 生成"),
                        *body.items()])
    write_json(os.path.join(BUILD_DIR, "base.json"), body)

    log(f"[ok] build/base.json")
    log(f"     出站 {len(outbounds)}（节点 {len(nodes)} + 基础组 {len(group_tags)} + 内置）")
    log(f"     规则 {len(rules)} 条 / rule-set {len(rule_sets)} 个 / final {base_cfg['final']}")
    return body


# ================================================================ [3] final

def stage_final(report=None):
    report = report if report is not None else new_report()
    base_path = os.path.join(BUILD_DIR, "base.json")
    if not os.path.exists(base_path):
        raise SystemExit("缺少 build/base.json，先跑 --stage base")
    nodes = load_json(os.path.join(BUILD_DIR, "nodes.json"))["nodes"]
    policy = load_json(os.path.join(ROOT, "policy.json"))
    base_groups = load_json(os.path.join(BASE_DIR, "groups.json"))["groups"]
    base_cfg = load_json(os.path.join(BASE_DIR, "rules.json"))
    custom_groups = load_json(os.path.join(CUSTOM_DIR, "groups.json")).get("groups", [])
    custom_targets = load_json(os.path.join(CUSTOM_DIR, "targets.json"))
    custom_rules = load_json(os.path.join(CUSTOM_DIR, "rules.json"))

    groups = base_groups + custom_groups
    overridden = {g["tag"] for g in custom_groups} & {g["tag"] for g in base_groups}
    if overridden:
        report["overridden_groups"] = sorted(overridden)

    outbounds, group_tags = assemble_outbounds(nodes, groups, report)
    rules = assemble_rules(policy, base_cfg, custom_targets, custom_rules, report)
    rule_sets, manifest = collect_rule_sets(policy, base_cfg, rules, report)

    body = build_body(policy, outbounds, rule_sets, rules, base_cfg["final"])

    artifacts = {}
    for mode, name in (("tun", "config.tun.json"), ("proxy", "config.proxy.json")):
        cfg = OrderedDict()
        for k, v in body.items():
            if not k.startswith("$"):
                cfg[k] = v
        # inbounds 按模式贴上去，插在 log/experimental/http_clients 之后
        inbounds = []
        tun_src = policy["inbounds"]["tun"]
        if mode == "tun" and tun_src.get("enabled", True):
            inbounds.append(dict({"type": "tun"},
                                 **{k: v for k, v in tun_src.items() if k != "enabled"}))
        mixed_src = policy["inbounds"]["mixed"]
        idle_src = policy["inbounds"].get("tun_idle", {})
        if mode != "tun" and idle_src and idle_src.get("enabled", True):
            # 空载 tun：不接管流量，只承载 platform.http_proxy（官方客户端据此把系统代理
            # 写进登录用户的 hive）。核心本体忽略 platform.*，所以它对本仓脚本无副作用。
            idle = strip_meta({k: v for k, v in idle_src.items() if k != "enabled"})
            http_proxy = idle.get("platform", {}).get("http_proxy")
            if http_proxy and http_proxy.get("server_port") == "@mixed":
                http_proxy["server_port"] = mixed_src.get("listen_port")
            inbounds.append(dict({"type": "tun"}, **idle))
        if mixed_src.get("enabled", True):
            mixed = {k: v for k, v in mixed_src.items() if k != "enabled"}
            if mode == "tun":
                mixed.pop("set_system_proxy", None)  # TUN 已接管，再设系统代理多余
            inbounds.append(dict({"type": "mixed"}, **mixed))
        cfg["inbounds"] = inbounds
        if mode != "tun":
            cfg["route"]["rules"] = [r for r in cfg["route"]["rules"]
                                     if r.get("action") != "hijack-dns"]
        # 重新排序为常规顺序
        ordered = OrderedDict()
        for key in ("log", "experimental", "http_clients", "inbounds", "outbounds", "dns", "route"):
            ordered[key] = cfg[key]
        path = os.path.join(OUT_DIR, name)
        write_json(path, ordered)
        artifacts[mode] = path

    write_json(os.path.join(OUT_DIR, "bootstrap_manifest.json"), manifest)
    write_changelog(nodes, group_tags, rule_sets, report)

    for mode, path in artifacts.items():
        log(f"[ok] {os.path.relpath(path, ROOT)}")
    log(f"     出站 {len(outbounds)} / 规则 {len(rules)} / rule-set {len(rule_sets)}")
    log(f"     策略组 {len(group_tags)}: {', '.join(sorted(group_tags))}")
    if report["custom_rules"]:
        log(f"[i] custom 层贡献 {report['custom_rules']} 条规则、{len(custom_groups)} 个组")
    if report["overridden_groups"]:
        log(f"[!] custom 覆盖了基础组: {report['overridden_groups']}")
    if report["bad_refs"]:
        log(f"[!] 引用错误 {len(report['bad_refs'])} 条: {report['bad_refs'][:5]}")
    return artifacts


def write_changelog(nodes, group_tags, rule_sets, report):
    state_path = os.path.join(OUT_DIR, ".last_state.json")
    state = {"nodes": [n["tag"] for n in nodes], "groups": sorted(group_tags),
             "rule_sets": [r["tag"] for r in rule_sets]}
    prev = load_json(state_path) if os.path.exists(state_path) else None
    lines = ["# 生成记录\n", f"- 节点: {len(state['nodes'])}",
             f"- 策略组: {len(state['groups'])}", f"- rule-set: {len(state['rule_sets'])}", ""]
    if prev:
        for key, label in (("nodes", "节点"), ("groups", "策略组"), ("rule_sets", "rule-set")):
            added = sorted(set(state[key]) - set(prev.get(key, [])))
            removed = sorted(set(prev.get(key, [])) - set(state[key]))
            if added or removed:
                lines.append(f"## {label}变化")
                if added:
                    lines.append(f"- 新增: {', '.join(added)}")
                if removed:
                    lines.append(f"- 移除: {', '.join(removed)}")
                lines.append("")
    else:
        lines.append("（首次生成，无对比基线）\n")
    with open(os.path.join(OUT_DIR, "CHANGELOG.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    write_json(state_path, state)


def new_report():
    return {"bad_refs": [], "custom_rules": 0, "overridden_groups": []}


def main():
    ap = argparse.ArgumentParser(description="Clash 订阅 -> sing-box 配置（三阶段）")
    ap.add_argument("--stage", choices=["nodes", "base", "final"], help="只跑某一阶段；省略则连跑")
    ap.add_argument("--sub", help="指定订阅文件（默认取 sub/ 下最新的 raw-*.yaml）")
    args = ap.parse_args()

    if args.stage in (None, "nodes"):
        stage_nodes(args.sub)
    if args.stage in (None, "base"):
        stage_base()
    if args.stage in (None, "final"):
        stage_final()


if __name__ == "__main__":
    main()
