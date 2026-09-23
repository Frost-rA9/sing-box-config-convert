<#
运行时冒烟测试 —— 临时起核心，验证运行期不变量（需要管理员）。

静态不变量请用 python tools\verify.py（不需要内核）。

  pwsh -File tools\smoke.ps1                    # 默认测 config.tun.json
  pwsh -File tools\smoke.ps1 -Config out\config.proxy.json
#>
param(
    [string]$SingBox,
    [string]$Config
)

[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
. (Join-Path $PSScriptRoot 'singbox-lib.ps1')

if (-not $Config) { $Config = Join-Path $script:OutDir 'config.tun.json' }
if (-not (Test-Path $Config)) { Write-Bad "找不到配置: $Config"; exit 1 }
if (-not (Test-Admin)) { Write-Bad '需要管理员权限（创建 wintun 适配器）'; exit 1 }
if (Show-CoreStatus) { Write-Bad '已有核心在跑，先 pwsh -File tools\stop.ps1'; exit 1 }

$fail = 0
$cfg = Get-Content -Raw $Config | ConvertFrom-Json
$hasTun = [bool]($cfg.inbounds | Where-Object { $_.type -eq 'tun' })

Write-Host "`n=== 运行时冒烟: $(Split-Path $Config -Leaf) ===" -ForegroundColor Cyan
if (-not (Start-Core -Config $Config -Mode 'smoke' -CoreExe $SingBox)) { exit 1 }

try {
    if ($hasTun) {
        $tunAd = Get-NetAdapter -IncludeHidden -ErrorAction SilentlyContinue |
                 Where-Object { $_.InterfaceDescription -match 'sing|wintun' }
        if ($tunAd) { Write-Ok "TUN 适配器: $($tunAd.Name) ($($tunAd.Status))" }
        else { Write-Bad '没有 TUN 适配器'; $fail++ }

        # I2：WSL/Hyper-V 网段不能被 TUN 抢走
        $r = Get-NetRoute -AddressFamily IPv4 -ErrorAction SilentlyContinue |
             Where-Object { $_.DestinationPrefix -eq '172.27.176.0/20' }
        if ($r) { Write-Ok 'WSL/Hyper-V 网段 172.27.176.0/20 仍在路由表' }
        else { Write-Warn2 '172.27.176.0/20 不在路由表（WSL 未启动时正常）' }

        # I1/I3 的运行期体现：wsl 不能卡死
        Write-Host '    ... 测试 wsl 是否卡住（10s 超时）'
        $job = Start-Job { wsl.exe -l -v 2>&1 }
        if (Wait-Job $job -Timeout 10) {
            Write-Ok 'wsl 正常响应（未复现卡死）'
        } else {
            Write-Bad 'wsl 10 秒无响应 —— 卡死复现'; $fail++; Stop-Job $job
        }
        Remove-Job $job -Force

        # I3：系统解析器应已被 dns_mode=hijack 接管，且不能被污染
        $g = (Resolve-DnsName www.google.com -Type A -ErrorAction SilentlyContinue |
              Where-Object { $_.IPAddress }).IPAddress
        $ranges = @('142.250.', '142.251.', '172.217.', '216.58.', '74.125.', '108.177.', '173.194.', '209.85.')
        if (-not $g) { Write-Bad 'www.google.com 解析失败'; $fail++ }
        elseif ($g | Where-Object { $ip = $_; -not ($ranges | Where-Object { $ip.StartsWith($_) }) }) {
            Write-Bad "www.google.com -> $($g -join ',') 不在 Google 网段 —— DNS 仍被污染"; $fail++
        } else { Write-Ok "DNS 未被污染: www.google.com -> $($g -join ',')" }
    }

    # 模式档位：Clash API 的 mode-list 由 clash_api.default_mode + clash_mode 拼出
    $api = Invoke-RestMethod -Uri 'http://127.0.0.1:9090/configs' -TimeoutSec 5 -ErrorAction SilentlyContinue
    if ($api) {
        $ml = @($api.'mode-list')
        if ($ml.Count -ge 2) { Write-Ok "模式档位: $($ml -join ' / ')（当前 $($api.mode)）" }
        else { Write-Warn2 "mode-list 只有 $($ml.Count) 项" }
    } else { Write-Warn2 'Clash API 不可达 (127.0.0.1:9090)' }

    # 策略组可见性
    $px = Invoke-RestMethod -Uri 'http://127.0.0.1:9090/proxies' -TimeoutSec 5 -ErrorAction SilentlyContinue
    if ($px) {
        $groups = @($px.proxies.PSObject.Properties | Where-Object { $_.Value.type -in 'Selector', 'URLTest' })
        Write-Ok "策略组 $($groups.Count) 个: $(($groups | ForEach-Object { $_.Name }) -join ', ')"
    }
} finally {
    Stop-Core -CoreExe $SingBox
}

Write-Host "`n冒烟完成，失败项: $fail`n" -ForegroundColor Cyan
exit $fail
