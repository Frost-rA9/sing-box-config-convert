"""The Android import QR must carry the sing-box deep link, not a plain URL."""

from __future__ import annotations

import importlib.util
import urllib.parse
from pathlib import Path

TOOL = Path(__file__).parents[1] / "tools" / "qr_import.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("qr_import_tool", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_import_link_targets_the_android_deep_link():
    tool = _load_tool()
    remote, link = tool.build_links("192.168.1.2", 56379, "config-android", "abc")
    assert remote == "http://192.168.1.2:56379/config.json?token=abc"
    parsed = urllib.parse.urlparse(link)
    assert parsed.scheme == "sing-box"
    assert parsed.netloc == "import-remote-profile"
    assert urllib.parse.parse_qs(parsed.query)["url"] == [remote]
    assert parsed.fragment == "config-android"
