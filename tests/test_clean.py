"""Cleaning: removal, deduplication, rename, plugin alias, and region detection."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from sbconv import load
from sbconv import load_artifact
from sbconv.artifacts import NodeRules
from sbconv.clean import clean, detect_region, normalize_plugin, raw_plugin
from sbconv.models import Node

FIXTURES = Path(__file__).parent / "fixtures"

RULES = NodeRules(
    drop_name_keywords=["Traffic", "Expire"],
    drop_duplicate_by=["server", "server_port"],
    plugin_alias={"simple-obfs": "obfs-local", "obfs": "obfs-local"},
    region_keys=[
        {"type": "chinese_name"},
        {"type": "flag_emoji"},
        {"type": "iso_code"},
    ],
    region_overrides=[{"match_name": "台湾", "region": "TW"}],
    fallback_region="其他地区",
)


def test_drops_info_node():
    nodes = load(str(FIXTURES / "sample-clash.yaml")).nodes
    kept, report = clean(nodes, RULES)
    assert [n.name for n in kept] == ["🇭🇰 香港 1", "🇯🇵 日本 1"]
    assert report.dropped == [("Traffic: 1.00 GB | 100 GB", "名字含关键字 traffic")]


def test_name_filter_runs_before_dedup():
    """An information node and a real node share a server:port. The real node must survive."""
    info = Node(name="Expire: 2099-01-01", kind="ss", origin="clash", clash={"server": "a.example.com", "port": 10015})
    real = Node(name="🇭🇰 香港 1", kind="ss", origin="clash", clash={"server": "a.example.com", "port": 10015})
    kept, report = clean([info, real], RULES)
    assert [n.name for n in kept] == ["🇭🇰 香港 1"]
    assert report.dropped[0][0] == "Expire: 2099-01-01"


def test_dedup_keeps_first_by_server_port():
    a = Node(name="🇭🇰 同名线路 A", kind="ss", origin="clash", clash={"server": "b.example.com", "port": 443})
    b = Node(name="🇭🇰 同名线路 B", kind="ss", origin="clash", clash={"server": "b.example.com", "port": 443})
    kept, report = clean([a, b], RULES)
    assert [n.name for n in kept] == ["🇭🇰 同名线路 A"]
    assert "server+server_port" in report.dropped[0][1]


def test_renames_duplicate_names():
    a = Node(name="🇭🇰 香港 1", kind="ss", origin="clash", clash={"server": "c1.example.com", "port": 1})
    b = Node(name="🇭🇰 香港 1", kind="ss", origin="clash", clash={"server": "c2.example.com", "port": 2})
    kept, report = clean([a, b], RULES)
    assert [n.name for n in kept] == ["🇭🇰 香港 1", "🇭🇰 香港 1 2"]
    assert report.renamed == [("🇭🇰 香港 1", "🇭🇰 香港 1 2")]


def test_plugin_alias_clash_carrier():
    nodes = load(str(FIXTURES / "sample-clash.yaml")).nodes
    kept, report = clean(nodes, RULES)
    assert kept[0].plugin == "obfs-local"
    assert report.plugin_changed == [("🇭🇰 香港 1", "obfs", "obfs-local")]


def test_plugin_alias_uri_carrier():
    nodes = load(str(FIXTURES / "sample-uris.b64")).nodes
    assert raw_plugin(nodes[1]) == "simple-obfs"
    kept, report = clean(nodes, RULES)
    assert [n.plugin for n in kept] == ["obfs-local", None]
    assert report.plugin_changed == [("🇭🇰 香港 1", "simple-obfs", "obfs-local")]


def test_normalize_plugin():
    alias = {"simple-obfs": "obfs-local", "obfs": "obfs-local"}
    assert normalize_plugin("simple-obfs", alias) == "obfs-local"
    assert normalize_plugin("Simple-Obfs", alias) == "obfs-local"
    assert normalize_plugin("v2ray-plugin", alias) == "v2ray-plugin"
    assert normalize_plugin(None, alias) is None


def test_raw_plugin_from_uri():
    node = Node(name="x", kind="ss", origin="uri", uri="ss://YWJj@1.2.3.4:8388?plugin=simple-obfs%3Bobfs%3Dhttp#x")
    assert raw_plugin(node) == "simple-obfs"


def test_region_detection_order():
    assert detect_region("🇨🇳 台湾 01", RULES) == ("override:台湾", "TW")
    assert detect_region("🇭🇰 香港 1", RULES) == ("chinese_name:香港", "HK")
    assert detect_region("US 01", RULES) == ("iso_code:US", "US")
    assert detect_region("节点 01", RULES) is None


def test_unknown_falls_back():
    nodes = [Node(name="专线 01", kind="ss", origin="clash", clash={"server": "d.example.com", "port": 1})]
    kept, report = clean(nodes, RULES)
    assert kept[0].region == "其他地区"
    assert report.unknown == ["专线 01"]
    assert report.distribution == {"其他地区": 1}


def test_hosts_rewrites_the_server():
    """The hosts table of the carrier rewrites a node address, and the report records it."""
    nodes = load(str(FIXTURES / "sample-hosts.yaml")).nodes
    kept, report = clean(nodes, RULES, {"node-1.example.com": "node-1.example.net"})
    hk = next(n for n in kept if n.name == "🇭🇰 香港 1")
    assert hk.clash["server"] == "node-1.example.net"
    assert report.hosts_changed == [("🇭🇰 香港 1", "node-1.example.com", "node-1.example.net")]
    others = [n.clash["server"] for n in kept if n.name != "🇭🇰 香港 1"]
    assert "node-2.example.com" in others and "node-3.example.com" in others


def test_hosts_can_point_at_an_ip():
    nodes = load(str(FIXTURES / "sample-hosts.yaml")).nodes
    kept, report = clean(nodes, RULES, {"node-2.example.com": "192.0.2.20"})
    jp = next(n for n in kept if n.name == "🇯🇵 日本 1")
    assert jp.clash["server"] == "192.0.2.20"
    assert len(report.hosts_changed) == 1


def test_hosts_rewrite_runs_before_dedup():
    """Two names for one endpoint collapse into one node."""
    a = Node(name="🇭🇰 香港 1", kind="ss", origin="clash", clash={"server": "node-1.example.com", "port": 443})
    b = Node(name="🇭🇰 香港 2", kind="ss", origin="clash", clash={"server": "node-1.example.net", "port": 443})
    kept, report = clean([a, b], RULES, {"node-1.example.com": "node-1.example.net"})
    assert [n.name for n in kept] == ["🇭🇰 香港 1"]
    assert report.dropped[0][0] == "🇭🇰 香港 2"


def test_no_hosts_table_keeps_the_addresses():
    nodes = load(str(FIXTURES / "sample-clash.yaml")).nodes
    before = {n.name: n.clash["server"] for n in nodes}
    kept, report = clean(nodes, RULES, {})
    assert all(n.clash["server"] == before[n.name] for n in kept)
    assert report.hosts_changed == []


def test_distribution():
    nodes = load(str(FIXTURES / "sample-clash.yaml")).nodes
    _, report = clean(nodes, RULES)
    assert report.distribution == {"HK": 1, "JP": 1}


@pytest.mark.skipif(
    not os.environ.get("SBCONV_REAL_CLASH"),
    reason="需要 SBCONV_REAL_CLASH 指向真实订阅的 YAML 形态",
)
def test_real_subscription_cleaning():
    """Run the cleaning on the real subscription, and assert structure only.

    Fixed node counts, region distributions, and dropped node names are account
    features. Put a baseline in an environment variable or a local file instead.
    """
    artifact = load_artifact(str(Path(__file__).parents[1] / "artifacts" / "base.yaml"))
    nodes = load(os.environ["SBCONV_REAL_CLASH"]).nodes
    kept, report = clean(nodes, artifact.nodes)

    assert kept, "清洗后不该一个节点都不剩"
    assert len(kept) <= len(nodes)
    assert len(report.dropped) == len(nodes) - len(kept)
    assert report.renamed == []
    assert {n.plugin for n in kept} <= {"obfs-local"}, "插件名必须全部归一，不能出现 simple-obfs"
    assert all(n.region for n in kept), "每个节点都要有地区码"
    assert len(report.distribution) <= len(kept)
