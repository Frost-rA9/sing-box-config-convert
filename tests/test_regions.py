"""Region detection: the match order and the boundaries of the three tables."""

from __future__ import annotations

import pytest

from sbconv.regions import FLAGS, flag_of, match_chinese_name, match_flag, match_iso_code


def test_flag_of():
    assert flag_of("HK") == "🇭🇰"
    assert flag_of("TW") == "🇹🇼"
    assert flag_of("GB") == "🇬🇧"


def test_flags_cover_region_names():
    assert len(FLAGS) >= 60
    assert all(len(f) == 2 for f in FLAGS)


@pytest.mark.parametrize(
    ("name", "code"),
    [
        ("香港 01", "HK"),
        ("日本 01", "JP"),
        ("新加坡 01", "SG"),
        ("台湾 01", "TW"),
        ("美國 洛杉矶 01", "US"),
        ("东京 01", "JP"),
        ("狮城 01", "SG"),
    ],
)
def test_chinese_name(name: str, code: str):
    hit = match_chinese_name(name)
    assert hit is not None and hit[1] == code


@pytest.mark.parametrize("name", ["IEPL 专线 1", "Socks 01", "IPLC-1x", "Relay 01"])
def test_chinese_name_no_false_positive(name: str):
    assert match_chinese_name(name) is None


@pytest.mark.parametrize(
    ("name", "code"),
    [("🇭🇰 香港 1", "HK"), ("🇹🇼 TW 1", "TW"), ("🇩🇪 DE 1", "DE")],
)
def test_flag(name: str, code: str):
    hit = match_flag(name)
    assert hit is not None and hit[1] == code


@pytest.mark.parametrize(
    ("name", "code"),
    [("US 洛杉矶 01", "US"), ("JP Tokyo 01", "JP"), ("[SG] 01", "SG")],
)
def test_iso_code(name: str, code: str):
    hit = match_iso_code(name)
    assert hit is not None and hit[1] == code


@pytest.mark.parametrize("name", ["🇭🇰 香港 IEPL 01", "Traffic: 9.99 GB | 99 GB", "IEPL-01", "Socks 01"])
def test_iso_code_no_false_positive(name: str):
    assert match_iso_code(name) is None


def test_iepl_is_not_poland():
    """sub2sing-box reads the PL inside IEPL as Poland."""
    assert match_iso_code("🇭🇰 香港 IEPL 01") is None
    assert match_iso_code("德国 IEPL 01") is None


def test_traffic_gb_is_not_uk():
    assert match_iso_code("Traffic: 9.99 GB | 99 GB") is None


def test_unrecognized_region_code():
    from sbconv.regions import REGION_NAMES

    assert REGION_NAMES["XX"] == "未识别"
    assert flag_of("XX") not in FLAGS


def test_default_fallback_region():
    from sbconv.artifacts import NodeRules

    assert NodeRules().fallback_region == "XX"
