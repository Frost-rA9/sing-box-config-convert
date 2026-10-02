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
pwsh -File tools\smoke-elevated.ps1 # 运行时冒烟（提权，输出到 out/smoke.log）
```

## 目录

```text
sub/          订阅原文（只取节点；含凭据，不入库）
base/         基线层：基础组与粗粒度规则（agent 只读）
custom/       模板层：空骨架 + 写作说明（不含任何具体服务选择）
local/        私有层：真实的组 / 映射 / 规则（不入库；存在则整文件替换 custom/ 同名文件）
policy.json   环境策略：TUN / DNS / 入站 / 模式档位
docs/         案例笔记（proxy 模式的空载 tun · SFA 的四条 Android 坑）
build/ out/   生成物（不要手改）
```

**加组 / 加映射**：写到 `local/`（判断依据见 `AGENTS.md` §2，写法与重叠陷阱见 `custom/*.json` 的 `$comment`）；改完按 `AGENTS.md` §7 的验收标准收尾。`sub/` 是唯一需要自备的外部输入（把订阅原文存成 `sub/raw-*.yaml`）。

## 形态

模板层（clone 后即可复现）与私有层（`local/`，不入库）分开，产物 = 模板层叠加私有层。端口 / 档位 / 规则条数 / rule-set 清单以 `policy.json` 与 `python tools/convert.py` 的输出为准，本文件不复制它们（复制就会过期）。

**演练（确认模板层自洽 / 再恢复自己的形态）**：

```bash
SBC_LOCAL_DIR=off python tools/convert.py && SBC_LOCAL_DIR=off python tools/verify.py
python tools/convert.py && python tools/verify.py    # 恢复私有层
```

订阅里的内联规则**不进入产物**（理由见 `AGENTS.md` §3）。

## 平台前提

| 项 | 说明 |
|---|---|
| 内核 | sing-box **1.14.x**。路径解析顺序：`$env:SINGBOX_CORE` → `tools/core.local`（单行写绝对路径，不入库）→ PATH |
| 端口 | mixed `127.0.0.1:9870`（避开 `7890` 与动态端口段）、Clash API `127.0.0.1:9090` |
| 客户端 | 官方桌面客户端（Electron 壳 + 独立内核进程）：内核以 **Windows 服务（LocalSystem）** 身份运行，另有用户会话 worker |
| 订阅 | `sub/raw-*.yaml`；用带 Clash 客户端标识的 UA 拉取（如 `clash-verge/v2.0.0`），换 UA 会拿到不同协议 |
| WSL（若用） | WSL2 镜像模式会自动继承 Windows 系统代理 |

> 系统代理只认「内核所属用户」的 hive —— 服务身份设不到你头上。本仓用 `inbounds.tun_idle`（空载 tun + `platform.http_proxy`）解决，细节见该字段的 `$comment` 与 `AGENTS.md` §4 的 I11。
>
> 完整排查过程与原理：[`docs/system-proxy-case-study.zh.md`](docs/system-proxy-case-study.zh.md)（[English](docs/system-proxy-case-study.md)）
>
> Android / SFA 的四条特有坑：[`docs/sfa-notes.md`](docs/sfa-notes.md)
