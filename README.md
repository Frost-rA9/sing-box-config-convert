# singbox-config

把 Clash 订阅确定性地转换成 sing-box 配置。**三段式：取节点 → 生成基础配置 → 按需叠加自定义。**

> 要改行为、要接手这个项目 —— 先读 [`AGENTS.md`](AGENTS.md)（职责边界、不变量、扩展点）。本文件只讲怎么用。

## 用法

```bash
python tools/convert.py                  # 三阶段连跑（日常用这个）
python tools/fetch_bootstrap.py          # 补 rule-set 冷启动快照（直连 jsDelivr，~300 KB）
python tools/verify.py                   # 不变量校验（不需要内核、不联网）
sing-box check -c out/config.tun.json    # 内核静态校验
```

```powershell
pwsh -File tools\start-tun.ps1      # TUN 模式（自动提权）
pwsh -File tools\start-proxy.ps1    # 普通模式（无 TUN，自动设/清系统代理）
pwsh -File tools\stop.ps1 -Status
```

## 目录

```
sub/          订阅原文（只取节点；含凭据，不入库）
base/         基线层：5 个基础组 + 9 条规则（agent 只读）
custom/       自定义层：组 / 映射 / 内联规则（按需增删）
policy.json   环境策略：TUN / DNS / 入站 / 模式档位
docs/         案例笔记（proxy 模式的空载 tun · SFA 的四条 Android 坑）
build/ out/   生成物（不要手改）
```

**加组 / 加映射**：改 `custom/` 即可（判断依据与模板见 `AGENTS.md` §2），改完跑 `verify.py`。

## 当前形态

```
入站      tun + mixed；proxy 模式 = 空载 tun（auto_route:false，仅承载 platform.http_proxy）+ mixed @ 127.0.0.1:9870
策略组    5 基础 + 8 自定义 = 13（AI / GitHub / Dev / Streaming / Apple / Microsoft / Google / Steam）
模式档位  规则（默认）/ 全局 / 直连
规则      基础 9 条 + custom 8 条 = 17 条
rule-set  16 个，全部来自 SagerNet 官方仓库，热更新 + 本地冷启动快照
产物      out/config.tun.json（桌面 TUN）· config.proxy.json（桌面系统代理）· config.android.json（SFA）
```

订阅里的内联规则**不进入产物**（理由见 `AGENTS.md` §3）。

## 环境事实

| 项 | 值 |
|---|---|
| 内核 | sing-box **1.14.1**。路径解析顺序：`$env:SINGBOX_CORE` → `tools/core.local`（单行写绝对路径，不入库）→ PATH |
| 端口 | mixed `127.0.0.1:9870`（避开 `7890` 与动态端口段）、Clash API `127.0.0.1:9090` |
| 客户端 | **SFW**（官方 Windows 客户端）：daemon 是 **Windows 服务（LocalSystem）**，另有一个用户会话 worker |
| 订阅 | `sub/raw-*.yaml`，用 UA `clash-verge/v2.0.0` 拉取（换 UA 会拿到不同协议） |
| WSL | WSL2 **镜像模式**，会自动继承 Windows 系统代理 |

> 系统代理只认「内核所属用户」的 hive —— 服务身份设不到你头上。本仓用 `inbounds.tun_idle`（空载 tun + `platform.http_proxy`）解决，细节见该字段的 `$comment` 与 `AGENTS.md` §4 的 I11。
>
> 完整排查过程与原理：[`docs/system-proxy-case-study.zh.md`](docs/system-proxy-case-study.zh.md)（[English](docs/system-proxy-case-study.md)）
>
> Android / SFA 的四条特有坑：[`docs/sfa-notes.md`](docs/sfa-notes.md)
