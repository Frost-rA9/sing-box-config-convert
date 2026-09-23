<#
启动 TUN 模式（config.tun.json）—— 需要管理员权限，脚本会自动提权。

  pwsh -File tools\start-tun.ps1
  pwsh -File tools\start-tun.ps1 -ClearSystemProxy   # 顺手清掉残留的系统代理（推荐）
#>
param(
    [string]$SingBox,
    [string]$Config,
    [switch]$ClearSystemProxy
)

[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
. (Join-Path $PSScriptRoot 'singbox-lib.ps1')

if (-not (Test-Admin)) {
    Write-Host '需要管理员权限（创建 wintun 适配器），正在提权...' -ForegroundColor Yellow
    $args = @('-NoProfile', '-File', "`"$PSCommandPath`"")
    if ($SingBox) { $args += @('-SingBox', "`"$SingBox`"") }
    if ($ClearSystemProxy) { $args += '-ClearSystemProxy' }
    Start-Process pwsh -Verb RunAs -ArgumentList $args | Out-Null
    exit 0
}

if (-not $Config) { $Config = Join-Path $script:OutDir 'config.tun.json' }
Write-Host "`n=== 启动 TUN 模式 ===" -ForegroundColor Cyan

if ($ClearSystemProxy) {
    Clear-SystemProxy
    Write-Ok '系统代理已关闭（TUN 模式不需要它）'
}

$ok = Start-Core -Config $Config -Mode 'tun' -CoreExe $SingBox

if ($ok) {
    Start-Sleep -Seconds 2
    $tunAd = Get-NetAdapter -IncludeHidden -ErrorAction SilentlyContinue |
             Where-Object { $_.InterfaceDescription -match 'sing|wintun' }
    if ($tunAd) { Write-Ok "TUN 适配器: $($tunAd.Name) ($($tunAd.Status))" }
    else        { Write-Warn2 '还没看到 TUN 适配器，稍等几秒或看 out\core.err.log' }

    Test-SystemProxySanity | Out-Null

    # DNS 污染自检：TUN 模式下系统解析器应已被 dns_mode=hijack 接管
    $g = (Resolve-DnsName www.google.com -Type A -ErrorAction SilentlyContinue |
          Where-Object { $_.IPAddress }).IPAddress
    if ($g) {
        $googleRanges = @('142.250.', '142.251.', '172.217.', '216.58.', '74.125.', '108.177.', '173.194.', '209.85.')
        $bad = $g | Where-Object { $ip = $_; -not ($googleRanges | Where-Object { $ip.StartsWith($_) }) }
        if ($bad) { Write-Warn2 "www.google.com -> $($g -join ',') 不在 Google 网段，DNS 可能仍被污染" }
        else      { Write-Ok "DNS 正常: www.google.com -> $($g -join ',')" }
    }

    Write-Host "`n切换回普通模式: pwsh -File tools\start-proxy.ps1" -ForegroundColor DarkGray
    Write-Host "完全停止:       pwsh -File tools\stop.ps1`n" -ForegroundColor DarkGray
}
exit ([int](-not $ok))
