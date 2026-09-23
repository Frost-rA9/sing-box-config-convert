# 为什么 sing-box 以 Windows 服务运行时，`set_system_proxy` 会静默失效

*以及一个不需要改代码的配置解法。*

**一句话** —— WinINet 的系统代理设置是**按用户**存放的，而桌面客户端的 daemon 以
`LocalSystem` 身份运行。于是 `mixed` / `http` 入站上的 `set_system_proxy` 被写进了
`HKU\S-1-5-18`（服务账号的 hive），当前登录用户完全看不到。而 sing-box 里**本来就有**
一套「冒充登录用户」的机制，用在 `tun.platform.http_proxy` 上；只要挂一个**不接管流量**
的 TUN 入站来承载这个字段，系统代理就能正常工作，而流量一点都不会被截走。

*本笔记源自 [#4447](https://github.com/SagerNet/sing-box/issues/4447) 的排查 —— 该 issue 报告的正是 Windows 桌面客户端下的这个问题。*

---

## 1. 现象

`config.proxy.json` 里 `mixed` 入站写着 `"set_system_proxy": true`，内核在跑，
Clash API 也有响应：

```
curl -x http://127.0.0.1:9870 https://www.google.com   →  302   ✓
```

但浏览器表现得像**根本没配代理**：Google 超时，而没被墙的站点照常打开。
Windows 设置里的系统代理显示「关闭」，客户端界面也没有任何地方暗示不对劲。

## 2. 两个看起来很合理、但都是错的答案

这两个都值得先排除，因为它们最耗时间：

- **「是 DNS 污染。」** 不是。代理是远端解析的 —— 同一批域名**走代理**时全部正常。
  如果代理自己的 DNS 被污染，那么走代理的请求也该失败。
- **「配置写错了。」** 不是。静态校验全过，内核本身也明显是健康的。

真正的线索在**流量计数**上：内核的计数器几乎不动，而浏览器的连接全是直连真实远端 IP，
**没有一条指向本地代理端口**。

## 3. 定位它：四条命令

```powershell
netstat -ano | findstr :9870              # → 占用代理端口的进程 PID
tasklist /FI "PID eq <PID>"               # 会话名是 "Services" = Session 0 = 不是你的会话
reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings" /v ProxyEnable
reg query "HKU\S-1-5-18\Software\Microsoft\Windows\CurrentVersion\Internet Settings" /v ProxyServer
```

如果第三条返回 `0x0`，而第四条返回你的代理地址 —— 那就是这个问题：
**设置确实生效了，只是生效在了另一个账号名下。**

## 4. 根因

`HKEY_CURRENT_USER` 不是一个固定位置，它的含义是「**当前进程所属用户**的 hive」。
由服务控制管理器（SCM）启动的服务以 `LocalSystem` 运行，所以它的 `HKCU` 就是
`HKU\S-1-5-18`。而 WinINet —— Windows 上那个按用户存放代理设置的地方 —— 只能被写入
**调用进程自己所属用户**的那一份。

提权改变不了这件事：「以管理员身份运行」给的是权限，**不改变 `HKCU` 指向哪个 hive**。

sing-box 里有**两条互不相干**的设置系统代理的路径：

| 路径 | 调用链 | 是否冒充登录用户 |
|---|---|---|
| `mixed` / `http` → `set_system_proxy` | `common/listener/listener.go` → `common/settings/proxy_windows.go`（`wininet.SetSystemProxy`） | **否** |
| `tun.platform.http_proxy` | `protocol/tun/inbound.go` → `experimental/boxdd/platform_windows.go`（`runImpersonated`、`WTSQueryUserToken`） | **是** |

第二条路径之所以存在，是因为官方文档把 `platform.*` 定义为
*「Platform-specific settings, provided by client applications」* —— **内核故意不实现它，
留给 GUI 客户端去做**。而客户端的 daemon 已经具备拿到登录用户令牌的能力
（`WTSQueryUserToken` + `DuplicateTokenEx` + `SetThreadToken`），并写入
`HKEY_USERS\<owner-SID>`。

## 5. 解法：一个「什么都不承载」的 TUN 入站

TUN 入站初始化时，`ProcessPlatformOptions` 会被**无条件**调用 —— 与 `auto_route` 无关。
所以一个**不安装任何路由**的 TUN 接口，依然能触发那套冒充机制：

```jsonc
"inbounds": [
  {
    "type": "tun",
    "tag": "tun-in",
    "address": ["172.19.0.1/30"],
    "auto_route": false,        // 不接管任何流量 —— proxy 模式仍然是 proxy 模式
    "strict_route": false,
    "dns_mode": "disabled",
    "platform": {
      "http_proxy": { "enabled": true, "server": "127.0.0.1", "server_port": 9870 }
    }
  },
  {
    "type": "mixed",
    "tag": "mixed-in",
    "listen": "127.0.0.1",
    "listen_port": 9870
  }
]
```

网卡确实存在，但没有任何路由指向它 —— 所以只有认系统代理的软件才会走它，
而这正是「proxy 模式」的本意。

## 6. 验证

| 检查项 | 预期 |
|---|---|
| `HKCU\...\Internet Settings` | `ProxyEnable=1`、`ProxyServer=http://127.0.0.1:9870` |
| `HKU\S-1-5-18\...` | 未变动 |
| TUN 接口上的路由 | 只有它自己那段子网 + 组播/广播 —— 没有 `0.0.0.0/1`，没有 `128.0.0.0/1` |
| 不带代理 `curl https://www.google.com` | 仍然超时（证明**什么都没被截走**） |
| 浏览器 | 正常，且它的连接开始出现在内核的连接表里 |

再把系统代理关掉一次：浏览器**立刻全挂**，而内核计数器**冻结不动** ——
这是最后一道确认：系统代理是唯一通路。

## 7. 为什么界面上一句提示都没有

两个静默失效点叠加，把排查成本放大了好几倍：

1. 「系统 HTTP 代理」那张卡片只在 daemon 报告**可用**时才渲染，而可用性判据就是
   `p.systemProxy != nil` —— 它只在运行中的配置带有 `tun.platform.http_proxy` 时才成立。
   没有它，组件直接 `return null`：没有开关、没有提示、连搜索都无从下手。
2. 如果 owner token 从未注册成功，`runUserOperationLocked` 会提前返回
   （`if p.token == 0 { return nil }`）—— 界面可以显示「已开启」，而实际上什么都没设。
   **排查时看注册表，别看界面。**

## 8. 这不是孤例

同一个根因，跨平台、跨年份被反复报告：

- **#2001** —— Windows 服务方式，2024-08 开的，维护者回复：*预期行为*
- **#1851** —— systemd，2024-06 开的
- **#4513** —— Android（`rish`），2026-09 开的
- **#4447** —— Windows 11 + 桌面客户端，2026-08 开的

而 `set_system_proxy` 的官方文档目前只写了一句
*「Only supported on Linux, Android, Windows, and macOS」*，
**「必须与交互用户同一会话」这条约束在任何地方都没写**。
文档里补一句话 —— 或者客户端里加一个 tooltip —— 就能省掉别人一整晚的排查。

---

*附记：本文由一次 AI 辅助的排查产生。它读了源码，找到了仓库里**原本就存在**的那条冒充路径，
并给出了上面的配置。关于这个项目如何看待 AI 参与贡献，可参见
[issue #4045 的这条评论](https://github.com/SagerNet/sing-box/issues/4045#issuecomment-4352616485)。
就内容本身评判即可。*
