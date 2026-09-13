<#
.SYNOPSIS
    Build the "download and run" release archive for V100.

.DESCRIPTION
    The archive carries everything that cannot be fetched or rebuilt by the
    user, and nothing that can:

      * this project's code (app/ scripts/ docs/ skills/ patches/)
      * the compiled audiocpp_cli.exe — 29 MB. This is the point of the archive:
        without it the user needs CUDA Toolkit + VS 2022 + ~30 minutes of build.
      * the CUDA runtime DLLs the exe actually imports. Measured from the PE
        import table: cudart64_12 + cublas64_12 only. cublasLt64_12 (660 MB) is
        NOT imported, so it is not included — that single fact is the difference
        between a ~140 MB archive and an ~800 MB one.
      * CUDA's EULA.txt (required when redistributing NVIDIA runtime files).
      * requirements.txt + a setup script for the Python side.

    It deliberately excludes:
      * models/  — 4.7 GB and licence-restricted (see WEIGHTS.md)
      * output/  — generated artifacts
      * build/   — CMake intermediates (the exe is copied out of it)
      * logs/ cache/

.EXAMPLE
    pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\make-release.ps1
.EXAMPLE
    # 只看会打包什么,不写文件
    pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\make-release.ps1 -DryRun
#>
[CmdletBinding()]
param(
    [string] $CudaRoot = 'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.8',
    [string] $OutDir   = (Join-Path $PSScriptRoot '..\..\release'),
    # 第二个产物:已打补丁的 audio.cpp 源码。默认生成 —— 它同时是
    # "想自己编译的人要的东西"和"GPL 要求的 Corresponding Source"。
    [switch] $SkipAudioCppSource,
    [switch] $DryRun
)

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$version = (Get-Content (Join-Path $root 'VERSION') -Raw).Trim()
$stage = Join-Path $OutDir "yue2-v100-v$version"
$zip = Join-Path $OutDir "yue2-v100-v$version-win-x64-v100.zip"

# 只从 PE 导入表里**实测**需要的两个 CUDA 运行库。
# cublasLt64_12.dll(660 MB)不在导入表里 —— 别顺手把它塞进来。
$cudaDlls = @('cudart64_12.dll', 'cublas64_12.dll')

$plan = [ordered]@{}
function Add-Plan([string] $what, [string] $src) {
    if (Test-Path $src) {
        $f = Get-Item $src
        $plan[$what] = @{ src = $src; bytes = $f.Length }
    } else {
        $plan[$what] = @{ src = $src; bytes = $null }
    }
}

# 1) 我们的代码
foreach ($d in @('app', 'scripts', 'docs', 'examples', 'skills', 'patches', 'results')) {
    $p = Join-Path $root $d
    if (Test-Path $p) {
        $plan["$d\"] = @{ src = $p; bytes = (Get-ChildItem $p -Recurse -File | Measure-Object Length -Sum).Sum }
    }
}
foreach ($f in @('README.md', 'CHANGELOG.md', 'VERSION', 'LICENSE', 'NOTICE', 'WEIGHTS.md',
                 'requirements.txt', '.gitignore', 'server.json')) {
    Add-Plan $f (Join-Path $root $f)
}

# 2) 引擎(这个压缩包真正的意义)
Add-Plan 'bin\audiocpp_cli.exe' (Join-Path $root 'build\bin\Release\audiocpp_cli.exe')

# 3) CUDA 运行库 + EULA
foreach ($n in $cudaDlls) { Add-Plan "bin\$n" (Join-Path $CudaRoot "bin\$n") }
Add-Plan 'bin\CUDA-EULA.txt' (Join-Path $CudaRoot 'EULA.txt')

Write-Host "yue2-V100 v$version  发布包" -ForegroundColor Cyan
Write-Host "输出: $zip"
Write-Host ''
$total = 0
foreach ($k in $plan.Keys) {
    $e = $plan[$k]
    if ($null -eq $e.bytes) {
        Write-Host ("  {0,-34} {1}" -f $k, '缺失!(会跳过)') -ForegroundColor Yellow
    } else {
        $total += $e.bytes
        Write-Host ("  {0,-34} {1,9:N2} MB" -f $k, ($e.bytes / 1MB))
    }
}
Write-Host ''
Write-Host ("  合计  {0:N1} MB(压缩后会更小)" -f ($total / 1MB)) -ForegroundColor Green
Write-Host ''

$missing = @($plan.Keys | Where-Object { $null -eq $plan[$_].bytes })
if ($missing -contains 'bin\audiocpp_cli.exe') {
    throw '缺少引擎 exe —— 先编译(见 README)。'
}

if ($DryRun) { Write-Host 'DryRun:未写任何文件。' -ForegroundColor Yellow; exit 0 }

if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Force (Join-Path $stage 'bin') | Out-Null

foreach ($k in $plan.Keys) {
    $e = $plan[$k]
    if ($null -eq $e.bytes) { continue }
    $dest = Join-Path $stage $k
    if ((Get-Item $e.src).PSIsContainer) {
        # robocopy 对目录更稳,且能排除我们不想带的东西
        & robocopy $e.src $dest /E /NFL /NDL /NJH /NJS /NP `
            /XD __pycache__ .git .venv venv | Out-Null
    } else {
        New-Item -ItemType Directory -Force (Split-Path $dest -Parent) | Out-Null
        Copy-Item $e.src $dest -Force
    }
}

# 发布时的合规说明:分发二进制必须能拿到对应源码(GPLv3 §6)
$readme = @"
yue2-V100 v$version —— 下载即用包(V100 / SM70 专用)
=====================================================

这个包里的 bin\audiocpp_cli.exe 是**为 Tesla V100(SM70)编译的**,
只含 sm_70 的机器码,在其它架构的显卡上跑不了。

目录
----
  app\ scripts\ docs\ skills\ patches\   本项目的代码与文档
  requirements.txt                        Python 依赖(8 个直接依赖)
  bin\audiocpp_cli.exe                    已编译的引擎(29 MB)
  bin\cudart64_12.dll  bin\cublas64_12.dll   引擎实际导入的 CUDA 运行库
  bin\CUDA-EULA.txt                       NVIDIA CUDA 许可(再分发要求随附)
  WEIGHTS.md  NOTICE  LICENSE             许可与第三方归属

还需要你自己准备的
------------------
  1. NVIDIA 驱动(提供 nvcuda.dll)
  2. Python 3.10+  ->  pip install -r requirements.txt
  3. 模型权重(约 4.7 GB)->  见 WEIGHTS.md
     ⚠️ 其中 MuScriptor 权重是 CC-BY-NC-4.0(禁止商用);
        YuE2 权重**没有许可声明**,不能推定可以再分发。本包不含权重。

怎么跑
------
  python scripts\start-gui.ps1      # 或用 pwsh 跑
  然后打开 http://127.0.0.1:7860

许可与对应源码(GPLv3)
----------------------
本项目以 GPL-3.0-or-later 分发,见 LICENSE。它构建于 audio.cpp(Apache-2.0,
Copyright 2026 ShugoAI LLC)之上,并修改了其中 6 个文件。

  上游   https://github.com/0xShug0/audio.cpp
  提交   87544b57242f52c7e7d3dee5766fe82eacf6a321  (dev 分支)
  补丁   patches\*.patch
  获取   scripts\fetch-audio-cpp.ps1

按 GPLv3,分发这个二进制的同时必须能获得对应的完整源码 ——
上述提交 + 本包内的 patches\ 就是它。请把这一节连同仓库地址一起发布。
"@
Set-Content (Join-Path $stage 'README-RELEASE.txt') -Value $readme -Encoding UTF8

Write-Host '正在压缩…' -ForegroundColor Cyan
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $zip -CompressionLevel Optimal
$z = Get-Item $zip
Write-Host ''
Write-Host ("完成: {0}" -f $zip) -ForegroundColor Green
Write-Host ("      {0:N1} MB(解压后 {1:N1} MB)" -f ($z.Length / 1MB), ($total / 1MB))

# ── 第二个产物:已打补丁的 audio.cpp 源码 ─────────────────────────────────
#
# 为什么单独一个包,而不是塞进上面的 zip:
#   * 用预编译 exe 的人**根本不需要**源码,凭什么多下 104 MB;
#   * 想自己编译的人(换显卡架构)才需要;
#   * GPLv3 §6(d) 只要求"在同一处可获得对应源码" —— 同一个 Release 页面即可。
#
# 为什么整棵树打包、不裁剪:
#   裁掉测试音频能省 47 MB,但裁剪是"版本出问题"的头号来源。
#   整棵树 204 MB → 51% 压缩后 104 MB,这个代价换"绝对一致"是值得的。
#
# 归档里的源码是**已打补丁**的 —— 用户拿到就能直接编译,不必再跑 git apply。
if ($SkipAudioCppSource) {
    Write-Host '(-SkipAudioCppSource:未生成源码包)' -ForegroundColor Yellow
} else {
    $acSrc = Join-Path $root 'audio.cpp'
    $srcZip = Join-Path $OutDir "yue2-v100-v$version-audio.cpp-source-patched.zip"
    if (-not (Test-Path (Join-Path $acSrc '.git'))) {
        Write-Host "audio.cpp 不是 git 检出,跳过源码包:$acSrc" -ForegroundColor Yellow
    } else {
        Write-Host ''
        Write-Host '正在打包 audio.cpp 源码(已含本项目补丁)…' -ForegroundColor Cyan
        $tmp = Join-Path $OutDir '_srctmp'
        if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
        New-Item -ItemType Directory -Force $tmp | Out-Null
        # /XD .git —— 那个目录 95 MB 且与"对应源码"无关
        & robocopy $acSrc $tmp /E /NFL /NDL /NJH /NJS /NP /XD .git | Out-Null
        $treeBytes = (Get-ChildItem $tmp -Recurse -File | Measure-Object Length -Sum).Sum

        $head = (& git -C $acSrc rev-parse HEAD).Trim()
        @"
audio.cpp —— 本项目构建所用的源码(已含 yue2-V100 的补丁)
=========================================================

上游     https://github.com/0xShug0/audio.cpp
提交     $head
版权     Copyright 2026 ShugoAI LLC
许可     Apache License 2.0(见 LICENSE)

**这棵树已经打好了本项目的 2 个补丁**,解压后可直接 configure/编译:

  1. external/ggml/src/ggml-cuda/fattn.cu
     Volta 上闪存注意力回退到通用瓦片内核(没有它,V100 上首次生成就崩)
  2. src/models/yue2/{pipeline,tokenizer_text,session}.cpp 与对应 .h
     导出 YuE2 生成的 ABC 乐谱(上游只丢弃它)

补丁的原始形式在 yue2-V100 仓库的 patches\ 目录,可用 git apply 复核:

  git clone <yue2-V100 仓库>
  git apply --check --reverse patches\*.patch   # 反向能应用 = 本树与补丁一致

本归档不含 .git(95 MB,与对应源码无关)。除上述 6 个文件外,其余与上游提交完全一致。

编译步骤见 yue2-V100 仓库的 README(需 CUDA Toolkit 12.8 + VS 2022,约 30 分钟)。
⚠️ 编译产物只对该架构有效:本项目用 CMAKE_CUDA_ARCHITECTURES=70-real,只编 sm_70 机器码。
"@ | Set-Content (Join-Path $tmp 'SOURCE-NOTES.txt') -Encoding UTF8

        if (Test-Path $srcZip) { Remove-Item $srcZip -Force }
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        [System.IO.Compression.ZipFile]::CreateFromDirectory(
            $tmp, $srcZip, [System.IO.Compression.CompressionLevel]::Optimal, $false)
        Remove-Item $tmp -Recurse -Force
        $sz = Get-Item $srcZip
        Write-Host ("      {0:N1} MB(解压后 {1:N1} MB)" -f ($sz.Length / 1MB), ($treeBytes / 1MB)) -ForegroundColor Green
    }
}

