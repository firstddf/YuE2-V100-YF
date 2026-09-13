<#
.SYNOPSIS
    Configure audio.cpp for a Tesla V100 (SM70) on this Windows host.

.DESCRIPTION
    CONFIGURE ONLY - this does not compile anything. It exists to prove that the
    toolchain resolves correctly before any build time is spent, because several
    things on this machine will silently pick the wrong tool if left alone:

      * CUDA 13.1 is installed next to 12.8. CUDA 13 cannot build sm_70 at all,
        and a CUDA 13 binary also needs driver >= 580 (this host runs 572.83).
      * Visual Studio 18 is installed next to VS 2022. CUDA 12.8's host_config.h
        rejects _MSC_VER >= 1950, and vswhere -latest returns VS 18 first.
        The "Visual Studio 17 2022" generator pins this by name.
      * The default CUDA architecture list covers 10 architectures. Building all
        of them costs time and several GB for no benefit on a single-GPU host.

    Generator choice: the default is the Visual Studio generator, not Ninja.
    Ninja hangs on this host during CMake's "Detecting C compiler ABI info"
    (reproduced with ninja 1.12.1 from VS and with the 1.13 jobserver-pipe build
    on PATH; CMake's ABI probe drives the build tool for a try_compile project).
    Switch with -Generator Ninja only when building from an ordinary terminal.

    Run scripts\check-env.ps1 first; rationale is in docs\preflight-plan.md.

.EXAMPLE
    pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\configure.ps1
.EXAMPLE
    # after changing CUDA toolkit or VS install
    pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\configure.ps1 -Reconfigure
#>
[CmdletBinding()]
param(
    [string] $SourceDir = (Join-Path $PSScriptRoot '..\audio.cpp'),
    [string] $BuildDir  = (Join-Path $PSScriptRoot '..\build'),
    [string] $CudaRoot  = 'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.8',
    [string] $VsInstall = 'C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools',
    [string] $CudaArch  = '70-real',
    [string] $Generator = 'Visual Studio 17 2022',
    [string] $NinjaPath = 'C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\Common7\IDE\CommonExtensions\Microsoft\CMake\Ninja\ninja.exe',
    [string] $ModelSet  = 'custom',
    [string] $Models    = 'yue2',
    [switch] $Reconfigure
)

$ErrorActionPreference = 'Stop'
$SourceDir = [System.IO.Path]::GetFullPath($SourceDir)
$BuildDir  = [System.IO.Path]::GetFullPath($BuildDir)
$useNinja  = ($Generator -eq 'Ninja')

# ---------------------------------------------------------------- preflight
foreach ($p in @($SourceDir, $CudaRoot)) {
    if (-not (Test-Path $p)) { throw "Required path missing: $p" }
}
if (-not (Test-Path (Join-Path $SourceDir 'CMakeLists.txt'))) {
    throw "Not an audio.cpp source tree: $SourceDir"
}
$nvcc = Join-Path $CudaRoot 'bin\nvcc.exe'
if (-not (Test-Path $nvcc)) { throw "nvcc not found: $nvcc" }

$compute = $CudaArch -replace '-.*$', ''
$archOut = & $nvcc --list-gpu-arch 2>&1
if (-not ($archOut | Select-String -Pattern "^compute_$compute$")) {
    throw "nvcc at $nvcc does not offer compute_$compute - wrong toolkit for this GPU"
}

# CUDA 12.8 rejects a host compiler at or above this _MSC_VER.
$hostCeiling = 1950
$hostConfig = Join-Path $CudaRoot 'include\crt\host_config.h'
if (Test-Path $hostConfig) {
    $m = Select-String -Path $hostConfig -Pattern '_MSC_VER\s*>=\s*(\d{4})' -AllMatches |
         ForEach-Object { $_.Matches } | ForEach-Object { [int]$_.Groups[1].Value }
    if ($m) { $hostCeiling = ($m | Measure-Object -Maximum).Maximum }
}

if ($Reconfigure -and (Test-Path (Join-Path $BuildDir 'CMakeCache.txt'))) {
    Write-Host "Removing cached CMake state in $BuildDir (toolchain changes need this)" -ForegroundColor Yellow
    Remove-Item (Join-Path $BuildDir 'CMakeCache.txt') -Force
    Remove-Item (Join-Path $BuildDir 'CMakeFiles') -Recurse -Force -ErrorAction SilentlyContinue
}
New-Item -ItemType Directory -Path $BuildDir -Force | Out-Null

$cmakeArgs = @(
    '-S', $SourceDir,
    '-B', $BuildDir,
    '-G', $Generator,
    # mirrors the upstream 'windows-cuda-release' preset
    '-DENGINE_ENABLE_CUDA=ON',
    '-DENGINE_ENABLE_CUDA_GRAPHS=ON',
    '-DENGINE_ENABLE_VULKAN=OFF',
    '-DENGINE_ENABLE_NATIVE_CPU=ON',
    '-DENGINE_ENABLE_LLAMAFILE=ON',
    '-DENGINE_BUILD_TESTS=OFF',
    # toolchain pinning - both are required, see docs\preflight-plan.md W1
    "-DCUDAToolkit_ROOT=$CudaRoot",
    "-DCMAKE_CUDA_COMPILER=$nvcc",
    "-DCMAKE_CUDA_ARCHITECTURES=$CudaArch",
    # build only what this GPU will run
    "-DAUDIOCPP_MODEL_SET=$ModelSet",
    "-DAUDIOCPP_MODELS=$Models"
)

if ($useNinja) {
    # The ninja on PATH is '1.13.0.git.kitware.jobserver-pipe-1': its job server
    # rides a named pipe, which a sandboxed shell denies. Use the VS-bundled one.
    $ninja = if (Test-Path $NinjaPath) { $NinjaPath } else { (Get-Command ninja -ErrorAction SilentlyContinue | Select-Object -First 1).Source }
    if (-not $ninja) { throw 'No ninja found. Pass -NinjaPath, or use the Visual Studio generator.' }
    $ninjaVer = (& $ninja --version 2>&1 | Select-Object -First 1).ToString().Trim()
    if ($ninjaVer -match 'jobserver-pipe') {
        throw "ninja at $ninja ($ninjaVer) uses a named-pipe job server and hangs in a sandboxed shell."
    }
    $cmakeArgs += @('-DCMAKE_BUILD_TYPE=Release', "-DCMAKE_MAKE_PROGRAM=$ninja")
    $buildCmd = "cmake --build `"$BuildDir`" --target audiocpp_cli"
    Write-Host "  generator : Ninja ($ninjaVer)"
    Write-Host "  ninja     : $ninja"
} else {
    # VS generator: multi-config, so build type is chosen at build time.
    # The generator name pins the VS major version, which is what keeps
    # CUDA 12.8 away from the VS 18 installation.
    if (-not (Test-Path $VsInstall)) { throw "VS install missing: $VsInstall" }
    $cmakeArgs += @('-A', 'x64', '-T', 'host=x64', '-DCMAKE_CONFIGURATION_TYPES=Release')
    $buildCmd = "cmake --build `"$BuildDir`" --config Release --target audiocpp_cli"
    Write-Host "  generator : $Generator (multi-config, Release only)"
}

# Report the MSVC toolset that the VS generator will actually use. The version
# is derived from the toolset directory name, not from cl.exe's banner: this
# machine's cl prints its usage text in Chinese, so banner parsing is unreliable.
if (Test-Path $VsInstall) {
    $tools = Get-ChildItem (Join-Path $VsInstall 'VC\Tools\MSVC') -Directory -ErrorAction SilentlyContinue |
             Sort-Object Name -Descending | Select-Object -First 1
    if ($tools -and $tools.Name -match '^14\.(\d+)\.') {
        $mscVer = 1900 + [int]$Matches[1]
        Write-Host "  MSVC      : $($tools.Name) (_MSC_VER $mscVer, ceiling $hostCeiling)"
        if ($mscVer -ge $hostCeiling) {
            throw "MSVC _MSC_VER $mscVer is rejected by CUDA ($hostCeiling). Point -VsInstall at VS 2022 Build Tools."
        }
    }
}

Write-Host ''
Write-Host '=== cmake configure ===' -ForegroundColor Cyan
Write-Host ("cmake " + ($cmakeArgs -join ' '))
Write-Host ''
& cmake @cmakeArgs
$code = $LASTEXITCODE

Write-Host ''
if ($code -ne 0) {
    Write-Host "CONFIGURE FAILED (exit $code)" -ForegroundColor Red
    exit $code
}
Write-Host 'CONFIGURE OK' -ForegroundColor Green

# ---------------------------------------------------------------- verify cache
Write-Host ''
Write-Host '=== resolved toolchain ===' -ForegroundColor Cyan
$cacheFile = Join-Path $BuildDir 'CMakeCache.txt'

function Get-Cached {
    param([string] $Key)
    $hit = Select-String -Path $cacheFile -Pattern "^$Key(:[^=]*)?=" | Select-Object -First 1
    if ($hit) { return ($hit.Line -split '=', 2)[1] }
    return $null
}
# The compiler identity is not a cache entry: CMake records it in
# CMakeFiles\<ver>\CMake<LANG>Compiler.cmake instead.
function Get-ToolchainValue {
    param([string] $FileName, [string] $VarName)
    $f = Get-ChildItem $BuildDir -Recurse -Filter $FileName -File -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $f) { return $null }
    $hit = Select-String -Path $f.FullName -Pattern ("^set\(" + [regex]::Escape($VarName) + "\s+`"?([^`"\r\n]*)`"?\)") |
           Select-Object -First 1
    if ($hit) { return $hit.Matches[0].Groups[1].Value }
    return $null
}

$report = [ordered]@{
    'CMAKE_GENERATOR'           = Get-Cached 'CMAKE_GENERATOR'
    'CMAKE_CXX_COMPILER'        = Get-ToolchainValue 'CMakeCXXCompiler.cmake'  'CMAKE_CXX_COMPILER'
    'CMAKE_CXX_COMPILER_VERSION'= Get-ToolchainValue 'CMakeCXXCompiler.cmake'  'CMAKE_CXX_COMPILER_VERSION'
    'CMAKE_CUDA_COMPILER'       = Get-Cached 'CMAKE_CUDA_COMPILER'
    'CMAKE_CUDA_COMPILER_VERSION'= Get-ToolchainValue 'CMakeCUDACompiler.cmake' 'CMAKE_CUDA_COMPILER_VERSION'
    'CMAKE_CUDA_HOST_VERSION'   = Get-ToolchainValue 'CMakeCUDACompiler.cmake' 'CMAKE_CUDA_HOST_COMPILER_VERSION'
    'CUDAToolkit_BIN_DIR'       = Get-Cached 'CUDAToolkit_BIN_DIR'
    'CMAKE_CUDA_ARCHITECTURES'  = Get-Cached 'CMAKE_CUDA_ARCHITECTURES'
    'ENGINE_ENABLE_CUDA'        = Get-Cached 'ENGINE_ENABLE_CUDA'
    'ENGINE_ENABLE_CUDA_GRAPHS' = Get-Cached 'ENGINE_ENABLE_CUDA_GRAPHS'
    'AUDIOCPP_MODEL_SET'        = Get-Cached 'AUDIOCPP_MODEL_SET'
    'AUDIOCPP_MODELS'           = Get-Cached 'AUDIOCPP_MODELS'
    'AUDIOCPP_GGML_SOURCE_DIR'  = Get-Cached 'AUDIOCPP_GGML_SOURCE_DIR'
}
foreach ($kv in $report.GetEnumerator()) {
    "{0,-28} {1}" -f $kv.Key, $(if ($kv.Value) { $kv.Value } else { '(not found)' })
}

# The two values that would silently ruin the build if they drifted.
$expectedCudaBin = ((Join-Path $CudaRoot 'bin') -replace '\\', '/')
$actualCudaBin = $report['CUDAToolkit_BIN_DIR']
if ($actualCudaBin -and (($actualCudaBin -replace '\\', '/') -ne $expectedCudaBin)) {
    Write-Host "WARNING: CUDAToolkit resolved to '$actualCudaBin', expected '$expectedCudaBin'" -ForegroundColor Red
}
if ($report['CMAKE_CUDA_ARCHITECTURES'] -ne $CudaArch) {
    Write-Host "WARNING: CMAKE_CUDA_ARCHITECTURES is '$($report['CMAKE_CUDA_ARCHITECTURES'])', expected '$CudaArch'" -ForegroundColor Red
}

$meta = Get-ChildItem $BuildDir -Recurse -Filter '*.ninja' -File -ErrorAction SilentlyContinue
$requested = @($Models -split '[,;]' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
if ($useNinja -and (Test-Path (Join-Path $BuildDir 'build.ninja'))) {
    $probe = Join-Path $BuildDir 'build.ninja'
    $label = 'build.ninja'
} else {
    $sln = Get-ChildItem $BuildDir -Filter '*.sln' -File -ErrorAction SilentlyContinue | Select-Object -First 1
    $probe = if ($sln) { $sln.FullName } else { $null }
    $label = if ($sln) { $sln.Name } else { '' }
}
if ($probe) {
    # 名字拼错时 CMake 会直接 FATAL_ERROR("Unknown AUDIOCPP_MODELS entry"),
    # 所以能走到这里就说明名字合法。但请求名可能是别名(如 htdemucs -> demucs),
    # 所以不猜别名,而是**把构建系统里真实存在的模型目标列出来**,让人一眼比对。
    $found = @()
    if ($useNinja) {
        $found = Select-String -Path $probe -Pattern 'engine_model_([A-Za-z0-9_]+)' -AllMatches |
                 ForEach-Object { $_.Matches } | ForEach-Object { $_.Groups[1].Value } |
                 Sort-Object -Unique
    } else {
        $found = Get-ChildItem $BuildDir -Recurse -Filter 'engine_model_*.vcxproj' -File -ErrorAction SilentlyContinue |
                 ForEach-Object { $_.BaseName -replace '^engine_model_', '' } | Sort-Object -Unique
    }
    "{0,-28} {1}" -f 'requested models', ($requested -join ', ')
    "{0,-28} {1}" -f 'wired model targets', $(if ($found) { $found -join ', ' } else { '(none)' })
    foreach ($name in $requested) {
        if ($found -notcontains $name) {
            Write-Host ("WARNING: '$name' 未出现在构建系统目标里(可能是别名,或 configure 未生效)") -ForegroundColor Yellow
        }
    }
}

Write-Host ''
Write-Host 'Nothing was compiled. To build (later step):' -ForegroundColor Yellow
Write-Host "  $buildCmd"
