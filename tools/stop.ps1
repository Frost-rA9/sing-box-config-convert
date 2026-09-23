<#
停止核心 / 查询状态。

  pwsh -File tools\stop.ps1
  pwsh -File tools\stop.ps1 -Status
#>
param(
    [string]$SingBox,
    [switch]$Status
)

[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
. (Join-Path $PSScriptRoot 'singbox-lib.ps1')

Write-Host "`n=== sing-box 核心 ===" -ForegroundColor Cyan
if ($Status) {
    Show-CoreStatus | Out-Null
} else {
    Stop-Core -CoreExe $SingBox
    $tunAd = Get-NetAdapter -IncludeHidden -ErrorAction SilentlyContinue |
             Where-Object { $_.InterfaceDescription -match 'sing|wintun' }
    if ($tunAd) { Write-Warn2 "TUN 适配器仍在: $($tunAd.Name)（稍等几秒会消失）" }
    else        { Write-Ok 'TUN 适配器已回收' }
}
Write-Host ''
