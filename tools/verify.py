#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""不变量校验 —— 不需要内核、不联网，任何 agent 跑一条命令就知道产物合不合规。

    python tools/verify.py

这些不变量是踩坑换来的（见 AGENTS.md「已知坑」），不是风格偏好。
退出码 0 = 全部通过。
"""

import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "out")

PRIVATE_CIDRS = ["127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12",
                 "192.168.0.0/16", "169.254.0.0/16", "224.0.0.0/4",
                 "fd00::/8", "fe80::/10"]

# 基线层必须提供的 5 个基础组（顺序 = 声明里的 order）
BASE_GROUPS = ["Auto", "Proxy", "Direct", "Reject", "Final"]

# 基线层必须提供的 rule-set（粗粒度分类：CN/私网直连、广告拦截、境外代理）
BASE_RULE_SETS = ["geosite-private", "geosite-cn", "geoip-cn",
                  "geosite-category-ads-all", "geosite-geolocation-!cn"]

DECLARATIONS = ["policy.json", "base/groups.json", "base/rules.json",
                "custom/groups.json", "custom/targets.json", "custom/rules.json"]

# 只允许与 sing-box 内核同源的官方规则集来源，不依赖任何服务提供方的仓库
ALLOWED_RULE_SET_BASE = (
    "https://fastly.jsdelivr.net/gh/SagerNet/sing-geosite@rule-set/",
    "https://fastly.jsdelivr.net/gh/SagerNet/sing-geoip@rule-set/",
)

FAILED = []
PASSED = []


def ok(msg):
    PASSED.append(msg)
    print(f"  [ok]   {msg}")


def bad(msg):
    FAILED.append(msg)
    print(f"  [FAIL] {msg}")


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def section(title):
    print(f"\n=== {title} ===")


# ---------------------------------------------------------------- 声明文件

def check_declarations():
    section("1. 声明文件")
    decls = {}
    for name in DECLARATIONS:
        path = os.path.join(ROOT, name.replace("/", os.sep))
        if not os.path.exists(path):
            bad(f"{name} 不存在")
            continue
        try:
            decls[name] = load(path)
            ok(f"{name} 合法")
        except Exception as e:
            bad(f"{name} 解析失败: {e}")
    return decls


def check_layers(decls):
    """基线层必须完整；自定义层的引用必须可解析。"""
    section("2. 两层结构")

    base_groups = decls.get("base/groups.json", {}).get("groups", [])
    base_tags = [g["tag"] for g in base_groups]
    missing = [t for t in BASE_GROUPS if t not in base_tags]
    if missing:
        bad(f"基线层缺少基础组: {missing}")
    else:
        ok(f"基线层 5 个基础组齐备: {' / '.join(BASE_GROUPS)}")

    base_cfg = decls.get("base/rules.json", {})
    rs_tags = {r.get("tag") for r in [{"tag": t} for t in BASE_RULE_SETS]}
    used = set()
    for r in base_cfg.get("rules", []):
        used.update(r.get("rule_set", []))
    miss_rs = [t for t in BASE_RULE_SETS if t not in used]
    if miss_rs:
        bad(f"基线规则未引用这些基础 rule-set: {miss_rs}")
    else:
        ok(f"基线层 {len(BASE_RULE_SETS)} 个粗粒度 rule-set 齐备")

    anchors = [r for r in base_cfg.get("rules", []) if r.get("$anchor") == "catchall"]
    if anchors:
        ok("基线层有 catchall 锚点（custom 规则默认插在它之前）")
    else:
        bad("基线层缺少 $anchor: catchall 锚点")

    # 自定义层：组名与基础组冲突要告警；rule_set 前缀要能推导 URL
    cg = decls.get("custom/groups.json", {}).get("groups", [])
    overridden = sorted({g["tag"] for g in cg} & set(base_tags))
    if overridden:
        bad(f"custom 覆盖了基础组（基线不再是保证）: {overridden}")
    else:
        ok(f"自定义层新增 {len(cg)} 个组，未覆盖基础组")

    bases = set(base_cfg.get("rule_set_base", {}).keys())
    ct = decls.get("custom/targets.json", {}).get("targets", {})
    bad_prefix = [t for spec in ct.values() for t in spec.get("rule_set", [])
                  if t.split("-", 1)[0] not in bases]
    if bad_prefix:
        bad(f"custom 的 rule-set 无法推导 URL（未知前缀）: {bad_prefix}")
    else:
        ok(f"自定义层 {len(ct)} 条映射，rule-set 前缀均可推导")

    known = set(base_tags) | {g["tag"] for g in cg}
    bad_out = [spec["outbound"] for spec in ct.values()
               if spec.get("outbound") and spec["outbound"] not in known]
    if bad_out:
        bad(f"custom 映射指向了不存在的组: {sorted(set(bad_out))}")
    else:
        ok("自定义层引用的出站都存在")


# ---------------------------------------------------------------- 产物

def check_config(path, mode, decls):
    name = os.path.basename(path)
    section(f"{mode} 配置：{name}")
    if not os.path.exists(path):
        bad(f"{name} 不存在（先跑 tools/convert.py）")
        return None
    cfg = load(path)

    out_tags = {o["tag"] for o in cfg["outbounds"]}
    tun = next((i for i in cfg["inbounds"] if i["type"] == "tun"), None)
    mixed = next((i for i in cfg["inbounds"] if i["type"] == "mixed"), None)

    # --- 出站 tag 唯一
    tags = [o["tag"] for o in cfg["outbounds"]]
    if len(tags) == len(set(tags)):
        ok(f"出站 tag 唯一（{len(tags)} 个）")
    else:
        bad("出站 tag 有重复")

    # --- 引用完整性
    bad_out = [r["outbound"] for r in cfg["route"]["rules"]
               if r.get("outbound") and r["outbound"] not in out_tags]
    if bad_out:
        bad(f"规则引用了不存在的出站: {sorted(set(bad_out))}")
    else:
        ok("规则引用的出站都存在")

    if cfg["route"]["final"] in out_tags:
        ok(f"route.final = {cfg['route']['final']}")
    else:
        bad(f"route.final 指向不存在的出站: {cfg['route']['final']}")

    bad_detour = [s["tag"] for s in cfg["dns"]["servers"]
                  if s.get("detour") and s["detour"] not in out_tags]
    if bad_detour:
        bad(f"DNS 服务器 detour 指向不存在的出站: {bad_detour}")
    else:
        ok("DNS 服务器 detour 都合法")

    for c in cfg.get("http_clients", []):
        if c.get("detour") and c["detour"] not in out_tags:
            bad(f"http_client {c['tag']} 的 detour 不存在: {c['detour']}")
    ok("http_clients 引用合法")

    rs_tags = {r["tag"] for r in cfg["route"]["rule_set"]}
    used = set()
    for r in cfg["route"]["rules"] + cfg["dns"].get("rules", []):
        used.update(r.get("rule_set", []))
    if tun:
        used.update(tun.get("route_exclude_address_set", []))
    missing = used - rs_tags
    if missing:
        bad(f"引用了未声明的 rule-set: {sorted(missing)}")
    else:
        ok(f"rule-set 引用完整（{len(rs_tags)} 个）")

    # --- 基线 5 个粗粒度 rule-set 必须都在
    miss_base = [t for t in BASE_RULE_SETS if t not in rs_tags]
    if miss_base:
        bad(f"产物缺少基础 rule-set: {miss_base}")
    else:
        ok(f"基线 {len(BASE_RULE_SETS)} 个 rule-set 齐备")

    # --- 基础 5 组必须都在
    out_group_tags = {o["tag"] for o in cfg["outbounds"] if o["type"] in ("selector", "urltest")}
    miss_g = [t for t in BASE_GROUPS if t not in out_group_tags]
    if miss_g:
        bad(f"产物缺少基础组: {miss_g}")
    else:
        ok(f"基线 5 个基础组齐备（另有自定义组 {sorted(out_group_tags - set(BASE_GROUPS))}）")

    # --- 策略组顺序 = 声明里的 order（GUI 按 outbounds 顺序渲染卡片）
    decl_groups = decls.get("base/groups.json", {}).get("groups", []) + \
                  decls.get("custom/groups.json", {}).get("groups", [])
    want = list(dict.fromkeys(
        g["tag"] for _, g in sorted(enumerate(decl_groups),
                                     key=lambda kv: (kv[1].get("order", 1000), kv[0]))))
    got = [o["tag"] for o in cfg["outbounds"] if o["type"] in ("selector", "urltest")]
    if got != want:
        bad(f"策略组顺序与声明 order 不一致：期望 {want}，实际 {got}")
    else:
        ok(f"策略组顺序 = 声明 order：{' → '.join(want)}")

    # --- rule-set 来源与形式
    local = [r["tag"] for r in cfg["route"]["rule_set"] if r["type"] != "remote"]
    if local:
        bad(f"出现非远程 rule-set（约定不用本地文件）: {local}")
    else:
        ok("全部 rule-set 都是 remote")

    foreign = [r["url"] for r in cfg["route"]["rule_set"]
               if not r["url"].startswith(ALLOWED_RULE_SET_BASE)]
    if foreign:
        bad(f"rule-set 来源不在允许列表（不得依赖服务提供方仓库）: {foreign[:3]}")
    else:
        ok("rule-set 全部来自 SagerNet 官方仓库")

    rel = [r["initial_path"] for r in cfg["route"]["rule_set"]
           if not os.path.isabs(r["initial_path"])]
    if rel:
        bad(f"initial_path 不是绝对路径（相对路径按进程工作目录解析）: {rel[:3]}")
    else:
        ok("initial_path 全部是绝对路径")

    # --- default_domain_resolver 必须指向直连解析器，否则成环
    ddr = cfg["route"].get("default_domain_resolver")
    if not ddr:
        bad("缺少 route.default_domain_resolver（1.14 起必填，否则 FATAL）")
    else:
        srv = next((s for s in cfg["dns"]["servers"] if s["tag"] == ddr["server"]), None)
        if srv is None:
            bad(f"default_domain_resolver 指向不存在的 DNS 服务器: {ddr['server']}")
        elif srv.get("detour"):
            bad(f"default_domain_resolver 指向走代理的解析器（会形成解析环路）: {ddr['server']}")
        else:
            ok(f"default_domain_resolver = {ddr['server']}（直连）")

    # --- 模式档位
    api = cfg["experimental"]["clash_api"]
    modes = [r["clash_mode"] for r in cfg["route"]["rules"] if "clash_mode" in r]
    if api["default_mode"] in modes:
        bad(f"default_mode({api['default_mode']}) 不应同时出现在 clash_mode 规则里（默认档不需要规则）")
    else:
        ok(f"模式档位: [{api['default_mode']}] + {modes}")

    if mode == "tun":
        if not tun:
            bad("tun 配置里没有 tun 入站")
        else:
            if tun.get("strict_route") is False:
                ok("strict_route = false（否则 Windows 会装 WFP 过滤器，卡死 WSL）")
            else:
                bad(f"strict_route 不是 false: {tun.get('strict_route')}")
            miss = [c for c in PRIVATE_CIDRS if c not in tun.get("route_exclude_address", [])]
            if miss:
                bad(f"route_exclude_address 缺: {miss}")
            else:
                ok("私网段排除齐全（含 172.16.0.0/12，保 WSL/Hyper-V）")
            if tun.get("dns_mode") == "hijack":
                ok("dns_mode = hijack（系统解析器指向 sing-box，防本地 DNS 污染）")
            else:
                bad(f"dns_mode = {tun.get('dns_mode')}，应为 hijack")
            if any(r.get("action") == "hijack-dns" for r in cfg["route"]["rules"]):
                ok("DNS 劫持规则存在")
            else:
                bad("TUN 模式缺少 hijack-dns 规则")
    else:
        # I11：proxy 模式不得有【接管流量】的 tun；允许一个空载 tun（auto_route=false）
        # 专用于承载 platform.http_proxy —— 官方客户端据此把系统代理写进登录用户的 hive。
        if tun is None:
            ok("无 tun 入站（符合预期）")
        else:
            http_proxy = (tun.get("platform") or {}).get("http_proxy") or {}
            if tun.get("auto_route") is not False:
                bad("proxy 配置里的 tun 入站会接管流量（auto_route 应为 false）")
            elif not http_proxy.get("enabled"):
                bad("空载 tun 缺少 platform.http_proxy.enabled（那就没有存在意义）")
            elif not mixed or http_proxy.get("server_port") != mixed.get("listen_port"):
                bad(f"空载 tun 的 platform.http_proxy 端口({http_proxy.get('server_port')})"
                    f" 与 mixed({mixed and mixed.get('listen_port')}) 不一致")
            else:
                ok(f"空载 tun 仅承载 platform.http_proxy（auto_route=false → "
                   f"{http_proxy.get('server')}:{http_proxy.get('server_port')}）")
        if any(r.get("action") == "hijack-dns" for r in cfg["route"]["rules"]):
            bad("proxy 配置里不应有 hijack-dns 规则")
        else:
            ok("无 hijack-dns 规则（符合预期）")
        if mixed and mixed.get("set_system_proxy"):
            ok("mixed 入站已开 set_system_proxy（起核心自动设、停核心自动清）")
        else:
            bad("proxy 配置的 mixed 入站缺少 set_system_proxy")

    return cfg


def check_parity(tun_cfg, proxy_cfg):
    section("两份配置的一致性")
    if not tun_cfg or not proxy_cfg:
        bad("无法比对（有一份缺失）")
        return
    for key in ("log", "experimental", "http_clients", "outbounds", "dns"):
        if tun_cfg[key] != proxy_cfg[key]:
            bad(f"{key} 两份不一致（应只差 inbounds 与 TUN 专属规则）")
            return
    a = {k: v for k, v in tun_cfg["route"].items() if k != "rules"}
    b = {k: v for k, v in proxy_cfg["route"].items() if k != "rules"}
    if a != b:
        bad("route 段（除 rules）两份不一致")
        return
    ok("除 inbounds 与 TUN 专属规则外完全一致")


def check_bootstrap(manifest):
    section("rule-set 冷启动快照")
    if not manifest:
        bad("没有 bootstrap_manifest.json")
        return
    missing = [t for t, m in manifest.items()
               if not os.path.exists(os.path.join(ROOT, m["initial_path"]))]
    if missing:
        bad(f"缺少快照 {len(missing)} 个（跑 python tools/fetch_bootstrap.py）: {missing[:5]}")
    else:
        ok(f"{len(manifest)} 个快照齐备（冷启动不依赖网络）")


def check_subscription():
    section("订阅快照")
    files = sorted(glob.glob(os.path.join(ROOT, "sub", "raw-*.yaml")))
    if not files:
        bad("sub/ 下没有 raw-*.yaml")
        return
    ok(f"最新快照: {os.path.basename(files[-1])}（共 {len(files)} 份）")


def main():
    print("singbox-config 不变量校验")
    decls = check_declarations()
    check_layers(decls)
    check_subscription()

    tun = check_config(os.path.join(OUT, "config.tun.json"), "tun", decls)
    proxy = check_config(os.path.join(OUT, "config.proxy.json"), "proxy", decls)
    check_parity(tun, proxy)

    mp = os.path.join(OUT, "bootstrap_manifest.json")
    check_bootstrap(load(mp) if os.path.exists(mp) else None)

    print(f"\n通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    if FAILED:
        print("\n失败明细:")
        for f in FAILED:
            print(f"  - {f}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
