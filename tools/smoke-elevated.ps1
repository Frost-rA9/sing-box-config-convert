<#
提权运行冒烟测试，并把全部输出落到 out\smoke.log。

为什么单独一个脚本：PowerShell 的 Start-Process -Verb RunAs 不能和
-RedirectStandardOutput 同时使用，所以由本脚本自己把输出写文件，
调用方只需轮询 out\smoke.log 里的 "### EXIT=" 结束标记。

  Start-Process pwsh -Verb RunAs -ArgumentList @('-NoProfile','-File','<此文件>')
#>
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$root = Split-Path $PSScriptRoot -Parent
$log = Join-Path $root 'out\smoke.log'

Remove-Item $log -Force -ErrorAction SilentlyContinue
"### START $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" | Out-File $log -Encoding utf8

& pwsh -NoProfile -File (Join-Path $PSScriptRoot 'smoke.ps1') @args *>&1 |
    Out-File -FilePath $log -Encoding utf8 -Append

"### EXIT=$LASTEXITCODE  END $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" |
    Out-File $log -Encoding utf8 -Append
