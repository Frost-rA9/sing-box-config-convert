# SFA（sing-box for Android）配置踩坑记录

> 桌面端的系统代理问题见 [`system-proxy-case-study.zh.md`](system-proxy-case-study.zh.md)。
> 本文只记 Android 特有的四条，对应 `AGENTS.md` 的 **I19**，并由 `tools/verify.py` 机器校验。

---

## 1. `configure tun interface: Bad address` —— 不能排除回环地址

**现象**：SFA 点启动即报

```
start or reload service: start inbound/tun[tun-in]: configure tun interface: Bad address
```

**原因**：SFA 把 `route_exclude_address` 逐条交给 Android 的 `VpnService.Builder.excludeRoute()`，
而 Android 的 `Builder.check()` 里有一句：

```java
if (address.isLoopbackAddress()) throw new IllegalArgumentException("Bad address");
```

Java 的 `isLoopbackAddress()` 对**整个 `127.0.0.0/8`** 都返回 true（不只是 `127.0.0.1`），
所以列表里只要有 `127.0.0.0/8` 就会在第一条抛异常。

**修复**：从 `route_exclude_address` 里去掉 `127.0.0.0/8`（以及 IPv6 的 `::1/128`）。
回环流量本来就不会进 VPN，去掉零语义损失。

**同类报告**：[sing-box#2030](https://github.com/SagerNet/sing-box/issues/2030)（Android，2024-08）
评论区确认：`127.0.0.0/8` 与 `::1/128` 在 Android 上都会触发；**Windows 没有这个限制**。

---

## 2. VPN 起来了但「国内正常、境外全挂」—— 不要用 `route_exclude_address_set`

**现象**：VPN 图标正常，国内 App 一切正常，境外全挂（Google 打不开、Pixiv 加载失败）。

**日志给出的判据**（进 TUN 的连接目标）：

| | 有 `route_exclude_address_set` | 去掉之后 |
|---|---|---|
| 进 TUN 的 TCP | 18 条，**全是 CN IPv6** | **70 条** |
| 境外 TCP 目标 | **0 条** | Google / Fastly / GCP / Akamai / CloudFront 都出现 |
| 错误 | 10 条 | 2 条 |

也就是说：**IPv4 默认路由没有建立起来** —— 只有 VPN 自带网段（`172.19.0.0/30`）被路由进去，
所以 DNS（目标是 `172.19.0.2`）正常、国内 IPv4 直连正常、**境外 IPv4 全部绕过 VPN 走直连** → 被墙。

**原因**：`geoip-cn` 会被展开成**几千条 CIDR** 再逐条 `excludeRoute()`，
超出 Android `VpnService.Builder` 的路由表规模限制；而且没有 `initial_path` 时，
还得先下载 `geoip-cn` 才能展开，拖慢建 VPN。

**上游行为**：`protocol/tun/inbound.go` 里这个展开是**有条件**的：

```go
if t.autoRedirect != nil || t.platformInterface == nil || C.IsWindows {
    ... routeExcludeAddressSet = append(...)
}
```

即只在 Linux `auto_redirect`、**裸核 CLI**、或 Windows 下才展开。
**GUI 客户端（SFA/SFW/SFM）提供平台接口 → 该字段被跳过** —— 这与
[sing-box#3191](https://github.com/SagerNet/sing-box/issues/3191)（iOS/macOS 报"不生效"）一致。

**修复**：不要用 `route_exclude_address_set`。CN 流量进内核后由路由规则判直连
（`geosite-cn` / `geoip-cn` → `Direct`），结果一样，只是多一层转发。

---

## 3. rule-set 的 `initial_path` 在 Android 上不存在

桌面产物里 `initial_path` 是本机绝对路径（`D:/.../bootstrap/xxx.srs`），Android 上不存在。
**去掉该字段**即可：内核改为直接从 `url` 下载（走 `http_clients` 的 `detour`，所以能通），
代价只是首次启动慢几秒，之后由 `cache_file` 缓存。

---

## 4. mixed 入站不要开 `set_system_proxy`

Android 上设系统代理需要特权（`WRITE_SECURE_SETTINGS` / Shizuku / root），
而且 [sing-box#4513](https://github.com/SagerNet/sing-box/issues/4513) 报告在 SFA + `rish` 下也不工作。
Android 走 VPN/TUN 语义，这个字段直接去掉。

---

## 用法

1. 把生成的 `config.android.json` 传到手机，SFA → 配置 → 新建 → 从文件导入
2. 首次启动会慢几秒（下载 16 个 rule-set）
3. 若设备有异常，可调 `policy.json` 的 `inbounds.tun_android`：`stack`（`system` → `gvisor`）、
   `mtu`（`9000` → `1500`）

> 顺带一个已知的、与代理无关的现象：运营商 IPv6 服务（如电信 RCS/IMS，`240e::/20`）
> 在当前网络下走 `Direct` 会超时（`dial wlan0: i/o timeout`），因为 sing-box 把直连拨号绑定到了
> 默认网卡，而那些服务只在该运营商的接入网内可达。
