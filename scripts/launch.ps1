<#
.SYNOPSIS
    启动(或停止)yue2-V100,并在启动前把缺的东西一次性说清楚。

.DESCRIPTION
    双击根目录的 start.bat 就会走到这里。之所以让 .bat 只做瘦启动器、
    把中文输出全放在本脚本:.bat 是 cmd 读的,代码页(936 或 65001)会让
    中文变成乱码;PowerShell 读写 UTF-8 没有这个问题。

    启动前会逐项检查,缺什么就明确告诉你缺什么、去哪儿补 —— 而不是
    等引擎启动到一半才抛一句看不懂的错。

    兼容 Windows PowerShell 5.1(不依赖 PowerShell 7):优先用 pwsh,
    没有就退回 powershell。语法避开 7 才有的 ?: / ?? 等。

.EXAMPLE
    pwsh -File .\scripts\launch.ps1
.EXAMPLE
    pwsh -File .\scripts\launch.ps1 -Stop
.EXAMPLE
    pwsh -File .\scripts\launch.ps1 -CheckOnly   # 只体检,不启动
#>
[CmdletBinding()]
param(
    [switch] $Stop,
    [switch] $CheckOnly,
    [switch] $NoBrowser,
    [int]    $ServicePort = 1414,
    [int]    $GuiPort     = 7860
)

$ErrorActionPreference = 'Continue'
$root = Split-Path $PSScriptRoot -Parent

function Say([string] $text, [string] $color = 'Gray') { Write-Host $text -ForegroundColor $color }
function Ok([string] $text)   { Write-Host "  [OK]   $text" -ForegroundColor Green }
function Warn([string] $text) { Write-Host "  [警告] $text" -ForegroundColor Yellow }
function Bad([string] $text)  { Write-Host "  [缺失] $text" -ForegroundColor Red }

# ── 停止 ────────────────────────────────────────────────────────────────
if ($Stop) {
    Say ''
    Say '正在停止 yue2-V100 ...' Cyan
    & (Join-Path $PSScriptRoot 'start-gui.ps1') -Stop -ServicePort $ServicePort -GuiPort $GuiPort
    Say '已停止。' Green
    exit 0
}

Say ''
Say '========================================' Cyan
Say '  yue2-V100  —— V100(SM70)本地音乐生成' Cyan
Say '========================================' Cyan
Say ''
Say "项目目录: $root"
Say ''

$problems = New-Object System.Collections.ArrayList

# ── 1. Python ──────────────────────────────────────────────────────────
# 顺序:显式指定 > 本机 runtime\ > venv > py 启动器 > PATH。
# 刻意**不写死**任何绝对路径(比如 D:\Python\...) —— 那是开发机的路径,
# 对下载发布包的人毫无意义,还会盖过他自己装的那个。
$candidates = New-Object System.Collections.ArrayList
if ($env:YUE2_PYTHON) { [void]$candidates.Add($env:YUE2_PYTHON) }
[void]$candidates.Add((Join-Path (Split-Path $root -Parent) 'runtime\Scripts\python.exe'))
[void]$candidates.Add((Join-Path $root 'runtime\Scripts\python.exe'))
[void]$candidates.Add((Join-Path $root '.venv\Scripts\python.exe'))
foreach ($v in @('313', '312', '311', '310')) {
    [void]$candidates.Add((Join-Path $env:LOCALAPPDATA "Programs\Python\Python$v\python.exe"))
}

$existing = @($candidates | Where-Object { $_ -and (Test-Path $_) })

# py 启动器:Windows 官方安装器的默认入口,能自己找到已装的版本
if ($existing.Count -eq 0) {
    $py = Get-Command 'py' -ErrorAction SilentlyContinue
    if ($py) {
        $resolved = (& py -3 -c "import sys; print(sys.executable)" 2>$null | Select-Object -First 1)
        if ($resolved -and (Test-Path $resolved)) { $existing += $resolved }
    }
}
if ($existing.Count -eq 0) {
    foreach ($name in @('python', 'python3')) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) { $existing += $cmd.Source; break }
    }
}

# 多个候选时,优先选**真的装了依赖**的那个。
# 起因:实测过一台机器上第一个候选是干净的系统 Python,体检报"缺 6 个依赖",
# 而旁边那个 runtime 里的 Python 其实什么都装好了 —— 一键启动不该把人引到坑里。
function Test-HasGradio([string] $exe) {
    & $exe -c "import gradio" 2>$null
    return ($LASTEXITCODE -eq 0)
}

$python = $null
foreach ($c in $existing) {
    if (-not $python) { $python = $c }
    if ($existing.Count -gt 1 -and (Test-HasGradio $c)) { $python = $c; break }
}
if ($existing.Count -eq 1) { $python = $existing[0] }

if ($python) {
    $ver = (& $python --version 2>&1) -join ' '
    Ok "Python: $python  ($ver)"
    if ($existing.Count -gt 1) {
        Say ("         其它候选: " + (($existing | Where-Object { $_ -ne $python }) -join '  '))
    }
} else {
    Bad 'Python: 没找到。装 Python 3.10+(3.12 验证过),或用 YUE2_PYTHON 环境变量指定路径。'
    [void]$problems.Add('python')
}

# ── 2. Python 依赖 ─────────────────────────────────────────────────────
if ($python) {
    $need = @('gradio', 'fastapi', 'uvicorn', 'requests', 'librosa', 'numpy',
              'soundfile', 'pyloudnorm', 'huggingface_hub')
    $script = ($need | ForEach-Object { "import $_" }) -join '; '
    $out = & $python -c "$script" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Ok "依赖: $($need.Count) 个直接依赖都在"
    } else {
        $missing = @()
        foreach ($m in $need) {
            & $python -c "import $m" 2>$null
            if ($LASTEXITCODE -ne 0) { $missing += $m }
        }
        Bad ("依赖缺失: " + ($missing -join ', '))
        Say  "         补装: `"$python`" -m pip install -r requirements.txt"
        [void]$problems.Add('deps')
    }
}

# ── 3. 引擎 exe ────────────────────────────────────────────────────────
# 和 app\yue2_service.py 的 _find_engine() 保持同一个顺序,别让两处说法不一致。
$engineCandidates = @()
if ($env:YUE2_ENGINE) { $engineCandidates += $env:YUE2_ENGINE }
$engineCandidates += @(
    (Join-Path $root 'build\bin\Release\audiocpp_cli.exe'),
    (Join-Path $root 'bin\audiocpp_cli.exe'),
    (Join-Path $root 'build\bin\audiocpp_cli.exe')
)
$engine = $null
foreach ($e in $engineCandidates) { if (Test-Path $e) { $engine = $e; break } }
if ($engine) {
    $mb = [math]::Round((Get-Item $engine).Length / 1MB, 1)
    Ok "引擎: $engine  ($mb MB)"
} else {
    Bad '引擎: 没找到 audiocpp_cli.exe'
    Say '         要么下载发布包(已含编译好的引擎),要么自己编译:'
    Say '           scripts\fetch-audio-cpp.ps1  ->  scripts\configure.ps1  ->  编译(见 README)'
    [void]$problems.Add('engine')
}

# ── 4. CUDA 运行库(发布包布局下 exe 旁边必须有)─────────────────────
if ($engine) {
    $exeDir = Split-Path $engine -Parent
    $dlls = @('cudart64_12.dll', 'cublas64_12.dll')
    $missingDll = @()
    foreach ($d in $dlls) { if (-not (Test-Path (Join-Path $exeDir $d))) { $missingDll += $d } }

    # 源码树的 build 输出旁边没有 DLL 也正常 —— 那时靠 CUDA Toolkit 的 bin 在 PATH 里
    $isBuildTree = $engine -like '*\build\bin\Release\*'
    $cudaOnPath = $false
    if ($env:PATH) { foreach ($seg in $env:PATH.Split(';')) { if ($seg -and (Test-Path (Join-Path $seg 'cublas64_12.dll'))) { $cudaOnPath = $true; break } } }

    if ($missingDll.Count -eq 0) {
        Ok 'CUDA 运行库: 引擎旁边有 cudart64_12 + cublas64_12'
    } elseif ($isBuildTree -and $cudaOnPath) {
        Ok 'CUDA 运行库: 走 CUDA Toolkit 的 PATH(源码树布局)'
    } else {
        Bad ("CUDA 运行库缺失: " + ($missingDll -join ', '))
        Say '         装 CUDA Toolkit 12.8(会放到 PATH),或把它们复制到引擎旁边。'
        Say '         注意:只需要这两个。cublasLt64_12.dll(660 MB)不在导入表里,不用带。'
        [void]$problems.Add('cuda-dll')
    }
}

# ── 5. NVIDIA 驱动(nvcuda.dll)────────────────────────────────────────
$nvcuda = Join-Path $env:SystemRoot 'System32\nvcuda.dll'
if (Test-Path $nvcuda) {
    Ok 'NVIDIA 驱动: nvcuda.dll 存在'
    $smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
    if ($smi) {
        $gpu = (& nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader 2>$null | Select-Object -First 1)
        if ($gpu) { Say "         $gpu" }
    }
} else {
    Bad 'NVIDIA 驱动: 没找到 nvcuda.dll(装好显卡驱动再来)'
    [void]$problems.Add('driver')
}

# ── 6. 模型权重 ────────────────────────────────────────────────────────
$yue2Dir = Join-Path $root 'models\Yue2-3B-GGUF'
$yue2Gguf = @()
if (Test-Path $yue2Dir) { $yue2Gguf = @(Get-ChildItem $yue2Dir -Filter '*.gguf' -ErrorAction SilentlyContinue) }
if ($yue2Gguf.Count -gt 0) {
    $gb = [math]::Round((($yue2Gguf | Measure-Object Length -Sum).Sum) / 1GB, 2)
    Ok "YuE2 权重: $($yue2Gguf.Count) 个 gguf,共 $gb GB"
} else {
    Bad 'YuE2 权重: models\Yue2-3B-GGUF 下没有 .gguf'
    Say '         下载方式见 WEIGHTS.md,或跑 scripts\fetch_weights.py'
    Say '         注意:YuE2 权重没有许可声明,不能由本仓库分发。'
    [void]$problems.Add('weights')
}

$musDir = Join-Path $root 'models\MuScriptor-Small-GGUF'
if (@(Get-ChildItem $musDir -Filter '*.gguf' -ErrorAction SilentlyContinue).Count -gt 0) {
    Ok 'MuScriptor 权重: 在(参考曲分析的第②级可用)'
} else {
    Warn 'MuScriptor 权重: 不在 —— 「参考曲分析」只能用第①级(librosa);第②级会报缺权重'
    Say  '         (它不是必需的。许可是 CC-BY-NC-4.0,禁止商用)'
}

# ── 结论 ───────────────────────────────────────────────────────────────
Say ''
if ($problems.Count -gt 0) {
    Bad ("有 $($problems.Count) 项没准备好:" + ($problems -join ', '))
    # 硬前提 = 缺了界面**根本起不来**。
    #   python / deps -> gui.py 连 import 都过不去
    #   engine        -> 服务能起,但一生成就失败
    # 权重和驱动不在这里:权重缺失由服务层报明确错误,驱动缺失会让引擎自己报
    # CUDA 初始化失败 —— 都比"连界面都打不开"好定位。
    $hard = @($problems | Where-Object { $_ -in @('python', 'deps', 'engine') })
    if ($hard.Count -gt 0) {
        Say ("这几项是硬前提,界面起不来,先解决它们:" + ($hard -join ', ')) Red
        exit 1
    }
    Say '其余问题会让部分功能不可用,但界面仍可以启动。' Yellow
}

if ($CheckOnly) {
    Say '体检完成(未启动服务)。' Cyan
    exit 0
}

# ── 启动 ───────────────────────────────────────────────────────────────
Say ''
Say '正在启动服务与界面 ...' Cyan

if (-not $NoBrowser) {
    # 后台等端口起来再开浏览器 —— 界面本身要十几秒才 listen,
    # 立刻开浏览器只会看到"无法连接"。
    $opener = Join-Path $PSScriptRoot 'open-when-ready.ps1'
    if (Test-Path $opener) {
        $psExe = (Get-Process -Id $PID).Path
        Start-Process -FilePath $psExe -WindowStyle Hidden -ArgumentList @(
            '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $opener,
            '-Url', "http://127.0.0.1:$GuiPort"
        ) | Out-Null
    }
}

# start-gui.ps1 会在前台跑界面(阻塞),所以控制台窗口关掉 = 界面停止。
& (Join-Path $PSScriptRoot 'start-gui.ps1') `
    -Python $python -ServicePort $ServicePort -GuiPort $GuiPort
exit $LASTEXITCODE
