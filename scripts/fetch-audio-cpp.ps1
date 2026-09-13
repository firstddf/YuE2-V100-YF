<#
.SYNOPSIS
    Fetch audio.cpp at the exact revision this project is built against, and
    apply this project's patches.

.DESCRIPTION
    audio.cpp is a third-party project (Apache-2.0, Copyright 2026 ShugoAI LLC)
    and is NOT vendored in this repository. This script reconstructs the tree
    this project builds from:

      1. clone https://github.com/0xShug0/audio.cpp at the pinned commit
      2. apply patches/*.patch
      3. verify the result matches what the patches claim

    Why the commit is pinned: the project tracks the `dev` branch, which moves.
    A build recipe that says "clone dev" is not reproducible, and for GPL
    purposes the Corresponding Source must correspond to the exact binary that
    was distributed — not to whatever upstream looks like today.

    The patches are ordinary git patches (git apply), not anchor-matching
    scripts, so they either apply exactly or fail loudly.

.EXAMPLE
    pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\fetch-audio-cpp.ps1
.EXAMPLE
    # only show what would happen
    pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\fetch-audio-cpp.ps1 -DryRun
#>
[CmdletBinding()]
param(
    [string] $Repo     = 'https://github.com/0xShug0/audio.cpp.git',
    [string] $Commit   = '87544b57242f52c7e7d3dee5766fe82eacf6a321',
    [string] $Target   = (Join-Path $PSScriptRoot '..\audio.cpp'),
    [switch] $DryRun
)

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$patchDir = Join-Path $root 'patches'
$Target = [System.IO.Path]::GetFullPath($Target)

if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw 'git not found on PATH' }
if (-not (Test-Path $patchDir)) { throw "patches directory not found: $patchDir" }
$patches = Get-ChildItem $patchDir -Filter '*.patch' | Sort-Object Name
if (-not $patches) { throw "no .patch files under $patchDir" }

Write-Host "repo    : $Repo"
Write-Host "commit  : $Commit"
Write-Host "target  : $Target"
Write-Host "patches : $($patches.Count)"
Write-Host ''

foreach ($p in $patches) { Write-Host ("  {0}  ({1} bytes)" -f $p.Name, $p.Length) }
Write-Host ''

if ($DryRun) { Write-Host 'DryRun: 未执行任何操作。' -ForegroundColor Yellow; exit 0 }

if (Test-Path (Join-Path $Target '.git')) {
    Write-Host "目标已是 git 检出,跳过克隆。" -ForegroundColor Yellow
    $head = (& git -C $Target rev-parse HEAD).Trim()
    if ($head -ne $Commit) {
        throw "已有检出的 HEAD 是 $head,与固定的 $Commit 不符。请手动处理(换个 -Target,或删掉重建)。"
    }
} else {
    if (Test-Path $Target) { throw "目标已存在但不是 git 检出:$Target。请先移走它。" }
    Write-Host '正在克隆(浅克隆单个提交,约 200 MB)…' -ForegroundColor Cyan
    & git clone --filter=blob:none --no-checkout $Repo $Target
    if ($LASTEXITCODE -ne 0) { throw 'git clone 失败' }
    & git -C $Target checkout $Commit
    if ($LASTEXITCODE -ne 0) { throw "checkout $Commit 失败" }
}

# 先 --check:不写任何文件就能知道补丁是否能干净应用
foreach ($p in $patches) {
    & git -C $Target apply --check $p.FullName 2>&1 | Out-String | Write-Verbose
    if ($LASTEXITCODE -ne 0) {
        # 可能已经打过了 —— 反向能过就说明如此
        & git -C $Target apply --check --reverse $p.FullName 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Host ("  已应用  {0}" -f $p.Name) -ForegroundColor Yellow
            continue
        }
        throw "补丁无法应用:$($p.Name)(既不能正向也不能反向 —— 上游可能变了)"
    }
    & git -C $Target apply $p.FullName
    if ($LASTEXITCODE -ne 0) { throw "应用失败:$($p.Name)" }
    Write-Host ("  已应用  {0}" -f $p.Name) -ForegroundColor Green
}

Write-Host ''
Write-Host '=== 结果 ===' -ForegroundColor Cyan
& git -C $Target diff --stat
Write-Host ''
Write-Host '下一步:scripts\check-env.ps1 → scripts\configure.ps1 → 编译(见 README)。' -ForegroundColor Yellow
