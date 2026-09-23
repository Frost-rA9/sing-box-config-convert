# 工作流规范

把 Clash 订阅确定性地转换成 sing-box 配置。**三段式：先取节点，再生成基础配置，最后按需叠加自定义。**

```
[1] sub/raw-*.yaml                              →  build/nodes.json
[2] nodes.json + base/ + policy.json            →  build/base.json
[3] base.json + custom/ + policy.json           →  out/config.tun.json
                                                   out/config.proxy.json
                                                   out/config.android.json
```

```bash
python tools/convert.py --stage nodes|base|final   # 或三阶段连跑
python tools/fetch_bootstrap.py                    # 补 rule-set 冷启动快照
python tools/verify.py                             # 不变量校验（不需内核、不联网）
sing-box check -c out/config.tun.json              # 内核静态校验（内核路径解析见 tools/singbox-lib.ps1）
```

**每个阶段都有产物可查**：`build/nodes.json`（可 diff 订阅更新）、`build/base.json`（无 custom）。先看 base，再决定加什么。

---

## 1. 职责边界

| 层 | 文件 | 谁维护 | 说明 |
|---|---|---|---|
| 输入 | `sub/raw-*.yaml` | 手动拉取 | **只用来取节点**，它的 rules/groups 不进入产物 |
| 基线 | `base/groups.json` `base/rules.json` | 我们 | **agent 只读**；改行为请改 policy.json 或 custom/ |
| 自定义 | `custom/groups.json` `custom/targets.json` `custom/rules.json` | 按需增删 | |
| 环境 | `policy.json` | 人 / agent | TUN / DNS / 入站 / 模式档位 / 兜底规则 |
| 机械 | `tools/convert.py` | 基本不动 | 只做映射，不含意图 |
| 产物 | `build/` `out/` | 生成物 | **不要手改** |

**输入契约**：订阅只提供节点（`tag` / 协议 / 参数）；策略组、规则、路由、DNS、TUN 全部由本仓声明。**换提供方 = 换一个 `sub/raw-*.yaml`**。

**硬规则**：`convert.py` 里不得出现具体域名 / 组名 / 开关；不得引入服务提供方仓库的规则集；不用 `type: local` 的 rule-set。

---

## 2. 加组 / 加规则

**判断依据 —— 某类流量需要「与基础策略不同的出站」时才加：**

1. 会**给它选不同节点**（Netflix 要特定地区、AI 要抗封节点）
2. 用户明确要求
3. 需要**独立开关**（一键切直连/拦截）
4. **账号 / 支付绑定在某地区**（Apple ID 港区、Google Play 付款日本、微软账号中国）

**反例**：不要为订阅里每个分类都建组 —— 订阅原文那几十个组就是这么来的（每个组都列出全部节点，纯冗余）。**per-service 规则集只在路由到不同出站时才有意义**。

```jsonc
// custom/groups.json
{ "tag": "AI", "type": "select", "order": 25, "members": ["Proxy", "Direct", "Reject", "@all"], "default": "Proxy" }

// custom/targets.json —— ⚠️ 书写顺序 = 规则优先级
{ "AI": { "rule_set": ["geosite-openai"], "outbound": "AI" } }
```

- 占位符：`@all` 全部节点、`@direct` / `@reject` 内置出站、`@<组名>` 引用其它组
- `order`：产物与 GUI 卡片顺序，缺省 1000（排最后）
- 组名一律 **ASCII 英文**（Clash API 对非 ASCII tag 报 400）
- 位置 `position`：`head`（压过 CN 直连）/ 省略（兜底之前，默认）/ `tail`
- **`targets.json` 顺序 = 优先级**：rule-set 之间有真实重叠（`microsoft` ⊃ `github`、`category-dev` ⊃ `github`、`google` ⊃ `youtube`），排错会让某个组**静默失效**。细节见该文件 `$comment`。

加完必须跑 `python tools/verify.py`。

---

## 3. 为什么基础层只有 5 个 rule-set

订阅原文里有大量按服务分类的内联规则。**它们不进入产物**：基础 5 组下这些分类全落到同一个出站，等价于一条规则 —— 粗粒度分类（CN/私网直连、境外代理、广告拦截）得到**同样的路由结果**。

这不是"丢弃"，是**收敛**。"零丢弃"是伪指标：早期版本追求它，给订阅的每个分类都配了独立 rule-set，数量膨胀了数倍 —— 那是把**订阅的冗余当成了意图**。

---

## 4. 不变量

由 `tools/verify.py` 机器校验。**踩坑换来的，不是风格偏好，不要为了"简化"绕过。**

| # | 不变量 |
|---|---|
| I1 | `tun.strict_route == false`（Windows 上 true 会装 WFP 过滤器拦非 TUN 流量 → 卡死 WSL） |
| I2 | `tun.route_exclude_address` 含 8 段（7 私网 + 组播，否则 `auto_route` 抢走 WSL 网段） |
| I3 | `tun.dns_mode == "hijack"`（否则发往内网 DNS 的查询绕过 TUN，吃到污染答案） |
| I4 | `route.default_domain_resolver` 指向**直连**解析器（指向走代理的会成环） |
| I5 | 所有 `rule_set` 都是 `type: remote` |
| I6 | 所有 rule-set URL 属于 SagerNet 官方仓库 |
| I7 | `initial_path` 是绝对路径（sing-box 按**进程工作目录**解析相对路径；android 见 I19） |
| I8 | 所有 `outbound` / `detour` / `rule_set` 引用都存在 |
| I9 | 出站 tag 唯一 |
| I10 | `clash_api.default_mode` 不出现在 `clash_mode` 规则里 |
| I11 | proxy 配置不得有**接管流量**的 tun（允许 `auto_route:false` 的空载 tun，仅承载 `platform.http_proxy`）；无 `hijack-dns`；有 `set_system_proxy` |
| I12 | tun / proxy 两份除 `inbounds` 与 TUN 专属规则外完全一致 |
| I13 | 基线层 5 个基础组 + 5 个粗粒度 rule-set 齐备 |
| I14 | `base/rules.json` 有 `$anchor: catchall` |
| I15 | custom 未覆盖基础组名 |
| I16 | rule-set 冷启动快照齐备 |
| I17 | 三份产物通过 `sing-box check` |
| I18 | 策略组顺序 = 声明里的 `order` |
| I19 | android 产物：rule-set **不带** `initial_path`（本机快照路径 Android 上不存在）、mixed **不开** `set_system_proxy`（需特权，SFA 下也不工作） |

---

## 5. 其它扩展点

**加模式档位** —— `policy.json` 两处同时改（缺一不可）：

```json
"experimental": { "clash_api": { "default_mode": "规则" } },
"route": { "head_rules": [ { "clash_mode": "全局", "action": "route", "outbound": "Proxy" } ] }
```

GUI 里的档位列表 = `[default_mode]` + 所有 `clash_mode` 取值。

**运行期控制**：Clash API 是**只读**的（`PATCH /configs` 返回 204 但不生效），切档 / 切组只能靠官方 GUI；`clash_mode` 也只能切路由，切不了 TUN 与系统代理。

**改 TUN / DNS / 入站** —— `policy.json` 的 `inbounds` / `dns`。
**改基础路由结构**（不推荐，影响所有下游）—— `base/rules.json`。

---

## 6. 运行方式

```powershell
pwsh -File tools\start-tun.ps1      # TUN 模式（自动提权）
pwsh -File tools\start-proxy.ps1    # 普通模式（无 TUN，自动设系统代理）
pwsh -File tools\stop.ps1 -Status
pwsh -File tools\smoke-elevated.ps1 # 运行时冒烟（提权，输出到 out/smoke.log）
```

---

## 7. 术语与写作约定

- 统一用「**服务提供方**」「订阅来源」，不用行业俚语（上游有风控检测）。
- 文档、注释、提交信息用中文。提交信息：`<type>(<scope>): <中文一句>`；一个提交一件事。

---

## 8. 给 agent 的操作约定

1. **改行为 = 改 `custom/` 或 `policy.json`**，不要改 `base/`，不要改 `convert.py` 的逻辑。
2. 加自定义前**先看 `build/base.json`**，并参考 `--stage nodes` 打印的候选分类。
3. 改完**必须**跑 `python tools/verify.py` + `sing-box check`，全绿才算完成。
4. **未经明确指示不要 commit**；产物留在工作区。
5. 不要手改 `build/`、`out/`；不要用 `type: local` 的 rule-set；不要用服务提供方仓库。
6. 遇到内核报错：先看 `verify.py` 的输出与对应声明文件里的 `$comment`（约束都记在那儿）；**新约束补进不变量（`verify.py`）或声明文件的 `$comment`**，而不是新建文档。
7. **不参与上游争论**：必要时只提供技术方案（可发 issue 评论），发完不回复后续；不提交代码 PR。本地 fork 或本仓记录即可。
