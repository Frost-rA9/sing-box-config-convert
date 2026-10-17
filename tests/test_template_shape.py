"""Template shape probe: use the real core to confirm the field names in the skeleton.

The probe is a hand-written sample of the shape that the renderer must produce,
and it pinned the field names before the renderer existed. Without SBCONV_SINGBOX
the test does not run.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

PROBE = Path(__file__).parent / "fixtures" / "shape-probe.json"


def _singbox() -> str | None:
    env = os.environ.get("SBCONV_SINGBOX")
    if env:
        return env
    return shutil.which("sing-box")


def test_shape_probe_is_valid_json():
    import json

    doc = json.loads(PROBE.read_text(encoding="utf-8"))
    assert doc["route"]["final"] == "Final"
    assert doc["route"]["default_domain_resolver"] == {"server": "local"}
    # Direct and Reject appear as route actions only, not as outbounds.
    tags = {o["tag"] for o in doc["outbounds"]}
    assert "Reject" not in tags
    assert {"Proxy", "Final", "Auto", "Direct"} <= tags
    # The direct outbound must not be empty, or the rule-set fetch stops with
    # FATAL: detour to an empty direct outbound
    direct = next(o for o in doc["outbounds"] if o["tag"] == "Direct")
    assert direct.get("domain_resolver")
    assert doc["route"]["default_http_client"] == "download"


@pytest.mark.skipif(_singbox() is None, reason="需要 SBCONV_SINGBOX 指向 sing-box 可执行文件")
def test_shape_probe_passes_check():
    sb = _singbox()
    proc = subprocess.run(
        [sb, "check", "-c", str(PROBE)],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "FATAL" not in proc.stderr
