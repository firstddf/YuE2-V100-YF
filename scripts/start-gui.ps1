<#
.SYNOPSIS
    Start the YuE2 service (1414) and the Gradio GUI (7860).

.DESCRIPTION
    Two processes:
      * yue2_service.py  - job queue, stage progress, ABC export   (port 1414)
      * gui.py           - Chinese Gradio front end                (port 7860)

    Port cleanup deliberately uses netstat instead of Get-NetTCPConnection: in
    this environment that cmdlet returns nothing, which silently leaves an old
    service holding the port and makes a "restart" talk to stale code.

.EXAMPLE
    pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-gui.ps1
.EXAMPLE
    # stop both
    pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-gui.ps1 -Stop
#>
[CmdletBinding()]
param(
    [int]    $ServicePort = 1414,
    [int]    $GuiPort     = 7860,
    [string] $Python      = 'D:\mt-tool\runtime\Scripts\python.exe',
    [switch] $Stop,
    [switch] $NoService
)

$ErrorActionPreference = 'Continue'
$root = Split-Path $PSScriptRoot -Parent
$logs = Join-Path $root 'logs'

function Get-PortOwners([int] $port) {
    $out = @()
    foreach ($line in (netstat -ano | Select-String 'LISTENING')) {
        if ($line.ToString() -match ":$port\s") {
            $out += [int](($line.ToString() -split '\s+')[-1])
        }
    }
    return ($out | Sort-Object -Unique)
}

function Stop-Port([int] $port) {
    foreach ($pid_ in (Get-PortOwners $port)) {
        Write-Host ("  port {0}: killing pid {1}" -f $port, $pid_) -ForegroundColor Yellow
        Stop-Process -Id $pid_ -Force -ErrorAction SilentlyContinue
    }
}

if ($Stop) {
    Write-Host 'Stopping YuE2 service and GUI ...' -ForegroundColor Cyan
    Stop-Port $ServicePort
    Stop-Port $GuiPort
    Get-Process audiocpp_cli -ErrorAction SilentlyContinue | ForEach-Object {
        Write-Host ("  killing engine pid {0}" -f $_.Id) -ForegroundColor Yellow
        Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
    }
    Write-Host 'Stopped.'
    exit 0
}

if (-not (Test-Path $Python)) { throw "Python not found: $Python" }

# Engine lives in different places depending on layout:
#   source tree -> build\bin\Release\audiocpp_cli.exe
#   release zip -> bin\audiocpp_cli.exe
# Same resolution order as yue2_service.py's _find_engine(); keep the two in sync.
$engine = $null
foreach ($e in @((Join-Path $root 'build\bin\Release\audiocpp_cli.exe'),
                  (Join-Path $root 'bin\audiocpp_cli.exe'),
                  (Join-Path $root 'build\bin\audiocpp_cli.exe'))) {
    if (Test-Path $e) { $engine = $e; break }
}
if (-not $engine) {
    throw 'audiocpp_cli.exe not found (looked in build\bin\Release, bin, build\bin). Build it, or use the release zip.'
}

New-Item -ItemType Directory -Path $logs -Force | Out-Null

if (-not $NoService) {
    Write-Host 'Starting YuE2 service ...' -ForegroundColor Cyan
    Stop-Port $ServicePort
    Start-Sleep -Milliseconds 500
    # -u: unbuffered. Without it Python block-buffers stdout when redirected to a
    # file, so logs\service.out.log stays empty (0 bytes) for the whole run and is
    # useless for live diagnosis.
    Start-Process -FilePath $Python `
        -ArgumentList @('-u', (Join-Path $root 'app\yue2_service.py'), '--port', $ServicePort) `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logs 'service.out.log') `
        -RedirectStandardError  (Join-Path $logs 'service.err.log') | Out-Null

    $ready = $false
    foreach ($i in 1..30) {
        Start-Sleep -Milliseconds 500
        try {
            $h = Invoke-RestMethod "http://127.0.0.1:$ServicePort/api/health" -TimeoutSec 3
            Write-Host ("  service ready: engine={0} model={1}" -f $h.engine_exists, $h.model_exists) -ForegroundColor Green
            $ready = $true
            break
        } catch { }
    }
    if (-not $ready) { throw "Service did not become ready on $ServicePort - see logs\service.err.log" }
}

Write-Host 'Starting Gradio GUI ...' -ForegroundColor Cyan
Stop-Port $GuiPort
Start-Sleep -Milliseconds 500
Write-Host ''
Write-Host ("  GUI      ->  http://127.0.0.1:{0}" -f $GuiPort) -ForegroundColor Green
Write-Host ("  service  ->  http://127.0.0.1:{0}" -f $ServicePort)
Write-Host ("  stop     ->  pwsh -File .\scripts\start-gui.ps1 -Stop")
Write-Host ''
& $Python (Join-Path $root 'app\gui.py') --service "http://127.0.0.1:$ServicePort" --port $GuiPort
