# Why `set_system_proxy` silently does nothing when sing-box runs as a Windows service

*And a config-only workaround that needs no code change.*

**TL;DR** — WinINet proxy settings are per-user, and the desktop client's daemon runs as
`LocalSystem`. So `set_system_proxy` on a `mixed`/`http` inbound writes to
`HKU\S-1-5-18` — the service account's hive — and the logged-on user's browser never
sees it. sing-box already contains an impersonation path for `tun.platform.http_proxy`;
attaching a **non-routing** TUN inbound that only carries that field makes the system
proxy work, while capturing zero traffic.

*Source: this write-up comes from the investigation in
[#4447](https://github.com/SagerNet/sing-box/issues/4447), reported against the Windows
desktop client.*

---

## 1. The symptom

`config.proxy.json` with `"set_system_proxy": true` on the `mixed` inbound, core
running, Clash API answering:

```
curl -x http://127.0.0.1:9870 https://www.google.com   →  302   ✓
```

…but the browser behaves as if no proxy were configured at all: Google times out,
while sites that are not blocked load normally. The system proxy in Windows Settings
reads "off", and nothing in the client's UI suggests otherwise.

## 2. Two plausible wrong answers

Both are worth ruling out first, because they cost the most time:

- **"It's DNS pollution."** It isn't. The proxy resolves remotely — requests that go
  *through* the core succeed for the same domains. If the proxy's own DNS were
  poisoned, through-proxy requests would fail too.
- **"The config is wrong."** It isn't. `verify`-style checks pass and the core is
  demonstrably healthy.

The give-away is traffic accounting: the core's counters barely move, and the
browser's sockets go to real remote IPs — none to the local proxy port.

## 3. Localizing it: four commands

```powershell
netstat -ano | findstr :9870              # → PID of the process holding the proxy port
tasklist /FI "PID eq <PID>"               # session name "Services" = session 0 = NOT your session
reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings" /v ProxyEnable
reg query "HKU\S-1-5-18\Software\Microsoft\Windows\CurrentVersion\Internet Settings" /v ProxyServer
```

If the first returns `0x0` while the second returns your proxy address, you have this
bug: the setting was applied — to the wrong account.

## 4. Root cause

`HKEY_CURRENT_USER` is not a fixed place; it is "the hive of the user this process
belongs to". A service started by the Service Control Manager runs as `LocalSystem`,
so its `HKCU` is `HKU\S-1-5-18`. WinINet — the per-user proxy store on Windows — can
only be written for the calling process's own user. Nothing about elevation changes
that: "run as administrator" grants privileges, it does not change which hive `HKCU`
points at.

sing-box contains **two independent paths** for setting the system proxy:

| path | call chain | impersonates the logged-on user? |
|---|---|---|
| `mixed` / `http` → `set_system_proxy` | `common/listener/listener.go` → `common/settings/proxy_windows.go` (`wininet.SetSystemProxy`) | **no** |
| `tun.platform.http_proxy` | `protocol/tun/inbound.go` → `experimental/boxdd/platform_windows.go` (`runImpersonated`, `WTSQueryUserToken`) | **yes** |

The second path exists because `platform.*` is documented as
*"Platform-specific settings, provided by client applications"* — the core does not
implement it, the GUI client does. The client's daemon already knows how to obtain the
logged-on user's token (`WTSQueryUserToken` + `DuplicateTokenEx` + `SetThreadToken`)
and write to `HKEY_USERS\<owner-SID>`.

## 5. Workaround: a TUN inbound that carries nothing

`ProcessPlatformOptions` is called unconditionally while a TUN inbound initializes —
independently of `auto_route`. So a TUN interface that installs **no routes** still
triggers the impersonated system-proxy setup:

```jsonc
"inbounds": [
  {
    "type": "tun",
    "tag": "tun-in",
    "address": ["172.19.0.1/30"],
    "auto_route": false,        // no traffic capture — proxy mode stays proxy mode
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

The interface exists but nothing is routed into it, so only applications that honour
the system proxy use it — which is the point of "proxy mode".

## 6. Verification

| check | expected |
|---|---|
| `HKCU\...\Internet Settings` | `ProxyEnable=1`, `ProxyServer=http://127.0.0.1:9870` |
| `HKU\S-1-5-18\...` | unchanged |
| routes on the TUN interface | only its own subnet, multicast and broadcast — no `0.0.0.0/1`, no `128.0.0.0/1` |
| `curl https://www.google.com` with no proxy | still times out (proves nothing is captured) |
| browser | works, and its sockets now appear in the core's connection table |

Turning the system proxy off again makes the browser fail immediately, while the
core's counters freeze — the last confirmation that the system proxy is the only
path in use.

## 7. Why the UI never says anything

Two silent failure modes compound the problem:

1. The "System HTTP Proxy" card is rendered only when the daemon reports the feature
   as available, and availability is literally `p.systemProxy != nil` — which is only
   true when the running config carries `tun.platform.http_proxy`. Without it the
   component returns `null`: no switch, no message, nothing to search for.
2. If the owner token was never registered, `runUserOperationLocked` returns early
   (`if p.token == 0 { return nil }`) — the UI can read "enabled" while nothing has
   been set. Check the registry, not the UI.

## 8. This is not a one-off

The same root cause has been reported across platforms and years:

- #2001 — Windows service, opened 2024-08, maintainer reply: *expected behaviour*
- #1851 — systemd, opened 2024-06
- #4513 — Android (`rish`), opened 2026-09
- #4447 — Windows 11 + the desktop client, opened 2026-08

The `set_system_proxy` documentation currently says only *"Only supported on Linux,
Android, Windows, and macOS"*; the same-session constraint is not mentioned anywhere.
One sentence there — or one tooltip in the client — would replace an entire evening of
debugging.

---

*Postscript: this write-up is the output of an AI-assisted debugging session. It read
the source, found the impersonation path that was already in the tree, and produced the
config above. For context on how AI-assisted contributions are received in this
project, see [issue #4045, comment](https://github.com/SagerNet/sing-box/issues/4045#issuecomment-4352616485).
Judge it on the content.*
