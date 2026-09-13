<#
.SYNOPSIS
    yue2-V100 preflight check (read-only).

.DESCRIPTION
    Verifies that this Windows host can BUILD audio.cpp from source for a
    Tesla V100 (sm_70 + CUDA 12.8) and then RUN YuE2 from GGUF weights.

    Prints a PASS / WARN / FAIL table and writes a JSON report into results\.
    Nothing outside the results\ folder is touched. Safe to re-run.

.NOTES
    Why this script looks unusual:
      * Get-CimInstance / WMI are NOT used - they are blocked in some sandboxes.
      * wmic is NOT used - it is absent on this host.
      * Host RAM is read via Get-Counter '\Memory\Available MBytes'.
      * All strings are ASCII so the file is safe for both powershell.exe (5.1)
        and pwsh (7.x) regardless of BOM.
#>
[CmdletBinding()]
param(
    [string] $ResultDir    = (Join-Path $PSScriptRoot '..\results'),
    [int]    $MinFreeGB    = 20,
    [int]    $WarnFreeGB   = 12,
    [string] $TargetDrive  = 'D',
    [string] $CudaRoot     = 'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA',
    [string] $VsRoot       = 'C:\Program Files (x86)\Microsoft Visual Studio',
    [int]    $NetTimeoutMs = 3000
)

$ErrorActionPreference = 'Continue'
$ResultDir = [System.IO.Path]::GetFullPath($ResultDir)
$script:Checks = New-Object System.Collections.ArrayList

function Add-Check {
    param(
        [string] $Area,
        [string] $Item,
        [string] $Value,
        [ValidateSet('PASS','WARN','FAIL','INFO')] [string] $Verdict,
        [string] $Note = ''
    )
    [void]$script:Checks.Add([pscustomobject]@{
        Area = $Area; Item = $Item; Value = $Value; Verdict = $Verdict; Note = $Note
    })
}

function Test-TcpPort {
    param([string] $TargetHost, [int] $Port, [int] $TimeoutMs)
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $iar = $client.BeginConnect($TargetHost, $Port, $null, $null)
        if (-not $iar.AsyncWaitHandle.WaitOne($TimeoutMs)) { return $false }
        $client.EndConnect($iar)
        return $true
    } catch { return $false } finally { $client.Close() }
}

# ---------------------------------------------------------------- OS / host
$osKey = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
$productName = (Get-ItemProperty $osKey -ErrorAction SilentlyContinue).ProductName
$buildNumber = [int](Get-ItemProperty $osKey -ErrorAction SilentlyContinue).CurrentBuildNumber
# Registry ProductName still says "Windows 10 Pro" on Windows 11; build number decides.
$osReal = if ($buildNumber -ge 22000) { 'Windows 11' } else { 'Windows 10' }
Add-Check 'OS' 'Version' "$osReal build $buildNumber (registry: $productName)" `
    $(if ($buildNumber -ge 19041) { 'PASS' } else { 'FAIL' }) `
    'build 22000 = Windows 11 21H2 despite the ProductName string'

$longPaths = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' -Name LongPathsEnabled -ErrorAction SilentlyContinue).LongPathsEnabled
Add-Check 'OS' 'LongPathsEnabled' "$longPaths" `
    $(if ($longPaths -eq 1) { 'PASS' } else { 'WARN' }) `
    'Deep build trees under a long project path can still hit MAX_PATH'

# ---------------------------------------------------------------- memory
$ramGB = $null
try {
    $availMB = (Get-Counter '\Memory\Available MBytes' -ErrorAction Stop).CounterSamples[0].CookedValue
    $ramGB = [math]::Round($availMB / 1024, 1)
} catch { }
if ($ramGB -ne $null) {
    Add-Check 'RAM' 'Available' "$ramGB GB" `
        $(if ($ramGB -ge 8) { 'PASS' } elseif ($ramGB -ge 4) { 'WARN' } else { 'FAIL' }) `
        'GGUF route mmaps weights; 8 GB+ free is enough (unlike the 24 GB PyTorch route)'
} else {
    Add-Check 'RAM' 'Available' 'unknown' 'WARN' 'Get-Counter failed; check Task Manager manually'
}

# ---------------------------------------------------------------- GPU
$gpuRaw = $null
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    $gpuRaw = & nvidia-smi --query-gpu=name,driver_version,memory.total,compute_cap --format=csv,noheader 2>&1
}
if ($gpuRaw -and $gpuRaw -notmatch 'FAIL|error') {
    $line = ($gpuRaw | Select-Object -First 1).ToString().Trim()
    $f = $line -split '\s*,\s*'
    $gpuName = $f[0]; $drv = $f[1]; $vram = $f[2]; $cc = $f[3]
    Add-Check 'GPU' 'Device' "$gpuName / $vram / CC $cc" `
        $(if ($cc -eq '7.0') { 'PASS' } else { 'FAIL' }) `
        'Build target is compute_70; a non-7.0 card needs a different -CudaArchitectures'
    $vramGB = 0
    [void][int]::TryParse(($vram -replace '[^\d]',''), [ref]$vramGB)
    Add-Check 'GPU' 'VRAM' "$([math]::Round($vramGB/1024,1)) GB" `
        $(if ($vramGB -ge 16384) { 'PASS' } elseif ($vramGB -ge 12288) { 'WARN' } else { 'FAIL' }) `
        'Q4_0 measured peak is ~7.7 GB and Q8_0 ~8.9 GB, so 16 GB has headroom'

    # Driver numbering is compared numerically on purpose: the "Rxxx" branch label
    # is marketing, not part of the version string, so it is not inferred here.
    $drvNum = 0.0
    if ($drv -match '^(\d+\.\d+)') { $drvNum = [double]$Matches[1] }
    if ($drvNum -ge 570 -and $drvNum -lt 581) {
        $v = 'PASS'; $n = 'CUDA 12.8 needs >= 570; Volta is still supported below the 580 cut-off'
    } elseif ($drvNum -ge 581) {
        $v = 'WARN'; $n = 'Newer than the 580 cut-off: Volta support may be gone - do NOT upgrade further'
    } else {
        $v = 'FAIL'; $n = 'CUDA 12.8 requires driver >= 570 on Windows'
    }
    Add-Check 'GPU' 'Driver' "$drv" $v $n
} else {
    Add-Check 'GPU' 'nvidia-smi' 'not available or no GPU reported' 'FAIL' 'Cannot verify the GPU at all'
}

# ---------------------------------------------------------------- CUDA
$nvcc = (Get-Command nvcc -ErrorAction SilentlyContinue | Select-Object -First 1).Source

# More than one toolkit on disk is normal and dangerous here: CUDA 13 cannot build
# sm_70 at all, so a build that silently picks 13.x fails on this GPU. Always pin.
$cudaToolkits = @()
if (Test-Path $CudaRoot) {
    $cudaToolkits = @(Get-ChildItem $CudaRoot -Directory -ErrorAction SilentlyContinue | Sort-Object Name)
}
if ($cudaToolkits.Count -gt 0) {
    $names = ($cudaToolkits | Select-Object -ExpandProperty Name) -join ', '
    $has13 = @($cudaToolkits | Where-Object { $_.Name -match '^v13\.' }).Count -gt 0
    Add-Check 'CUDA' 'Installed toolkits' $names `
        $(if ($has13) { 'WARN' } else { 'PASS' }) `
        $(if ($has13) { 'A 13.x toolkit cannot build sm_70 - pin CUDAToolkit_ROOT and CMAKE_CUDA_COMPILER to the 12.x tree' } else { '' })
}

# The toolkit that actually owns the nvcc on PATH is the only one that matters
# for the compiler-ceiling check below.
$activeCudaRoot = $null
if ($nvcc -and $nvcc -match '^(.*)\\bin\\nvcc\.exe$') { $activeCudaRoot = $Matches[1] }
if (-not $activeCudaRoot -and $cudaToolkits.Count -gt 0) {
    $activeCudaRoot = ($cudaToolkits | Where-Object { $_.Name -match '^v12\.' } |
        Sort-Object Name -Descending | Select-Object -First 1).FullName
}
Add-Check 'CUDA' 'Active toolkit root' "$activeCudaRoot" `
    $(if ($activeCudaRoot) { 'INFO' } else { 'WARN' }) 'Derived from the nvcc on PATH'

if (-not $nvcc) {
    Add-Check 'CUDA' 'nvcc' 'NOT FOUND on PATH' 'FAIL' 'Install the CUDA Toolkit 12.x'
} else {
    $nvccVer = (& $nvcc --version 2>&1 | Select-String 'release' | Select-Object -First 1).ToString().Trim()
    $is12 = $nvccVer -match 'release 12\.'
    Add-Check 'CUDA' 'nvcc' "$nvcc :: $nvccVer" `
        $(if ($is12) { 'PASS' } else { 'FAIL' }) `
        'CUDA 13 removes sm_70 entirely; a 12.x toolkit is mandatory for V100'

    $archList = & $nvcc --list-gpu-arch 2>&1
    $has70 = ($archList | Select-String -Pattern '^compute_70$') -ne $null
    Add-Check 'CUDA' 'compute_70 available' `
        $(if ($has70) { 'yes' } else { 'no' }) `
        $(if ($has70) { 'PASS' } else { 'FAIL' }) `
        'This is the single most important check: without compute_70 there is no V100 build'

    $envCuda = $env:CUDA_PATH
    Add-Check 'CUDA' 'CUDA_PATH' "$envCuda" `
        $(if ($envCuda) { 'PASS' } else { 'WARN' }) `
        'Toolkit root used by CMake when CUDAToolkit_ROOT is not passed explicitly'
}

# ---------------------------------------------------------------- MSVC
$msvcDirs = @()
foreach ($vs in @("$VsRoot\2022\BuildTools", "$VsRoot\2022\Community", "$VsRoot\2022\Professional",
                  "$VsRoot\2022\Enterprise", "$VsRoot\18\Community", "$VsRoot\18\BuildTools")) {
    $toolsRoot = Join-Path $vs 'VC\Tools\MSVC'
    if (Test-Path $toolsRoot) {
        foreach ($t in (Get-ChildItem $toolsRoot -Directory -ErrorAction SilentlyContinue)) {
            $cl = Join-Path $t.FullName 'bin\Hostx64\x64\cl.exe'
            if (Test-Path $cl) {
                # MSVC 14.44 -> _MSC_VER 1944
                $msc = 0
                if ($t.Name -match '^14\.(\d+)\.') { $msc = 1900 + [int]$Matches[1] }
                $msvcDirs += [pscustomobject]@{ Path = $cl; Version = $t.Name; MscVer = $msc; Vs = $vs }
            }
        }
    }
}

$hostConfig = Join-Path ($(if ($activeCudaRoot) { $activeCudaRoot } else { "$CudaRoot\v12.8" })) 'include\crt\host_config.h'
$mscUpper = $null
if (Test-Path $hostConfig) {
    $m = Select-String -Path $hostConfig -Pattern '_MSC_VER\s*>=\s*(\d{4})' -AllMatches |
         ForEach-Object { $_.Matches } | ForEach-Object { [int]$_.Groups[1].Value }
    if ($m) { $mscUpper = ($m | Measure-Object -Maximum).Maximum }
}
Add-Check 'MSVC' 'CUDA host compiler ceiling' "$(if ($mscUpper) { "_MSC_VER < $mscUpper" } else { 'unknown' })" `
    $(if ($mscUpper) { 'INFO' } else { 'WARN' }) `
    "$hostConfig"

if ($msvcDirs.Count -eq 0) {
    Add-Check 'MSVC' 'cl.exe' 'NOT FOUND' 'FAIL' 'Install VS 2022 Build Tools with the C++ desktop workload'
} else {
    $usable = if ($mscUpper) { $msvcDirs | Where-Object { $_.MscVer -lt $mscUpper } } else { $msvcDirs }
    $best = $usable | Sort-Object MscVer -Descending | Select-Object -First 1
    $all = ($msvcDirs | ForEach-Object { "$($_.Version)(_MSC_VER $($_.MscVer))" }) -join ', '
    if ($best) {
        Add-Check 'MSVC' 'cl.exe (usable)' "$($best.Version) at $($best.Path)" 'PASS' `
            "All installed: $all"
        Add-Check 'MSVC' 'VsInstall to pass' "$($best.Vs)" 'INFO' `
            'Pass this via -VsInstall so the newest VS 18 is not picked automatically'
    } else {
        Add-Check 'MSVC' 'cl.exe (usable)' "none of: $all" 'FAIL' `
            "CUDA 12.8 rejects _MSC_VER >= $mscUpper (VS 18); use VS 2022 Build Tools"
    }
}
$sdkRoot = 'C:\Program Files (x86)\Windows Kits\10\Include'
if (Test-Path $sdkRoot) {
    $sdk = (Get-ChildItem $sdkRoot -Directory | Sort-Object Name -Descending | Select-Object -First 1).Name
    Add-Check 'MSVC' 'Windows SDK' $sdk 'PASS' 'Required by cl.exe'
} else {
    Add-Check 'MSVC' 'Windows SDK' 'NOT FOUND' 'FAIL' 'Install the Windows 10/11 SDK component'
}

# ---------------------------------------------------------------- build tools
foreach ($tool in @(@{N='cmake';C='cmake'}, @{N='ninja';C='ninja'}, @{N='git';C='git'})) {
    $p = Get-Command $tool.C -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $p) {
        Add-Check 'Build' $tool.N 'NOT FOUND' $(if ($tool.N -eq 'cmake') { 'FAIL' } else { 'WARN' }) ''
        continue
    }
    $ver = try { (& $p.Source --version 2>&1 | Select-Object -First 1).ToString().Trim() } catch { 'unknown' }
    if ($tool.N -eq 'cmake') {
        $cm = $null
        if ($ver -match '(\d+)\.(\d+)') { $cm = [int]$Matches[1] * 100 + [int]$Matches[2] }
        Add-Check 'Build' 'cmake' "$($p.Source) :: $ver" `
            $(if ($cm -ge 324) { 'PASS' } elseif ($cm -ge 320) { 'WARN' } else { 'FAIL' }) `
            'audio.cpp needs >= 3.20; >= 3.24 only matters for -CudaArchitectures native'
    } else {
        Add-Check 'Build' $tool.N "$($p.Source) :: $ver" 'PASS' ''
    }
}

# ---------------------------------------------------------------- disk
$drives = Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue |
          Where-Object { $_.Free -ne $null -and $_.Name.Length -eq 1 }
foreach ($d in $drives) {
    $freeGB = [math]::Round($d.Free / 1GB, 1)
    if ($d.Name -eq $TargetDrive) {
        Add-Check 'Disk' "Target drive $($d.Name):" "$freeGB GB free" `
            $(if ($freeGB -ge $MinFreeGB) { 'PASS' } elseif ($freeGB -ge $WarnFreeGB) { 'WARN' } else { 'FAIL' }) `
            "Budget: weights 2.7 GB (Q4_0) + source 0.1 GB + build tree 3-8 GB + outputs"
    } else {
        Add-Check 'Disk' "Drive $($d.Name):" "$freeGB GB free" 'INFO' ''
    }
}
$hfHome = if ($env:HF_HOME) { $env:HF_HOME } else { Join-Path $env:USERPROFILE '.cache\huggingface' }
$hfDrive = ($hfHome -replace '^([A-Za-z]):.*','$1')
$hfFree = ($drives | Where-Object { $_.Name -eq $hfDrive } | Select-Object -First 1).Free
if ($hfFree -ne $null) {
    $hfFreeGB = [math]::Round($hfFree / 1GB, 1)
    Add-Check 'Disk' 'HF cache drive' "$hfHome (drive $hfDrive`: $hfFreeGB GB free)" `
        $(if ($hfFreeGB -ge 10) { 'PASS' } else { 'WARN' }) `
        'Set HF_HOME into the project folder if this drive is tight'
}

# ---------------------------------------------------------------- python + network
$py = Get-Command python -ErrorAction SilentlyContinue | Select-Object -First 1
if ($py) {
    $pyVer = try { (& $py.Source --version 2>&1 | Select-Object -First 1).ToString().Trim() } catch { 'unknown' }
    $hfCli = Get-Command hf -ErrorAction SilentlyContinue | Select-Object -First 1
    Add-Check 'Python' 'python' "$($py.Source) :: $pyVer" 'PASS' `
        "hf CLI: $(if ($hfCli) { $hfCli.Source } else { 'NOT installed - pip install huggingface_hub' })"
} else {
    Add-Check 'Python' 'python' 'NOT FOUND' 'WARN' 'Needed only for downloading the GGUF weights'
}

$netHost = 'hf-mirror.com'
if (Test-TcpPort -TargetHost $netHost -Port 443 -TimeoutMs $NetTimeoutMs) {
    Add-Check 'Network' "$netHost`:443" 'reachable' 'PASS' 'Use HF_ENDPOINT=https://hf-mirror.com'
} else {
    Add-Check 'Network' "$netHost`:443" 'unreachable' 'WARN' 'Check proxy/DNS before the 2.7 GB download'
}

# ---------------------------------------------------------------- report
if (-not (Test-Path $ResultDir)) { New-Item -ItemType Directory -Path $ResultDir -Force | Out-Null }

Write-Host ''
Write-Host '=== yue2-V100 preflight ===' -ForegroundColor Cyan
$script:Checks | Format-Table -AutoSize -Wrap Area, Item, Verdict, Value | Out-String -Width 200 | Write-Host

Write-Host '--- notes ---' -ForegroundColor Cyan
$script:Checks | Where-Object { $_.Note -ne '' } |
    ForEach-Object { Write-Host ("[{0}] {1}/{2}: {3}" -f $_.Verdict, $_.Area, $_.Item, $_.Note) }

$fail = @($script:Checks | Where-Object { $_.Verdict -eq 'FAIL' }).Count
$warn = @($script:Checks | Where-Object { $_.Verdict -eq 'WARN' }).Count
$pass = @($script:Checks | Where-Object { $_.Verdict -eq 'PASS' }).Count

Write-Host ''
$color = if ($fail -gt 0) { 'Red' } elseif ($warn -gt 0) { 'Yellow' } else { 'Green' }
$verdict = if ($fail -gt 0) { 'BLOCKED' } elseif ($warn -gt 0) { 'READY WITH WARNINGS' } else { 'READY' }
Write-Host ("RESULT: {0}   (PASS {1} / WARN {2} / FAIL {3})" -f $verdict, $pass, $warn, $fail) -ForegroundColor $color

$report = [pscustomobject]@{
    GeneratedAt = (Get-Date).ToString('s')
    Host        = "$env:COMPUTERNAME ($osReal build $buildNumber)"
    Verdict     = $verdict
    Summary     = [pscustomobject]@{ Pass = $pass; Warn = $warn; Fail = $fail }
    Checks      = $script:Checks
}
$outFile = Join-Path $ResultDir ("preflight-{0}.json" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
$report | ConvertTo-Json -Depth 5 | Set-Content -Path $outFile -Encoding UTF8
Write-Host "Report: $outFile"

if ($fail -gt 0) { exit 1 } else { exit 0 }
