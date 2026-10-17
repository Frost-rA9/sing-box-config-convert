"""Fetch remote rule sets into a local directory and remove the startup dependency.

The core fetches them at startup, and a failed fetch is a FATAL error. A test saw
rare connection resets from jsDelivr, so the output can carry a local copy.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import requests

TIMEOUT = 60


class LocalizeError(Exception):
    pass


def localize_rule_sets(config: dict[str, Any], base_dir: Path, absolute: bool = False) -> list[str]:
    """Download the remote rule sets and rewrite them as local references.

    The core resolves a relative path against its working directory. A GUI client
    uses its install directory as the working directory, so it needs absolute
    paths.
    """
    rule_sets = (config.get("route") or {}).get("rule_set") or []
    if not rule_sets:
        return []
    target = base_dir / "rules"
    target.mkdir(parents=True, exist_ok=True)
    done: list[str] = []
    for entry in rule_sets:
        url = entry.get("url")
        if not url:
            continue
        resp = requests.get(url, timeout=TIMEOUT)
        if resp.status_code != 200 or not resp.content:
            raise LocalizeError(f"下载 {entry.get('tag')} 失败：HTTP {resp.status_code}")
        name = f"{entry['tag']}.srs"
        (target / name).write_bytes(resp.content)
        entry.pop("url", None)
        entry.pop("type", None)
        entry["type"] = "local"
        entry["format"] = "binary"
        entry["path"] = str((target / name).resolve()) if absolute else f"rules/{name}"
        done.append(entry["tag"])
    return done
