"""Command line: `report` prints the clean report, `render` writes and validates the configuration."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from .artifacts import load_artifact, load_profile
from .check import check
from .clean import CleanReport, clean
from .loader import LoadError, load
from .merge import MergeError, merge
from .regions import REGION_NAMES
from .render import RenderError, proxy_mode, render
from .rulesets import LocalizeError, localize_rule_sets

DEFAULT_ARTIFACT = "artifacts/base.yaml"


def _prepare(args: argparse.Namespace):
    base = load_artifact(args.artifact)
    profile = load_profile(args.profile) if args.profile else None
    artifact = merge(base, profile)
    result = load(args.source, user_agent=args.user_agent)
    kept, report = clean(result.nodes, artifact.nodes)
    return artifact, profile, result, kept, report


def cmd_report(args: argparse.Namespace) -> int:
    try:
        artifact, profile, result, kept, report = _prepare(args)
    except (LoadError, MergeError, ValueError) as exc:
        print(f"失败：{exc}", file=sys.stderr)
        return 2

    print(f"载体        {result.carrier}")
    print(f"载入节点    {len(result.nodes)}")
    print(f"保留节点    {len(kept)}")
    print(f"剔除节点    {len(report.dropped)}")
    for name, reason in report.dropped:
        print(f"  - {name}：{reason}")
    if result.skipped:
        print(f"跳过行      {len(result.skipped)}")
    if report.renamed:
        print(f"改名        {len(report.renamed)}")
        for old, new in report.renamed:
            print(f"  - {old} → {new}")
    print(f"插件归一    {len(report.plugin_changed)}")
    summary: dict[tuple[str, str], int] = {}
    for _, old, new in report.plugin_changed:
        summary[(old, new)] = summary.get((old, new), 0) + 1
    for (old, new), count in sorted(summary.items(), key=lambda kv: -kv[1]):
        print(f"  - {old} → {new}：{count} 个")

    print("地区分布")
    for code, count in sorted(report.distribution.items(), key=lambda kv: -kv[1]):
        print(f"  {code:<10} {REGION_NAMES.get(code, code):<8} {count}")
    if report.unknown:
        print(f"未识别地区  {len(report.unknown)}")
        for name in report.unknown:
            print(f"  - {name}")
    plugins = sorted({n.plugin for n in kept if n.plugin})
    print(f"插件取值    {plugins or '无'}")
    if profile:
        print(
            f"策略        {args.profile}：新增 {len(profile.groups)} 个分组，"
            f"{len(profile.route.user_rules)} 条路由规则，{len(profile.rule_sets)} 个规则集"
        )
    print(f"分支        {len(artifact.groups)} 个分组，{len(artifact.rule_sets)} 个规则集")
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    try:
        artifact, _, result, kept, report = _prepare(args)
        if args.proxy_mode:
            artifact = artifact.model_copy(update={"inbounds": proxy_mode(artifact.inbounds)})
        if args.test_inbound:
            # A proxy already runs here. Use one mixed inbound to avoid a port
            # clash and an admin prompt.
            artifact = artifact.model_copy(
                update={
                    "inbounds": [
                        {
                            "type": "mixed",
                            "tag": "mixed-in",
                            "listen": "127.0.0.1",
                            "listen_port": args.test_inbound,
                        }
                    ]
                }
            )
        config, warnings = render(artifact, kept, report)
    except (LoadError, MergeError, ValueError, RenderError) as exc:
        print(f"渲染失败：{exc}", file=sys.stderr)
        return 2

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    localized: list[str] = []
    if args.local_rulesets:
        try:
            localized = localize_rule_sets(config, out.parent, absolute=args.absolute_rulesets)
        except LocalizeError as exc:
            print(f"规则集本地化失败：{exc}", file=sys.stderr)
            return 2
    out.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"节点        {len(kept)}（剔除 {len(report.dropped)}，插件归一 {len(report.plugin_changed)}）")
    if localized:
        print(f"规则集      {len(localized)} 个已落到 {out.parent / 'rules'}，配置不再依赖外网抓取")
    for w in warnings:
        print(f"警告        {w}")

    errors, warns = check(config)
    for w in warns:
        print(f"检查提示    {w}")
    if errors:
        for e in errors:
            print(f"检查错误    {e}", file=sys.stderr)
        print(f"配置已写出但未通过静态检查：{out}", file=sys.stderr)
        return 3
    print("静态检查    通过")

    singbox = args.singbox or shutil.which("sing-box")
    if not singbox:
        print("内核检查    跳过（未找到 sing-box，用 --singbox 指定）")
        return 0
    # A local rule-set path resolves against the working directory, so run the
    # core there.
    proc = subprocess.run(
        [singbox, "check", "-c", out.name], cwd=str(out.parent), capture_output=True, text=True, timeout=300
    )
    msg = (proc.stdout + proc.stderr).strip()
    if proc.returncode != 0 or "FATAL" in msg:
        print(f"内核检查    失败\n{msg}", file=sys.stderr)
        return 4
    print("内核检查    sing-box check 通过")

    if args.run:
        print(f"启动验证    短跑 {args.run} 秒")
        try:
            proc = subprocess.run(
                [singbox, "run", "-c", out.name],
                cwd=str(out.parent),
                capture_output=True,
                text=True,
                timeout=args.run,
            )
        except subprocess.TimeoutExpired:
            print("启动验证    进程存活到超时，视为启动成功")
            return 0
        msg = (proc.stdout + proc.stderr).strip()
        print(f"启动验证    提前退出，退出码 {proc.returncode}\n{msg}", file=sys.stderr)
        return 5
    print(f"产物        {out}")
    if localized and not args.absolute_rulesets:
        print(f"启动方式    cd {out.parent} && sing-box run -c {out.name}")
    elif localized:
        print("启动方式    规则集用绝对路径，GUI 客户端可直接导入")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sbconv", description="Clash 订阅 → sing-box 配置")
    sub = parser.add_subparsers(dest="command", required=True)

    def shared(p: argparse.ArgumentParser) -> None:
        p.add_argument("source", help="订阅链接或 Clash YAML 文件")
        p.add_argument("-a", "--artifact", default=DEFAULT_ARTIFACT, help="基础模板")
        p.add_argument("-p", "--profile", default=None, help="用户策略文件，只写增量")
        p.add_argument("--user-agent", default="clash.meta")

    p_report = sub.add_parser("report", help="载入与清洗，出报告")
    shared(p_report)
    p_report.set_defaults(func=cmd_report)

    p_render = sub.add_parser("render", help="渲染配置并验收")
    shared(p_render)
    p_render.add_argument("-o", "--output", default="local/config.json")
    p_render.add_argument("--singbox", default=None, help="sing-box 可执行文件路径")
    p_render.add_argument("--run", type=int, default=0, help="额外短跑 N 秒做启动验证")
    inbounds_mode = p_render.add_mutually_exclusive_group()
    inbounds_mode.add_argument(
        "--test-inbound", type=int, default=0, help="用单个 mixed 入站替换工件里的 inbounds，便于本机试跑"
    )
    inbounds_mode.add_argument(
        "--proxy-mode", action="store_true", help="TUN 不接管流量，由 GUI 设置系统代理（见 system-proxy 案例）"
    )
    p_render.add_argument(
        "--local-rulesets", action="store_true", help="把远程规则集抓到 <输出目录>/rules 并改成 local 引用"
    )
    p_render.add_argument(
        "--absolute-rulesets", action="store_true", help="规则集写绝对路径，供 GUI 客户端使用（其工作目录是安装目录）"
    )
    p_render.set_defaults(func=cmd_render)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
