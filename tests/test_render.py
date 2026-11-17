"""Renderer and static checks."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from sbconv import load, load_artifact
from sbconv.models import Node
from sbconv.artifacts import Artifact, GroupSpec
from sbconv.check import check
from sbconv.clean import clean
from sbconv.render import RenderError, build_node_outbound, proxy_mode, render, resolve_groups, tun_only

FIXTURES = Path(__file__).parent / "fixtures"
ARTIFACT = Path(__file__).parents[1] / "artifacts" / "base.yaml"


def _artifact() -> Artifact:
    return load_artifact(str(ARTIFACT))


def _ss_nodes() -> list[Node]:
    """Three ss nodes with real shapes. All of them render."""
    def node(name: str, server: str, port: int) -> Node:
        return Node(
            name=name,
            kind="ss",
            clash={
                "server": server,
                "port": port,
                "cipher": "aes-128-gcm",
                "password": "pw",
                "plugin": "obfs",
                "plugin-opts": {"mode": "http", "host": "example.com"},
            },
        )

    return [
        node("🇭🇰 香港 1", "192.0.2.11", 10016),
        node("🇯🇵 日本 1", "192.0.2.12", 10026),
        node("🇺🇸 美国 1", "192.0.2.13", 10036),
    ]


def _mini_artifact() -> Artifact:
    """Keep only the regions that the sample data covers."""
    doc = {
        "meta": {"name": "t", "singbox_version": "1.14"},
        "nodes": {
            "drop_name_keywords": ["Traffic", "Expire"],
            "plugin_alias": {"simple-obfs": "obfs-local", "obfs": "obfs-local"},
            "region_keys": [{"type": "chinese_name"}, {"type": "flag_emoji"}, {"type": "iso_code"}],
            "region_overrides": [{"match_name": "台湾", "region": "TW"}],
        },
        "groups": [
            {"tag": "Auto", "type": "urltest", "regions": ["*"]},
            {"tag": "HK", "type": "selector", "regions": ["HK"]},
            {"tag": "其它", "type": "selector", "regions": ["*"], "exclude_regions": ["HK"]},
            {"tag": "Proxy", "type": "selector", "groups": ["Auto", "HK", "其它"], "default": "Auto"},
            {"tag": "Final", "type": "selector", "groups": ["Proxy", "HK", "其它"], "default": "Proxy"},
        ],
        "dns": {
            "servers": [
                {"tag": "local", "type": "https", "server": "223.5.5.5"},
                {"tag": "fakeip", "type": "fakeip", "inet4_range": "198.18.0.0/15"},
            ],
            "rules": [
                {"rule_set": "geosite-private", "server": "local"},
                {"query_type": ["A", "AAAA"], "server": "fakeip"},
            ],
            "final": "local",
        },
        "http_clients": [{"tag": "download", "detour": "Direct"}],
        "rule_sets": [{"tag": "geosite-private", "url": "https://example.invalid/a.srs"}],
        "route": {
            "default_domain_resolver": "local",
            "default_http_client": "download",
            "rules": [{"action": "sniff"}, {"rule_set": "geosite-private", "outbound": "Direct"}],
            "final": "Final",
        },
        "inbounds": [{"type": "mixed", "tag": "in", "listen": "127.0.0.1", "listen_port": 1080}],
    }
    return Artifact.model_validate(doc)


def test_render_happy_path():
    artifact = _mini_artifact()
    kept, report = clean(_ss_nodes(), artifact.nodes)
    config, warnings = render(artifact, kept, report)

    assert warnings == []
    errors, warns = check(config)
    assert errors == []

    tags = [o["tag"] for o in config["outbounds"]]
    assert tags[0] == "🇭🇰 香港 1"

    other = next(o for o in config["outbounds"] if o["tag"] == "其它")
    assert other["outbounds"] == ["🇯🇵 日本 1", "🇺🇸 美国 1"], "No HK node may remain after the region exclusion"
    assert "Direct" in tags

    proxy = next(o for o in config["outbounds"] if o["tag"] == "Proxy")
    assert proxy["outbounds"] == ["Auto", "HK", "其它"], "A reference to a group stays a tag and never expands into nodes"
    assert proxy["default"] == "Auto"

    direct = next(o for o in config["outbounds"] if o["tag"] == "Direct")
    assert direct["domain_resolver"] == {"server": "local"}, "The direct outbound must not be empty, or the rule-set fetch stops with FATAL"

    assert config["route"]["default_domain_resolver"] == {"server": "local"}
    assert config["http_clients"] == [{"tag": "download", "detour": "Direct"}]
    assert "experimental" in config


def test_leftover_nodes_listed_directly():
    artifact = _artifact()
    kept, report = clean(_ss_nodes(), artifact.nodes)
    config, _ = render(artifact, kept, report)
    proxy = next(o for o in config["outbounds"] if o["tag"] == "Proxy")
    # All three nodes are HK, JP, or US and every region group collects them, so
    # Proxy lists group tags only.
    assert proxy["outbounds"][:6] == ["Auto", "HK", "JP", "SG", "TW", "US"]
    assert proxy["outbounds"][6:] == []


def test_ss_plugin_from_clash():
    artifact = _mini_artifact()
    nodes = load(str(FIXTURES / "sample-clash.yaml")).nodes
    kept, report = clean(nodes, artifact.nodes)
    config, warnings = render(artifact, kept, report)
    assert warnings == []
    hk = next(o for o in config["outbounds"] if o["tag"] == "🇭🇰 香港 1")
    assert hk["plugin"] == "obfs-local"
    assert hk["plugin_opts"] == "obfs=http;obfs-host=example.com"
    assert hk["method"] == "aes-128-gcm"


def test_vmess_from_clash():
    artifact = _mini_artifact()
    nodes = load(str(FIXTURES / "sample-clash.yaml")).nodes
    kept, report = clean(nodes, artifact.nodes)
    config, warnings = render(artifact, kept, report)
    assert warnings == []
    jp = next(o for o in config["outbounds"] if o["tag"] == "🇯🇵 日本 1")
    assert jp == {
        "type": "vmess",
        "tag": "🇯🇵 日本 1",
        "server": "192.0.2.12",
        "server_port": 443,
        "uuid": "00000000-0000-0000-0000-000000000000",
        "security": "auto",
        "alter_id": 0,
        "tls": {"enabled": True},
    }


def test_vmess_websocket_transport_and_tls():
    """A ws node with its own Host header: the header belongs to the transport, not to TLS."""
    node = Node(
        name="🇭🇰 香港 9",
        kind="vmess",
        clash={
            "server": "n.example.com",
            "port": 13487,
            "uuid": "01315cdf-d8be-370a-9bdf-a4a3259cd61b",
            "cipher": "auto",
            "alterId": 1,
            "tls": True,
            "servername": "speedtest.example.net",
            "skip-cert-verify": True,
            "network": "ws",
            "ws-opts": {"path": "/tutorial/x.m3u8", "headers": {"Host": "speedtest.example.net"}},
            "udp": True,
        },
    )
    out = build_node_outbound(node)
    assert out["alter_id"] == 1
    assert out["tls"] == {"enabled": True, "server_name": "speedtest.example.net", "insecure": True}
    assert out["transport"] == {"type": "ws", "path": "/tutorial/x.m3u8", "headers": {"Host": "speedtest.example.net"}}
    assert "udp" not in out


def test_vmess_writes_no_transport_for_tcp():
    node = Node(name="x", kind="vmess", clash={"server": "a.example.com", "port": 1, "uuid": "u", "network": "tcp"})
    out = build_node_outbound(node)
    assert "transport" not in out and "tls" not in out


def test_vmess_unsupported_network_is_named():
    """An unknown network must fail loudly, because a transport-less vmess node would dial the wrong way."""
    node = Node(name="x", kind="vmess", clash={"server": "a.example.com", "port": 1, "uuid": "u", "network": "grpc"})
    with pytest.raises(RenderError, match="network 'grpc'"):
        build_node_outbound(node)


def test_vmess_without_uuid_is_skipped():
    artifact = _mini_artifact()
    nodes = load(str(FIXTURES / "sample-clash.yaml")).nodes
    kept, report = clean(nodes, artifact.nodes)
    kept[-1].clash = {k: v for k, v in kept[-1].clash.items() if k != "uuid"}
    config, warnings = render(artifact, kept, report)
    assert warnings == ["节点 '🇯🇵 日本 1' 缺少 uuid"]
    assert "🇯🇵 日本 1" not in [o["tag"] for o in config["outbounds"]]


def test_trojan_from_clash():
    """A trojan node writes no `tls` flag, so the builder must add the block itself."""
    node = Node(
        name="🇭🇰 香港 1",
        kind="trojan",
        clash={
            "server": "hk.example.net",
            "port": 10115,
            "password": "pw",
            "sni": "m.ctrip.com",
            "skip-cert-verify": True,
            "udp": True,
        },
    )
    assert build_node_outbound(node) == {
        "type": "trojan",
        "tag": "🇭🇰 香港 1",
        "server": "hk.example.net",
        "server_port": 10115,
        "password": "pw",
        "tls": {"enabled": True, "server_name": "m.ctrip.com", "insecure": True},
    }


def test_trojan_without_tls_hints_keeps_the_block():
    """TLS is the protocol, not an option: no sni and no skip-cert-verify still need the block."""
    node = Node(name="x", kind="trojan", clash={"server": "a.example.com", "port": 1, "password": "pw"})
    out = build_node_outbound(node)
    assert out["tls"] == {"enabled": True}
    assert "network" not in out, "Both networks are on by default"


def test_trojan_udp_off_writes_only_tcp():
    node = Node(
        name="x",
        kind="trojan",
        clash={"server": "a.example.com", "port": 1, "password": "pw", "udp": False},
    )
    assert build_node_outbound(node)["network"] == ["tcp"]


def test_trojan_websocket_transport():
    node = Node(
        name="🇸🇬 新加坡 1",
        kind="trojan",
        clash={
            "server": "sg.example.net",
            "port": 443,
            "password": "pw",
            "sni": "sg.example.net",
            "network": "ws",
            "ws-opts": {"path": "/ws"},
        },
    )
    assert build_node_outbound(node)["transport"] == {"type": "ws", "path": "/ws"}


def test_trojan_nodes_reach_the_groups():
    """Registered under `trojan`, a trojan node is not dropped as an unknown protocol."""
    artifact = _mini_artifact()
    nodes = [
        Node(name="🇭🇰 香港 1", kind="trojan", clash={"server": "hk.example.net", "port": 1, "password": "pw"}),
        Node(name="🇯🇵 日本 1", kind="trojan", clash={"server": "jp.example.net", "port": 2, "password": "pw"}),
    ]
    kept, report = clean(nodes, artifact.nodes)
    config, warnings = render(artifact, kept, report)
    assert warnings == []
    assert [o["tag"] for o in config["outbounds"][:2]] == ["🇭🇰 香港 1", "🇯🇵 日本 1"]
    errors, _ = check(config)
    assert errors == []


def test_unsupported_protocol_is_skipped_with_warning():
    artifact = _mini_artifact()
    nodes = load(str(FIXTURES / "sample-clash.yaml")).nodes
    kept, report = clean(nodes, artifact.nodes)
    kept.append(copy.deepcopy(kept[0]))
    kept[-1].name, kept[-1].kind = "vless-node", "vless"
    kept[-1].clash = {"server": "x", "port": 1}
    config, warnings = render(artifact, kept, report)
    assert any("暂不支持协议 vless" in w for w in warnings)
    assert "vless-node" not in [o["tag"] for o in config["outbounds"]]


def test_output_key_order_matches_the_schema():
    """The schema declares an order. Matching it keeps the output readable next to the docs."""
    artifact = _mini_artifact()
    kept, report = clean(_ss_nodes(), artifact.nodes)
    config, _ = render(artifact, kept, report)

    assert list(config) == ["log", "dns", "http_clients", "inbounds", "outbounds", "route", "experimental"]
    assert list(config["route"]) == [
        "rules",
        "rule_set",
        "final",
        "auto_detect_interface",
        "default_domain_resolver",
        "default_http_client",
    ]
    assert list(config["dns"]) == ["servers", "rules", "final", "strategy"]


def test_custom_group_may_offer_direct():
    """A custom group can list Direct, so a user can switch that app off the proxy."""
    artifact = _mini_artifact()
    artifact.groups.append(GroupSpec(tag="Apple", type="selector", groups=["Proxy", "Direct"], regions=["*"]))
    kept, report = clean(_ss_nodes(), artifact.nodes)
    config, _ = render(artifact, kept, report)

    apple = next(o for o in config["outbounds"] if o["tag"] == "Apple")
    assert apple["outbounds"][:2] == ["Proxy", "Direct"]
    errors, _ = check(config)
    assert errors == []


def test_group_reference_must_exist():
    artifact = _mini_artifact()
    artifact.groups[1].groups = ["HK", "Direct"]
    kept, report = clean(_ss_nodes(), artifact.nodes)
    with pytest.raises(RenderError, match="未声明在前"):
        render(artifact, kept, report)


def test_tun_only_drops_the_listener():
    """An Android client creates the TUN itself and cannot set a system proxy."""
    artifact = _artifact()
    kept = tun_only(artifact.inbounds)
    assert [i["type"] for i in kept] == ["tun"]
    assert kept[0]["auto_route"] is True


def test_proxy_mode_keeps_a_tun_carrier():
    """A client that runs the core as a service needs the TUN platform path for the system proxy."""
    artifact = _artifact()
    kept = proxy_mode(artifact.inbounds)

    tun = next(i for i in kept if i["type"] == "tun")
    assert tun["auto_route"] is False and tun["strict_route"] is False
    assert tun["dns_mode"] == "disabled"
    assert tun["platform"]["http_proxy"] == {"enabled": True, "server": "127.0.0.1", "server_port": 7890}

    mixed = next(i for i in kept if i["type"] == "mixed")
    assert mixed["listen_port"] == 7890
    assert "set_system_proxy" not in mixed, "裸的 set_system_proxy 会写到服务账户的注册表"


def test_proxy_mode_needs_a_listener():
    with pytest.raises(RenderError, match="listen_port"):
        proxy_mode([{"type": "tun", "address": ["172.19.0.1/30"]}])


def test_group_referencing_later_group_is_error():
    artifact = _mini_artifact()
    bad = artifact.model_copy(deep=True)
    bad.groups[0].groups = ["Proxy"]
    kept, report = clean(_ss_nodes(), artifact.nodes)
    with pytest.raises(RenderError, match="未声明在前"):
        render(bad, kept, report)


def test_empty_group_is_error():
    artifact = _artifact()
    nodes = load(str(FIXTURES / "sample-clash.yaml")).nodes
    kept, report = clean(nodes, artifact.nodes)
    config, _ = render(artifact, kept, report)
    errors, _ = check(config)
    assert any("为空" in e for e in errors)


def test_check_rejects_default_outside_members():
    """`default` must be a member of its own group. The core looks for it in that group only, and a missing member is a FATAL error."""
    artifact = _artifact()
    kept, report = clean(_ss_nodes(), artifact.nodes)
    config, _ = render(artifact, kept, report)
    proxy = next(o for o in config["outbounds"] if o["tag"] == "Proxy")
    proxy["outbounds"] = [m for m in proxy["outbounds"] if m != proxy["default"]]
    errors, _ = check(config)
    assert any("不是本分组的成员" in e for e in errors)


def test_check_catches_dangling_references():
    artifact = _mini_artifact()
    kept, report = clean(_ss_nodes(), artifact.nodes)
    config, _ = render(artifact, kept, report)

    bad = copy.deepcopy(config)
    bad["outbounds"].append({"type": "selector", "tag": "X", "outbounds": ["不存在"]})
    errors, _ = check(bad)
    assert any("不存在的 tag" in e for e in errors)

    bad = copy.deepcopy(config)
    bad["route"]["rules"].append({"rule_set": "没有这个规则集", "action": "direct"})
    errors, _ = check(bad)
    assert any("未定义的 rule_set" in e for e in errors)

    bad = copy.deepcopy(config)
    bad["route"]["final"] = "不存在的组"
    errors, _ = check(bad)
    assert any("route.final" in e for e in errors)

    bad = copy.deepcopy(config)
    bad["route"]["default_http_client"] = None
    _, warns = check(bad)
    assert any("default_http_client" in w for w in warns)

    bad = copy.deepcopy(config)
    bad["route"]["rules"] = [r for r in bad["route"]["rules"] if "rule_set" not in r]
    bad["dns"]["rules"] = [r for r in bad["dns"]["rules"] if "rule_set" not in r]
    _, warns = check(bad)
    assert any("没有被任何规则引用" in w for w in warns)

    bad = copy.deepcopy(config)
    bad["route"]["default_domain_resolver"] = {"server": "没这个"}
    errors, _ = check(bad)
    assert any("default_domain_resolver" in e for e in errors)

    bad = copy.deepcopy(config)
    bad["http_clients"] = [{"tag": "download", "detour": "没有这个出站"}]
    errors, _ = check(bad)
    assert any("不是已定义的出站" in e for e in errors)
