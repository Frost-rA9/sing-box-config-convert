"""Renderer and static checks."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from sbconv import load, load_artifact
from sbconv.models import Node
from sbconv.artifacts import Artifact
from sbconv.check import check
from sbconv.clean import clean
from sbconv.render import RenderError, render, resolve_groups

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
            origin="clash",
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
        "http_clients": [{"tag": "download", "detour": "direct"}],
        "rule_sets": [{"tag": "geosite-private", "url": "https://example.invalid/a.srs"}],
        "route": {
            "default_domain_resolver": "local",
            "default_http_client": "download",
            "rules": [{"action": "sniff"}, {"rule_set": "geosite-private", "outbound": "direct"}],
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
    assert "direct" in tags

    proxy = next(o for o in config["outbounds"] if o["tag"] == "Proxy")
    assert proxy["outbounds"] == ["Auto", "HK", "其它"], "A reference to a group stays a tag and never expands into nodes"
    assert proxy["default"] == "Auto"

    direct = next(o for o in config["outbounds"] if o["tag"] == "direct")
    assert direct["domain_resolver"] == {"server": "local"}, "The direct outbound must not be empty, or the rule-set fetch stops with FATAL"

    assert config["route"]["default_domain_resolver"] == {"server": "local"}
    assert config["http_clients"] == [{"tag": "download", "detour": "direct"}]
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


def test_ss_plugin_from_both_carriers():
    artifact = _mini_artifact()
    for fixture in ("sample-clash.yaml", "sample-uris.b64"):
        nodes = load(str(FIXTURES / fixture)).nodes
        kept, report = clean(nodes, artifact.nodes)
        config, warnings = render(artifact, kept, report)
        assert warnings == ["暂不支持协议 vmess（节点 '🇯🇵 日本 1'）"]
        hk = next(o for o in config["outbounds"] if o["tag"] == "🇭🇰 香港 1")
        assert hk["plugin"] == "obfs-local"
        assert hk["plugin_opts"] == "obfs=http;obfs-host=example.com"
        assert hk["method"] == "aes-128-gcm"


def test_unsupported_protocol_is_skipped_with_warning():
    artifact = _mini_artifact()
    nodes = load(str(FIXTURES / "sample-clash.yaml")).nodes
    kept, report = clean(nodes, artifact.nodes)
    kept.append(copy.deepcopy(kept[0]))
    kept[-1].name, kept[-1].kind = "vmess-node", "vmess"
    kept[-1].clash, kept[-1].origin = {"server": "x", "port": 1}, "clash"
    config, warnings = render(artifact, kept, report)
    assert any("暂不支持协议 vmess" in w for w in warnings)
    assert "vmess-node" not in [o["tag"] for o in config["outbounds"]]


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
