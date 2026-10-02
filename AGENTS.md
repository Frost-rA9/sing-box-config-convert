# 工作流规范

把 Clash 订阅确定性地转成 sing-box 配置。**三段式：先取节点，再生成基础配置，最后按需叠加自定义。**

```text
[1] sub/raw-*.yaml                               →  build/nodes.json
[2] nodes.json + base/ + policy.json             →  build/base.json
[3] base.json + local/(缺则 custom/ 模板) + policy.json  →  out/config.{tun,proxy,android}.json
```

```bash
python tools/convert.py --stage nodes|base|final   # 或三阶段连跑
python tools/fetch_bootstrap.py                    # 补 rule-set 冷启动快照
python tools/verify.py                             # 不变量校验（不需内核、不联网）
sing-box check -c out/config.tun.json              # 内核静态校验（内核路径见 tools/singbox-lib.ps1）
```

每阶段都有产物可查：`build/nodes.json`（可 diff 订阅更新）、`build/base.json`（无自定义层）。先看 base，再决定加什么。

命令的完整清单与启动方式（TUN / proxy / 冒烟）见 [`README.md`](README.md)；两篇不变量背后的完整排查过程见 `docs/`。


## 1. 职责边界

| 层 | 文件 | 谁维护 | 说明 |
|---|---|---|---|
| 输入 | `sub/raw-*.yaml` | 手动拉取 | 只用来取节点 |
| 基线 | `base/*.json` | 我们 | **agent 只读** |
| 模板 | `custom/*.json` | 我们 | **空骨架 + 写作说明**，不含任何具体服务选择；不入库的只有 `local/` |
| 私有 | `local/*.json` | 你 / agent | 真实的组 / 映射 / 规则（含使用画像），**不入库**；存在则整文件替换 `custom/` 同名模板 |
| 环境 | `policy.json` | 人 / agent | TUN / DNS / 入站 / 模式档位 / 首尾追加规则；不变量载体，**不要拆到私有层** |
| 机械 | `tools/convert.py` | 基本不动 | 只做映射，不含意图 |
| 产物 | `build/` `out/` | 生成物 | **不要手改** |

- **输入契约**：订阅只提供节点（tag / 协议 / 参数）；策略组、规则、路由、DNS、TUN 全部由本仓声明。换提供方 = 换一个 `sub/raw-*.yaml`。
- **机械层**：`convert.py` 只做映射 —— 域名 / 组名 / 开关一律来自声明文件。rule-set 的形态与来源由 I5 / I6 约束。
- **声明解析**：`local/<name>.json` 存在则**整文件替换** `custom/<name>.json`（不合并 —— 模板是空骨架，替换即组合，且书写顺序 = 优先级不会被搅乱）。`convert.py` 与 `verify.py` 各有一份同语义的 `resolve_decl()`（verify 故意不 import convert，避免依赖 PyYAML）。`SBC_LOCAL_DIR=off` 屏蔽私有层，用于「干净 clone 演练」。


## 2. 加组 / 加规则

**判断依据 —— 某类流量需要「与基础策略不同的出站」才加：**

1. 要**选不同节点**（某服务只对特定地区开放，或某些节点地区被封 / 被风控）
2. 用户明确要求
3. 要**独立开关**（一键切直连 / 拦截 / 换节点）
4. **账号 / 支付绑定在某地区**（应用商店、账号体系的地区属性：换节点会导致地区错乱、支付失败）

**反例**：不要为订阅里每个分类都建组 —— 订阅原文那几十个组就是这么来的。**per-service 规则集只在路由到不同出站时才有意义。**

```jsonc
// local/groups.json（custom/groups.json 是空模板，只放说明）
{ "tag": "Example", "type": "select", "order": 25, "members": ["Proxy", "Direct", "Reject", "@all"], "default": "Proxy" }

// local/targets.json —— ⚠️ 书写顺序 = 规则优先级
{ "Example": { "rule_set": ["geosite-example"], "outbound": "Example" } }
```

- 写法（占位符 / `order` / members 顺序 / ASCII 组名 / `position` 三档 / rule-set 重叠清单）见 `custom/*.json` 的 `$comment` —— 模板层就是写作说明书
- 内联规则（`geosite` 里没有的域名）写在 `local/rules.json`，`position` 的取舍见该模板 `$comment`


## 3. 为什么基础层只有 5 个 rule-set

订阅按服务分类的内联规则**不进产物**：在基础 5 组下，那些分类和粗粒度分类（CN / 私网直连、境外代理、广告拦截）路由结果相同，等价于一条规则。

这是收敛，不是丢弃。「零丢弃」会把订阅的冗余当成意图，rule-set 数量膨胀数倍。


## 4. 不变量

`tools/verify.py` 机器校验。**踩坑换来的，不是风格偏好，别为「简化」绕过。**

| # | 不变量 |
|---|---|
| I1 | `tun.strict_route == false`（Windows 上 true 会装 WFP（Windows Filtering Platform）过滤器，卡死 WSL） |
| I2 | `tun.route_exclude_address` 8 段（7 私网 + 组播，否则抢走 WSL 网段；android 为 7 段，见 I19） |
| I3 | `tun.dns_mode == "hijack"`（防内网 DNS 查询绕过 TUN 吃污染） |
| I4 | `route.default_domain_resolver` 指向**直连**解析器（走代理会成环） |
| I5 | 所有 `rule_set` 都是 `type: remote` |
| I6 | rule-set URL 属于 SagerNet 官方仓库 |
| I7 | `initial_path` 是绝对路径（按**进程工作目录**解析；android 见 I19） |
| I8 | 所有 `outbound` / `detour` / `rule_set` 引用都存在 |
| I9 | 出站 tag 唯一 |
| I10 | `clash_api.default_mode` 不出现在 `clash_mode` 规则里 |
| I11 | proxy 不得有接管流量的 tun（`auto_route:false` 的空载 tun 仅承载 `platform.http_proxy`）；无 `hijack-dns`；有 `set_system_proxy` |
| I12 | tun / proxy 除 `inbounds` 与 TUN 专属规则外完全一致 |
| I13 | 基线 5 个基础组 + 5 个粗粒度 rule-set 齐备 |
| I14 | `base/rules.json` 有 `$anchor: catchall` |
| I15 | custom 未覆盖基础组名 |
| I16 | rule-set 冷启动快照齐备 |
| I17 | 三份产物通过 `sing-box check` |
| I18 | 策略组顺序 = 声明里的 `order` |
| I19 | android 四处差异：不带 `initial_path`、mixed 不开 `set_system_proxy`、`route_exclude_address` 不含 `127.0.0.0/8`、tun 不用 `route_exclude_address_set`；原因见 `verify.py` 报错文案与 `docs/sfa-notes.md` |
| I20 | TUN 有域名类规则就必须有域名来源（`reverse_mapping` / fakeip / sniff），否则 geosite 规则静默失效；本仓用 `reverse_mapping + sniff`，理由见 `policy.json` 注释 |


## 5. 其它扩展点

- **加模式档位** —— `policy.json` 两处同时改（缺一不可）：`experimental.clash_api.default_mode` 与 `route.head_rules` 里的 `clash_mode` 规则。GUI 档位列表 = `[default_mode]` + 所有 `clash_mode` 取值。
- **运行期控制**：Clash API 是**只读**的（`PATCH /configs` 返回 204 但不生效）；切档 / 切组只能靠官方 GUI；`clash_mode` 只切路由，切不了 TUN 与系统代理。
- **改 TUN / DNS / 入站** → `policy.json`。**改基础路由结构**（不推荐，影响所有下游）→ `base/rules.json`。


## 6. 术语与写作约定

- 统一用「**服务提供方**」「订阅来源」，不用行业俚语（上游有风控检测）。
- 文档、注释、提交信息用中文；提交信息 = `类型(范围): 一句中文`，例如 `fix(android): 去掉 route_exclude_address_set`，一个提交一件事。


## 7. 给 agent 的操作约定

1. **改行为 = 改 `local/` 或 `policy.json`**；`custom/` 只是空模板（只有在要更新写作说明时才动它）；不要改 `base/`，不要改 `convert.py` 的逻辑。
2. 加自定义前**先看 `build/base.json`**，并参考 `--stage nodes` 打印的候选分类。
3. 改完**必须**跑 `verify.py`（全绿）与三份产物的 `sing-box check`（全通过），两项都过才算完成。
4. **未经明确指示不要 commit**；产物留在工作区。
5. 内核报错先看 `verify.py` 输出与声明文件里的 `$comment`；**新约束补进 `verify.py` 或 `$comment`**，不新建文档。
6. **不参与上游争论**：必要时只给技术方案，发完不回复；不提交代码 PR。
