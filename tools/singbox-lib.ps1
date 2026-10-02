<#
共享函数：定位内核、启停核心、状态查询。
被 start-tun.ps1 / start-proxy.ps1 / stop.ps1 点源引用。
#>

[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$script:Root        = Split-Path $PSScriptRoot -Parent
$script:OutDir      = Join-Path $script:Root 'out'
$script:PidFile     = Join-Path $script:OutDir 'core.pid'
$script:StateFile   = Join-Path $script:OutDir 'core.state.json'
# 内核路径解析顺序：$env:SINGBOX_CORE → tools/core.local（不入库，单行写绝对路径）→ PATH 里的 sing-box
$script:LocalCoreFile = Join-Path $PSScriptRoot 'core.local'

function Write-Ok   ($m) { Write-Host "  [ok]   $m" -ForegroundColor Green }
function Write-Bad  ($m) { Write-Host "  [FAIL] $m" -ForegroundColor Red }
function Write-Info ($m) { Write-Host "  [i]    $m" -ForegroundColor Cyan }
function Write-Warn2($m) { Write-Host "  [warn] $m" -ForegroundColor Yellow }

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal $id).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

# ---------------------------------------------------------------- 系统代理
$script:IePath = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings'

function Get-SystemProxyState {
    $ie = Get-ItemProperty $script:IePath -ErrorAction SilentlyContinue
    $enabled = ($ie.ProxyEnable -eq 1)
    $server = $ie.ProxyServer
    $listening = $null
    if ($enabled -and $server) {
        # 兼容 "host:port" 和 "http=host:port;https=host:port" 两种写法
        $hp = ($server -split '=')[-1].Trim()
        $port = 0
        if ([int]::TryParse(($hp -split ':')[-1], [ref]$port)) {
            $listening = [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
        }
    }
    [pscustomobject]@{ Enabled = $enabled; Server = $server; Listening = $listening }
}

function Set-SystemProxy([string]$Server) {
    Set-ItemProperty $script:IePath -Name ProxyEnable -Value 1
    Set-ItemProperty $script:IePath -Name ProxyServer -Value $Server
}

function Clear-SystemProxy {
    Set-ItemProperty $script:IePath -Name ProxyEnable -Value 0
}

# 残留的死代理会让浏览器在 TUN 已接管的情况下依然打不开网页：
# 127.0.0.0/8 被 route_exclude_address 排除，浏览器连本地死端口 TUN 管不到。
function Test-SystemProxySanity {
    $s = Get-SystemProxyState
    if ($s.Enabled -and $s.Listening -eq $false) {
        Write-Warn2 "系统代理开着但指向 $($s.Server)，该端口没有监听 —— 浏览器会直接失败"
        Write-Info  '清理办法：重跑本脚本并加 -ClearSystemProxy'
        return $false
    }
    if ($s.Enabled) { Write-Info "系统代理: $($s.Server)（端口有监听）" }
    else            { Write-Info '系统代理: 未启用（TUN 模式下不需要）' }
    return $true
}

function Get-CoreExe {
    param([string]$Path)
    if ($Path) { return $Path }
    if ($env:SINGBOX_CORE) { return $env:SINGBOX_CORE }
    if (Test-Path $script:LocalCoreFile) {
        $local = (Get-Content $script:LocalCoreFile -Raw).Trim()
        if ($local) { return $local }
    }
    $found = Get-Command sing-box -ErrorAction SilentlyContinue
    if ($found) { return $found.Source }
    throw "找不到 sing-box 内核。三选一：设置 `$env:SINGBOX_CORE、创建 tools/core.local（单行写 sing-box.exe 绝对路径）、或把 sing-box 加进 PATH。"
}

# 找到由本脚本启动的核心进程（优先 pid 文件，其次按可执行文件路径兜底）
function Get-CoreProcess {
    param([string]$CoreExe)

    if (Test-Path $script:PidFile) {
        $pidText = (Get-Content $script:PidFile -Raw).Trim()
        $proc = Get-Process -Id ([int]$pidText) -ErrorAction SilentlyContinue
        if ($proc) { return $proc }
        Remove-Item $script:PidFile -Force -ErrorAction SilentlyContinue
    }
    if ($CoreExe) {
        $byPath = Get-Process -Name 'sing-box' -ErrorAction SilentlyContinue |
                  Where-Object { $_.Path -eq $CoreExe }
        if ($byPath) { return @($byPath)[0] }
    }
    return $null
}

function Stop-Core {
    param([string]$CoreExe)

    $proc = Get-CoreProcess -CoreExe $CoreExe
    if (-not $proc) {
        Write-Info '没有正在运行的核心'
        Remove-Item $script:PidFile -Force -ErrorAction SilentlyContinue
        return
    }
    Write-Info "停止核心 pid=$($proc.Id) ..."
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    # 等它真的退出（TUN 适配器需要时间回收）
    for ($i = 0; $i -lt 30; $i++) {
        if (-not (Get-Process -Id $proc.Id -ErrorAction SilentlyContinue)) { break }
        Start-Sleep -Milliseconds 200
    }
    Remove-Item $script:PidFile -Force -ErrorAction SilentlyContinue
    Write-Ok '核心已停止'
}

function Show-CoreStatus {
    $proc = Get-CoreProcess
    if (-not $proc) { Write-Info '核心未运行'; return $false }
    $state = if (Test-Path $script:StateFile) {
        Get-Content -Raw $script:StateFile | ConvertFrom-Json
    } else { $null }
    Write-Ok "核心运行中 pid=$($proc.Id)"
    if ($state) { Write-Info "模式=$($state.mode)  配置=$($state.config)  启动于 $($state.started)" }
    return $true
}

# 启动核心。$Mode 仅用于状态记录；$Config 是实际使用的配置文件。
function Start-Core {
    param(
        [Parameter(Mandatory)][string]$Config,
        [Parameter(Mandatory)][string]$Mode,
        [string]$CoreExe
    )

    $CoreExe = Get-CoreExe $CoreExe
    if (-not (Test-Path $CoreExe)) { Write-Bad "找不到内核: $CoreExe"; return $false }
    if (-not (Test-Path $Config))  { Write-Bad "找不到配置: $Config（先跑 python tools\convert.py）"; return $false }

    if (Show-CoreStatus) {
        Write-Info '先停掉旧核心（切换模式）'
        Stop-Core -CoreExe $CoreExe
    }

    $outLog = Join-Path $script:OutDir 'core.out.log'
    $errLog = Join-Path $script:OutDir 'core.err.log'
    Remove-Item $outLog, $errLog -Force -ErrorAction SilentlyContinue

    Write-Info "启动: sing-box run -c $Config"
    # -WorkingDirectory 是保险：rule-set 的 path 已用绝对路径，但 CWD 设在项目根更安全
    $proc = Start-Process -FilePath $CoreExe -ArgumentList @('run', '-c', $Config) `
        -WorkingDirectory $script:Root `
        -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput $outLog -RedirectStandardError $errLog

    Start-Sleep -Seconds 4
    if ($proc.HasExited) {
        Write-Bad "核心启动失败，exit=$($proc.ExitCode)"
        if (Test-Path $errLog) {
            Write-Host "  ---- core.err.log ----" -ForegroundColor DarkGray
            Get-Content $errLog | Select-Object -Last 15 | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
        }
        return $false
    }

    $proc.Id | Out-File -FilePath $script:PidFile -Encoding ascii -NoNewline
    @{ mode = $Mode; config = $Config; pid = $proc.Id
       started = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss') } |
        ConvertTo-Json | Out-File -FilePath $script:StateFile -Encoding utf8

    Write-Ok "核心已启动 pid=$($proc.Id)  模式=$Mode"
    if (Test-Path $errLog) {
        Get-Content $errLog | Select-Object -First 8 |
            Where-Object { $_ } | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
    }
    return $true
}
