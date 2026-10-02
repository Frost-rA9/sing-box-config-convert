<#
启动普通模式（config.proxy.json）—— 无 TUN，只开 mixed 入站，不需要管理员权限。

  pwsh -File tools\start-proxy.ps1
  pwsh -File tools\start-proxy.ps1 -SetSystemProxy     # 顺带把系统代理指到配置里的 mixed 入站
  pwsh -File tools\start-proxy.ps1 -ClearSystemProxy   # 关闭系统代理
#>
param(
    [string]$SingBox,
    [string]$Config,
    [switch]$SetSystemProxy,
    [switch]$ClearSystemProxy
)

[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
. (Join-Path $PSScriptRoot 'singbox-lib.ps1')

if (-not $Config) { $Config = Join-Path $script:OutDir 'config.proxy.json' }
Write-Host "`n=== 启动普通模式（无 TUN）===" -ForegroundColor Cyan
$ok = Start-Core -Config $Config -Mode 'proxy' -CoreExe $SingBox

$listen = (Get-Content -Raw $Config | ConvertFrom-Json).inbounds |
          Where-Object { $_.type -eq 'mixed' }
$addr = if ($listen) { "$($listen.listen):$($listen.listen_port)" } else { $null }

if ($ClearSystemProxy) {
    Clear-SystemProxy
    Write-Ok '系统代理已关闭'
}
if ($ok -and $SetSystemProxy) {
    if ($addr) {
        Set-SystemProxy $addr
        Write-Ok "系统代理已设为 $addr"
        Write-Warn2 '注意：这会覆盖其它代理客户端设置的系统代理'
    } else {
        Write-Bad '配置里没有 mixed 入站，无法设置系统代理'
    }
}

if ($ok) {
    if ($addr) {
        Write-Info "mixed 入站在 $addr（HTTP/SOCKS5 同端口）"
        Write-Info "终端/curl/WSL 需显式指代理: `$env:HTTP_PROXY=`"http://$addr`""
        if (-not $SetSystemProxy) { Write-Info "浏览器需要系统代理，加 -SetSystemProxy 或手动设置" }
    }
    Write-Host "`n切换到 TUN 模式: pwsh -File tools\start-tun.ps1" -ForegroundColor DarkGray
    Write-Host "完全停止:        pwsh -File tools\stop.ps1`n" -ForegroundColor DarkGray
}
exit ([int](-not $ok))
