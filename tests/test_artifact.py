"""Artifact schema: the default artifact loads, and a missing field or a bad value gives a clear error."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from sbconv import load_artifact
from sbconv.artifacts import Artifact

DEFAULT = Path(__file__).parents[1] / "artifacts" / "base.yaml"


def _rule_set_refs(rule: dict) -> list[str]:
    value = rule.get("rule_set")
    if value is None:
        return []
    return [value] if isinstance(value, str) else list(value)


def _minimal() -> dict:
    return {
        "meta": {"name": "t", "singbox_version": "1.14"},
        "groups": [{"tag": "节点选择", "type": "selector"}],
        "dns": {"servers": [{"tag": "local", "type": "https", "server": "223.5.5.5"}], "final": "local"},
        "route": {"default_domain_resolver": "local"},
    }


def test_default_artifact_loads():
    a = load_artifact(str(DEFAULT))
    assert a.meta.singbox_version == "1.14"
    assert a.route.final == "Final"
    assert a.route.default_domain_resolver == "local"
    assert [g.tag for g in a.groups] == ["Auto", "HK", "JP", "SG", "TW", "US", "Proxy", "Final"]
    tags = {r.tag for r in a.rule_sets}
    assert tags == {
        "geosite-category-ads-all",
        "geosite-falungong",
        "geosite-private",
        "geosite-category-ntp",
        "geosite-nintendo",
        "geosite-playstation",
        "geosite-xbox",
        "geosite-apple-update",
        "geosite-geolocation-cn",
        "geoip-cn",
    }
    referenced = {tag for rule in a.route.rules + a.dns.rules for tag in _rule_set_refs(rule)}
    assert referenced == tags, "Every rule set needs a reference. An unused rule set is downloaded on each startup"


def test_groups_accepts_single_string():
    doc = _minimal()
    doc["groups"][0]["groups"] = "HK"
    a = Artifact.model_validate(doc)
    assert a.groups[0].groups == ["HK"]


def test_leftover_nodes_go_into_proxy_and_final():
    """Nodes that no region group collects go straight into Proxy and Final. No extra group."""
    a = load_artifact(str(DEFAULT))
    assert "其他节点" not in [g.tag for g in a.groups]
    proxy = next(g for g in a.groups if g.tag == "Proxy")
    final = next(g for g in a.groups if g.tag == "Final")
    assert proxy.groups == ["Auto", "HK", "JP", "SG", "TW", "US"]
    assert final.groups == ["Proxy", "Auto", "HK", "JP", "SG", "TW", "US"]
    for g in (proxy, final):
        assert g.regions == ["*"]
        assert g.exclude_regions == ["HK", "JP", "SG", "TW", "US"]
    assert proxy.default == "Auto"
    # Every group needs a reference, so no group sits idle.
    referenced = {g for grp in a.groups for g in grp.groups} | {a.route.final}
    assert {g.tag for g in a.groups} <= referenced
    assert all("cdn.jsdelivr.net" in r.url for r in a.rule_sets)
    assert a.http_clients[0].tag == "download"
    assert a.http_clients[0].detour == "Auto", "Rule sets download through a node, because a direct fetch cannot reach the CDN"
    assert a.route.default_http_client == "download"


def test_missing_meta():
    doc = _minimal()
    del doc["meta"]
    with pytest.raises(ValidationError) as exc:
        Artifact.model_validate(doc)
    assert "meta" in str(exc.value)


def test_missing_meta_name():
    doc = _minimal()
    del doc["meta"]["name"]
    with pytest.raises(ValidationError) as exc:
        Artifact.model_validate(doc)
    assert "name" in str(exc.value) and "meta" in str(exc.value)


def test_missing_groups():
    doc = _minimal()
    del doc["groups"]
    with pytest.raises(ValidationError) as exc:
        Artifact.model_validate(doc)
    assert "groups" in str(exc.value)


@pytest.mark.parametrize("version", ["1.11", "1.10.0", "0.9"])
def test_rejects_old_version(version: str):
    doc = _minimal()
    doc["meta"]["singbox_version"] = version
    with pytest.raises(ValidationError, match="只支持 1.12"):
        Artifact.model_validate(doc)


def test_rejects_bad_version_string():
    doc = _minimal()
    doc["meta"]["singbox_version"] = "latest"
    with pytest.raises(ValidationError, match="版本号要写成"):
        Artifact.model_validate(doc)


def test_rejects_duplicate_group_tag():
    doc = _minimal()
    doc["groups"].append({"tag": "节点选择", "type": "selector"})
    with pytest.raises(ValidationError, match="重名 tag"):
        Artifact.model_validate(doc)


def test_rejects_unknown_group_type():
    doc = _minimal()
    doc["groups"][0]["type"] = "fallback"
    with pytest.raises(ValidationError):
        Artifact.model_validate(doc)


def test_dns_server_keeps_unknown_fields():
    doc = _minimal()
    doc["dns"]["servers"][0]["inet4_range"] = "198.18.0.0/15"
    a = Artifact.model_validate(doc)
    assert a.dns.servers[0].model_extra["inet4_range"] == "198.18.0.0/15"


def test_route_needs_default_domain_resolver():
    doc = _minimal()
    del doc["route"]["default_domain_resolver"]
    with pytest.raises(ValidationError) as exc:
        Artifact.model_validate(doc)
    assert "default_domain_resolver" in str(exc.value)


def test_domain_resolver_must_be_a_dns_server():
    doc = _minimal()
    doc["route"]["default_domain_resolver"] = "不存在"
    with pytest.raises(ValidationError, match="不在 dns.servers 里"):
        Artifact.model_validate(doc)


def test_resolve_precedes_ip_rules():
    """Under fake-ip, an IP rule needs the real IP, so `resolve` must run first."""
    a = load_artifact(str(DEFAULT))
    kinds = []
    for rule in a.route.rules:
        if rule.get("action") == "resolve":
            kinds.append("resolve")
        elif "ip_is_private" in rule or "geoip" in rule:
            kinds.append("ip")
    assert "resolve" in kinds, "Without action: resolve, an IP rule such as geoip-cn never matches"
    assert kinds.index("resolve") < kinds.index("ip"), "resolve must run before the IP rules"


def test_no_action_direct():
    """`action: direct` is a no-op on 1.14.3. Use `outbound: Direct`."""
    a = load_artifact(str(DEFAULT))
    bad = [r for r in a.route.rules if r.get("action") == "direct"]
    assert bad == [], f"These rules send the traffic around the direct outbound: {bad}"
    direct_rules = [r for r in a.route.rules if r.get("outbound") == "Direct"]
    assert len(direct_rules) >= 3, "All three direct rules must stay: LAN, China domains, China IPs"
