"""A user profile: rule-set declarations, merge position, duplicate tags, and rejected rules."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from sbconv import load, load_artifact, load_profile, merge
from sbconv.artifacts import Artifact, Profile
from sbconv.check import check
from sbconv.clean import clean
from sbconv.merge import MergeError
from sbconv.models import Node
from sbconv.render import render

FIXTURES = Path(__file__).parent / "fixtures"
ARTIFACT = Path(__file__).parents[1] / "artifacts" / "base.yaml"
EXAMPLE = Path(__file__).parents[1] / "artifacts" / "example.yaml"


def _base() -> Artifact:
    return load_artifact(str(ARTIFACT))


def _profile(**kw: Any) -> Profile:
    doc: dict[str, Any] = {"groups": [], "rule_sets": [], "route": {"user_rules": []}}
    doc.update(kw)
    return Profile.model_validate(doc)


# Rule sets carry their own URL


NETFLIX_SRS = "https://cdn.jsdelivr.net/gh/SagerNet/sing-geosite@rule-set/geosite-netflix.srs"


def test_rule_set_keeps_url_and_format():
    p = _profile(rule_sets=[{"tag": "my-list", "url": "https://example.com/my.srs", "format": "source"}])
    assert p.rule_sets[0].url == "https://example.com/my.srs"
    assert p.rule_sets[0].format == "source"


def test_rule_set_defaults_to_binary_remote():
    p = _profile(rule_sets=[{"tag": "geosite-netflix", "url": NETFLIX_SRS}])
    assert (p.rule_sets[0].type, p.rule_sets[0].format) == ("remote", "binary")


def test_rule_set_needs_a_url():
    with pytest.raises(ValidationError, match="url"):
        _profile(rule_sets=[{"tag": "geosite-netflix"}])


# Actions that a profile rule may use


@pytest.mark.parametrize("action", ["resolve", "direct", "sniff", "hijack-dns"])
def test_reserved_actions_rejected(action: str):
    with pytest.raises(ValidationError, match="只能写匹配条件加 outbound"):
        _profile(route={"user_rules": [{"action": action, "outbound": "Proxy"}]})


def test_reject_rule_allowed():
    p = _profile(route={"user_rules": [{"domain_suffix": ["ads.example"], "action": "reject"}]})
    assert p.route.user_rules[0]["action"] == "reject"


def test_rule_without_outbound_rejected():
    with pytest.raises(ValidationError, match="既没有 outbound"):
        _profile(route={"user_rules": [{"rule_set": "geosite-netflix"}]})


# The insertion point


def test_base_declares_an_insert_point():
    a = _base()
    assert a.route.user_rules_at is not None, "The base template must keep an insertion point for profile rules"
    assert all("insert" not in rule for rule in a.route.rules), "The sentinel must not stay in the rule list. The core rejects the insert key"


def test_user_rules_land_on_the_insert_point():
    base = _base()
    rule = {"rule_set": "geosite-netflix", "outbound": "Streaming"}
    merged = merge(base, _profile(route={"user_rules": [rule]}))

    rules = merged.route.rules
    i = rules.index(rule)
    assert rules[i - 1] == {"ip_is_private": True, "outbound": "direct"}
    assert rules[i + 1] == {"rule_set": "geosite-geolocation-cn", "outbound": "direct"}
    assert base.route.rules == [r for r in rules if r != rule], "A merge must not rewrite the base rules"


def test_profile_groups_come_after_base_groups():
    merged = merge(_base(), _profile(groups=[{"tag": "Streaming", "type": "selector", "groups": ["US"]}]))
    tags = [g.tag for g in merged.groups]
    assert tags[:8] == ["Auto", "HK", "JP", "SG", "TW", "US", "Proxy", "Final"]
    assert tags[-1] == "Streaming"


def test_empty_profile_is_a_no_op():
    base = _base()
    merged = merge(base, Profile())
    assert merged.route.rules == base.route.rules
    assert [g.tag for g in merged.groups] == [g.tag for g in base.groups]
    assert [r.tag for r in merged.rule_sets] == [r.tag for r in base.rule_sets]


def _no_slot_artifact() -> Artifact:
    return Artifact.model_validate(
        {
            "meta": {"name": "t", "singbox_version": "1.14"},
            "groups": [{"tag": "Proxy", "type": "selector"}],
            "dns": {"servers": [{"tag": "local", "type": "https", "server": "223.5.5.5"}], "final": "local"},
            "route": {"default_domain_resolver": "local", "final": "Proxy"},
        }
    )


def test_rules_need_an_insert_point():
    profile = _profile(route={"user_rules": [{"domain_suffix": ["a.example"], "outbound": "Proxy"}]})
    with pytest.raises(MergeError, match="没有插入点"):
        merge(_no_slot_artifact(), profile)


def test_groups_merge_without_an_insert_point():
    merged = merge(_no_slot_artifact(), _profile(groups=[{"tag": "X", "type": "selector", "regions": ["*"]}]))
    assert [g.tag for g in merged.groups] == ["Proxy", "X"]
    assert merged.route.rules == []


@pytest.mark.parametrize(
    "bad",
    [
        {"insert": "other"},
        {"insert": "user_rules", "outbound": "direct"},
        {"insert": "user_rules", "ip_is_private": True},
    ],
)
def test_bad_insert_point_is_rejected(bad: dict):
    doc = {
        "meta": {"name": "t", "singbox_version": "1.14"},
        "groups": [{"tag": "Proxy", "type": "selector"}],
        "dns": {"servers": [{"tag": "local", "type": "https", "server": "223.5.5.5"}], "final": "local"},
        "route": {"default_domain_resolver": "local", "rules": [bad]},
    }
    with pytest.raises(ValidationError, match="插入点只能写"):
        Artifact.model_validate(doc)


def test_two_insert_points_are_rejected():
    doc = {
        "meta": {"name": "t", "singbox_version": "1.14"},
        "groups": [{"tag": "Proxy", "type": "selector"}],
        "dns": {"servers": [{"tag": "local", "type": "https", "server": "223.5.5.5"}], "final": "local"},
        "route": {"default_domain_resolver": "local", "rules": [{"insert": "user_rules"}, {"insert": "user_rules"}]},
    }
    with pytest.raises(ValidationError, match="只能有一处"):
        Artifact.model_validate(doc)


def test_tag_conflicts_are_rejected():
    with pytest.raises(MergeError, match="分组与基础模板重名"):
        merge(_base(), _profile(groups=[{"tag": "HK", "type": "selector"}]))
    with pytest.raises(MergeError, match="规则集与基础模板重名"):
        merge(_base(), _profile(rule_sets=[{"tag": "geosite-private", "url": NETFLIX_SRS}]))


def test_duplicate_tags_inside_a_profile_are_rejected():
    with pytest.raises(ValidationError, match="重名 tag"):
        _profile(groups=[{"tag": "X", "type": "selector"}, {"tag": "X", "type": "selector"}])


# End to end


def _nodes() -> list[Node]:
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
                "plugin-opts": {"mode": "http"},
            },
        )

    return [node("🇺🇸 美国 1", "192.0.2.13", 10036), node("🇭🇰 香港 1", "192.0.2.11", 10016)]


def test_merged_artifact_renders_in_order():
    base = Artifact.model_validate(
        {
            "meta": {"name": "t", "singbox_version": "1.14"},
            "nodes": {
                "plugin_alias": {"obfs": "obfs-local"},
                "region_keys": [{"type": "chinese_name"}, {"type": "flag_emoji"}, {"type": "iso_code"}],
            },
            "groups": [
                {"tag": "Auto", "type": "urltest", "regions": ["*"]},
                {"tag": "US", "type": "selector", "regions": ["US"]},
                {
                    "tag": "Proxy",
                    "type": "selector",
                    "groups": ["Auto", "US"],
                    "regions": ["*"],
                    "exclude_regions": ["US"],
                    "default": "Auto",
                },
            ],
            "dns": {"servers": [{"tag": "local", "type": "https", "server": "223.5.5.5"}], "final": "local"},
            "rule_sets": [{"tag": "geoip-cn", "url": "https://cdn.jsdelivr.net/gh/SagerNet/sing-geoip@rule-set/geoip-cn.srs"}],
            "route": {
                "default_domain_resolver": "local",
                "rules": [
                    {"action": "resolve", "strategy": "prefer_ipv4"},
                    {"ip_is_private": True, "outbound": "direct"},
                    {"insert": "user_rules"},
                    {"rule_set": "geoip-cn", "outbound": "direct"},
                ],
                "final": "Proxy",
            },
        }
    )
    profile = _profile(
        groups=[{"tag": "Streaming", "type": "selector", "groups": ["US"], "default": "US"}],
        route={"user_rules": [{"domain_suffix": ["netflix.com"], "outbound": "Streaming"}]},
    )
    merged = merge(base, profile)
    kept, report = clean(_nodes(), merged.nodes)
    config, _ = render(merged, kept, report)

    errors, _ = check(config)
    assert errors == []

    assert config["route"]["rules"] == [
        {"action": "resolve", "strategy": "prefer_ipv4"},
        {"ip_is_private": True, "outbound": "direct"},
        {"domain_suffix": ["netflix.com"], "outbound": "Streaming"},
        {"rule_set": "geoip-cn", "outbound": "direct"},
    ]
    streaming = next(o for o in config["outbounds"] if o["tag"] == "Streaming")
    assert streaming["outbounds"] == ["US"], "A reference to a base group stays a tag"
    assert streaming["default"] == "US"


def test_example_profile_renders_with_the_real_base():
    """The example profile in artifacts/ merges with the base template, and the rule lands on the insertion point."""
    merged = merge(_base(), load_profile(str(EXAMPLE)))
    kept, report = clean(load(str(FIXTURES / "sample-clash.yaml")).nodes, merged.nodes)
    config, _ = render(merged, kept, report)
    errors, _ = check(config)

    assert {r["tag"] for r in config["route"]["rule_set"]} >= {"geosite-netflix"}
    rule = next(r for r in config["route"]["rules"] if r.get("outbound") == "Streaming")
    assert rule == {"rule_set": "geosite-netflix", "outbound": "Streaming"}
    # sample-clash.yaml has one usable node in Hong Kong, so the US and JP groups
    # are empty. Streaming references group tags, so it keeps two members and the
    # assertion also holds the rule: a group reference stays a tag.
    empty = {e.split("'")[1] for e in errors if "为空" in e}
    assert "US" in empty and "Streaming" not in empty
