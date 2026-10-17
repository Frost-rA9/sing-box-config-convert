"""Loader: three carriers become one node list."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from sbconv import LoadError, load

FIXTURES = Path(__file__).parent / "fixtures"
EXPECTED_NAMES = ["Traffic: 1.00 GB | 100 GB", "🇭🇰 香港 1", "🇯🇵 日本 1"]


def test_clash_yaml():
    result = load(str(FIXTURES / "sample-clash.yaml"))
    assert result.carrier == "file:clash"
    assert [n.name for n in result.nodes] == EXPECTED_NAMES
    assert [n.kind for n in result.nodes] == ["ss", "ss", "vmess"]
    assert result.nodes[0].clash["server"] == "192.0.2.10"
    assert result.nodes[0].uri is None


@pytest.mark.parametrize("fixture", ["sample-uris.txt", "sample-uris.b64"])
def test_uri_carriers(fixture):
    result = load(str(FIXTURES / fixture))
    assert result.carrier.endswith(":uri")
    assert [n.name for n in result.nodes] == EXPECTED_NAMES
    assert [n.kind for n in result.nodes] == ["ss", "ss", "vmess"]
    assert result.nodes[0].clash is None
    assert result.nodes[0].uri.startswith("ss://")


def test_carriers_agree():
    """Three carriers of the same subscription must give the same node list."""
    names = {
        fixture: [n.name for n in load(str(FIXTURES / fixture)).nodes]
        for fixture in ("sample-clash.yaml", "sample-uris.txt", "sample-uris.b64")
    }
    assert set(map(tuple, names.values())) == {tuple(EXPECTED_NAMES)}


def test_unsupported_scheme_skipped(tmp_path: Path):
    f = tmp_path / "mixed.txt"
    f.write_text(
        "\n".join(
            [
                "ssr://abcdef",
                "ss://" + "YWVzLTEyOC1nY206cHc=" + "@192.0.2.10:8388#香港 1",
            ]
        ),
        encoding="utf-8",
    )
    result = load(str(f))
    assert [n.name for n in result.nodes] == ["香港 1"]
    assert result.skipped[0][1] == "不支持的协议 ssr"


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


@pytest.mark.skipif(
    not (os.environ.get("SBCONV_REAL_CLASH") and os.environ.get("SBCONV_REAL_URI")),
    reason="需要 SBCONV_REAL_CLASH 与 SBCONV_REAL_URI 分别指向真实订阅的两种形态",
)
def test_real_subscription_two_carriers():
    clash = load(os.environ["SBCONV_REAL_CLASH"])
    uris = load(os.environ["SBCONV_REAL_URI"])
    assert clash.nodes and len(clash.nodes) == len(uris.nodes)
    assert {n.name for n in clash.nodes} == {n.name for n in uris.nodes}
