<#
.SYNOPSIS
    Install (or verify) the project's DSH skill.

.DESCRIPTION
    The canonical copy of every skill lives in this repository under skills\.
    DSH only discovers skills under its own roots (.dsh\skills or .agents\skills
    relative to the session cwd), so the repository copy must be installed.

    Direction of truth: edit skills\song-production\ in this repo, then run this
    script. Editing the installed copy directly will be overwritten. Run with
    -Check to report drift without writing anything.

.EXAMPLE
    pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-skill.ps1
.EXAMPLE
    pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-skill.ps1 -Check
#>
[CmdletBinding()]
param(
    [string] $SkillRoot = (Join-Path $PSScriptRoot '..\skills'),
    # DSH scans the project root's .dsh\skills; the project root is the parent
    # of this repository because the skill must be visible from the session cwd.
    [string] $InstallRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..\.dsh\skills') -ErrorAction SilentlyContinue),
    [switch] $Check
)

$ErrorActionPreference = 'Stop'
$SkillRoot = [System.IO.Path]::GetFullPath($SkillRoot)
if (-not $InstallRoot) { $InstallRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..\.dsh\skills')) }

if (-not (Test-Path $SkillRoot)) { throw "skills directory not found: $SkillRoot" }
$skills = Get-ChildItem $SkillRoot -Directory
if (-not $skills) { throw "no skills under $SkillRoot" }

Write-Host "source : $SkillRoot"
Write-Host "target : $InstallRoot"
Write-Host ''

$drift = 0
foreach ($skill in $skills) {
    if (-not (Test-Path (Join-Path $skill.FullName 'SKILL.md'))) {
        Write-Host ("  SKIP  {0} (no SKILL.md)" -f $skill.Name) -ForegroundColor Yellow
        continue
    }
    $dest = Join-Path $InstallRoot $skill.Name
    $changed = @()
    foreach ($f in Get-ChildItem $skill.FullName -Recurse -File) {
        $rel = $f.FullName.Substring($skill.FullName.Length).TrimStart('\')
        $other = Join-Path $dest $rel
        if (-not (Test-Path $other) -or
            (Get-FileHash $f.FullName).Hash -ne (Get-FileHash $other).Hash) {
            $changed += $rel
        }
    }
    $extra = @()
    if (Test-Path $dest) {
        foreach ($f in Get-ChildItem $dest -Recurse -File) {
            $rel = $f.FullName.Substring($dest.Length).TrimStart('\')
            if (-not (Test-Path (Join-Path $skill.FullName $rel))) { $extra += $rel }
        }
    }

    if ($Check) {
        if ($changed.Count -or $extra.Count) {
            $drift++
            Write-Host ("  DRIFT {0}: {1} changed, {2} extra" -f $skill.Name, $changed.Count, $extra.Count) -ForegroundColor Yellow
            foreach ($r in $changed) { Write-Host "          changed: $r" }
            foreach ($r in $extra)   { Write-Host "          extra  : $r" }
        } else {
            Write-Host ("  OK    {0} (in sync)" -f $skill.Name) -ForegroundColor Green
        }
        continue
    }

    New-Item -ItemType Directory -Force $dest | Out-Null
    # Clear first so a file deleted from the repo does not linger installed.
    if (Test-Path $dest) { Remove-Item (Join-Path $dest '*') -Recurse -Force -ErrorAction SilentlyContinue }
    Copy-Item (Join-Path $skill.FullName '*') $dest -Recurse -Force
    Write-Host ("  INSTALLED {0} ({1} file(s))" -f $skill.Name, (Get-ChildItem $dest -Recurse -File).Count) -ForegroundColor Green
}

Write-Host ''
if ($Check) {
    if ($drift) { Write-Host "有 $drift 个 skill 与项目不一致 —— 跑不带 -Check 的同一命令即可安装。" -ForegroundColor Yellow; exit 1 }
    Write-Host '全部与项目一致。' -ForegroundColor Green
} else {
    Write-Host '安装完成。DSH 的 skill 目录会热刷新,无需重启会话。' -ForegroundColor Green
}
