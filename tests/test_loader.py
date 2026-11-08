"""Loader: one Clash YAML file becomes one node list."""

from __future__ import annotations

from pathlib import Path

import pytest

from sbconv import LoadError, load

FIXTURES = Path(__file__).parent / "fixtures"
EXPECTED_NAMES = ["Traffic: 1.00 GB | 100 GB", "🇭🇰 香港 1", "🇯🇵 日本 1"]


def test_clash_yaml():
    result = load(str(FIXTURES / "sample-clash.yaml"))
    assert [n.name for n in result.nodes] == EXPECTED_NAMES
    assert [n.kind for n in result.nodes] == ["ss", "ss", "vmess"]
    assert result.nodes[0].clash["server"] == "192.0.2.10"


def test_hosts_table_is_read():
    """A Clash file can declare a hosts table. The loader reads it and keeps the nodes raw."""
    result = load(str(FIXTURES / "sample-hosts.yaml"))
    assert result.hosts == {"node-1.example.com": "node-1.example.net", "node-2.example.com": "192.0.2.20"}
    assert [n.clash["server"] for n in result.nodes] == [
        "node-1.example.com",
        "node-2.example.com",
        "node-3.example.com",
    ]


def test_file_without_hosts_table():
    assert load(str(FIXTURES / "sample-clash.yaml")).hosts == {}


def test_subscription_url_is_rejected():
    """The file is the only input. A URL fails, so the caller fetches it and passes a path."""
    with pytest.raises(LoadError, match="不抓取订阅链接"):
        load("https://example.com/link/abc?clash=3")


def test_link_list_is_rejected(tmp_path: Path):
    f = tmp_path / "links.txt"
    f.write_text("ss://YWVzLTEyOC1nY206cHc=@192.0.2.10:8388#香港 1\n", encoding="utf-8")
    with pytest.raises(LoadError, match="缺少 proxies 段"):
        load(str(f))


def test_clash_node_without_name(tmp_path: Path):
    f = tmp_path / "bad.yaml"
    f.write_text("proxies:\n  - {type: ss, server: 192.0.2.10, port: 8388}\n", encoding="utf-8")
    with pytest.raises(LoadError, match="缺少 name"):
        load(str(f))


def test_clash_node_without_type(tmp_path: Path):
    f = tmp_path / "bad.yaml"
    f.write_text("proxies:\n  - {name: 香港 1, server: 192.0.2.10, port: 8388}\n", encoding="utf-8")
    with pytest.raises(LoadError, match="缺少 type"):
        load(str(f))


def test_empty_proxies(tmp_path: Path):
    f = tmp_path / "empty.yaml"
    f.write_text("proxies: []\n", encoding="utf-8")
    with pytest.raises(LoadError, match="没有 proxies"):
        load(str(f))


def test_missing_file():
    with pytest.raises(LoadError, match="文件不存在"):
        load(str(FIXTURES / "does-not-exist.yaml"))
