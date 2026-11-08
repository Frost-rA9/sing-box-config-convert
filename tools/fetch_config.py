"""Fetch a subscription link into a local Clash YAML file.

The converter reads files only, so the fetch happens once here, and the file this
tool writes is the single input of the conversion.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import requests
import yaml

DEFAULT_USER_AGENT = "clash.meta"
TIMEOUT = 30


class FetchError(Exception):
    """The link gave no Clash configuration."""


def fetch(url: str, user_agent: str) -> str:
    """Return the response body. The proxy comes from the environment."""
    resp = requests.get(url, headers={"User-Agent": user_agent}, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.content.decode("utf-8", "replace")


def node_count(text: str) -> int:
    """Return the number of nodes, and reject a body that is not a Clash file."""
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise FetchError(f"响应不是 YAML：{exc}") from exc
    if not isinstance(doc, dict) or "proxies" not in doc:
        raise FetchError("响应不是 Clash 配置：缺少 proxies 段。链接可能已失效，或订阅只给链接列表")
    proxies = doc.get("proxies")
    if not isinstance(proxies, list) or not proxies:
        raise FetchError("Clash 配置里没有节点")
    return len(proxies)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="抓取订阅链接，存成本地 Clash YAML 文件")
    parser.add_argument("url", help="订阅链接")
    parser.add_argument("-o", "--output", required=True, help="输出文件，例如 local/ikuuu-sub.yaml")
    parser.add_argument(
        "--user-agent", default=DEFAULT_USER_AGENT, help=f"User-Agent 请求头，默认 {DEFAULT_USER_AGENT}"
    )
    parser.add_argument("--force", action="store_true", help="覆盖已存在的文件")
    args = parser.parse_args(argv)

    out = Path(args.output)
    if out.exists() and not args.force:
        print(f"文件已存在：{out}。加 --force 覆盖", file=sys.stderr)
        return 2
    try:
        text = fetch(args.url, args.user_agent)
        count = node_count(text)
    except (FetchError, requests.RequestException) as exc:
        print(f"抓取失败：{exc}", file=sys.stderr)
        return 2

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
    print(f"节点        {count}")
    print(f"配置文件    {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
