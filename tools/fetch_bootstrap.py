#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""下载远程 rule-set 的冷启动快照（initial_path）。

为什么需要：sing-box 启动时如果远程 rule-set 拉不下来，会直接 FATAL 退出。
initial_path 让冷启动读本地快照、不阻塞，起来之后再后台热更新。

实现说明
--------
下载器用 curl 而不是 urllib：curl 有 Happy Eyeballs（IPv4/IPv6 并行择优）、
内建重试和并发，实测直连 jsDelivr 稳定在 1 秒内；Python 的 urllib 在这台机器上
会在 TLS 握手上卡到超时。
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(ROOT, "out", "bootstrap_manifest.json")

CURL = shutil.which("curl") or r"C:\Windows\System32\curl.exe"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--proxy", default=None, help="例如 http://127.0.0.1:7890；默认直连")
    ap.add_argument("--force", action="store_true", help="已存在的也重新下载")
    ap.add_argument("--timeout", type=int, default=25, help="单文件超时秒数")
    ap.add_argument("--jobs", type=int, default=6, help="并发数")
    args = ap.parse_args()

    if not os.path.exists(MANIFEST):
        raise SystemExit("找不到 out/bootstrap_manifest.json，先跑 tools/convert.py")
    if not os.path.exists(CURL):
        raise SystemExit(f"找不到 curl: {CURL}")

    with open(MANIFEST, encoding="utf-8") as f:
        manifest = json.load(f)

    todo = []
    skip = 0
    for tag, info in sorted(manifest.items()):
        dst = os.path.join(ROOT, info["initial_path"].replace("/", os.sep))
        if os.path.exists(dst) and not args.force:
            skip += 1
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        todo.append((tag, info["url"], dst))

    if not todo:
        print(f"全部已存在（跳过 {skip}）。用 --force 强制重下。")
        return 0

    print(f"待下载 {len(todo)} 个（跳过 {skip}），并发 {args.jobs}，单文件超时 {args.timeout}s\n")

    # curl 的 --parallel + --write-out 无法逐个映射到文件名，所以按批并发、逐批核对
    failed = []
    for i in range(0, len(todo), args.jobs):
        batch = todo[i:i + args.jobs]
        procs = []
        for tag, url, dst in batch:
            cmd = [
                CURL, "-sSL", "--fail", "--max-time", str(args.timeout),
                "--retry", "2", "--retry-delay", "1", "--connect-timeout", "10",
                "-o", dst, url,
            ]
            if args.proxy:
                cmd[1:1] = ["-x", args.proxy]
            procs.append((tag, dst, subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                                     stderr=subprocess.PIPE)))
        for tag, dst, p in procs:
            err = p.communicate(timeout=args.timeout + 20)[1].decode("utf-8", "replace").strip()
            size = os.path.getsize(dst) if os.path.exists(dst) else 0
            if p.returncode != 0 or size < 64:
                failed.append(tag)
                if os.path.exists(dst):
                    os.remove(dst)
                print(f"  [FAIL] {tag:26} rc={p.returncode} {err[:90]}")
            else:
                sha = hashlib.sha256(open(dst, "rb").read()).hexdigest()[:12]
                print(f"  [ok]   {tag:26} {size:>8} B  sha256={sha}")

    total = sum(os.path.getsize(os.path.join(ROOT, m["initial_path"].replace("/", os.sep)))
                for t, m in manifest.items()
                if os.path.exists(os.path.join(ROOT, m["initial_path"].replace("/", os.sep))))
    print(f"\n完成：成功 {len(todo) - len(failed)} / 失败 {len(failed)} / 快照总量 {total / 1024:.0f} KB")
    if failed:
        print("失败项: " + ", ".join(failed), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
