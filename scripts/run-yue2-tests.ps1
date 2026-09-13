<#
.SYNOPSIS
    Run the remaining untested YuE2 paths on this V100 and emit a result table.

.DESCRIPTION
    Each test is one audiocpp_cli invocation with its own log. Metrics come from the
    CLI's own --metrics output, so no external timing harness is needed.

    Deliberately does NOT use --backend default: main.cpp:751 defaults to "cpu", which
    would make every test take hours. --backend cuda is always passed.

    Run from an ordinary terminal. Running it inside a sandboxed shell is fine too -
    these are single GPU processes, so parallelism does not apply.

.EXAMPLE
    pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-yue2-tests.ps1
.EXAMPLE
    # only the cheap functional tests
    pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-yue2-tests.ps1 -SkipLong
#>
[CmdletBinding()]
param(
    [string] $Root     = (Split-Path $PSScriptRoot -Parent),
    [string] $CudaRoot = 'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.8',
    [switch] $SkipLong,
    [switch] $SkipBatch
)

$ErrorActionPreference = 'Continue'
$exe   = Join-Path $Root 'build\bin\Release\audiocpp_cli.exe'
$model = Join-Path $Root 'models\Yue2-3B-GGUF'
$outDir = Join-Path $Root 'output\tests'
$logDir = Join-Path $Root 'logs'
$exDir  = Join-Path $Root 'examples'
New-Item -ItemType Directory -Path $outDir -Force | Out-Null

if (-not (Test-Path $exe))   { throw "Missing $exe" }
if (-not (Test-Path $model)) { throw "Missing $model" }
$env:PATH = "$CudaRoot\bin;$env:PATH"

$shortLyrics = "[Verse]`nSoft morning light is touching the window.`nI hear the city waking below.`n[Chorus]`nStay with the rhythm, let it carry us home."
# Long-form: 6 sections. The semantic stage runs until EOS, so more lyrics -> more tokens -> longer song.
$longLyrics = @(
    "[Verse]", "City lights are fading into morning grey.", "Every quiet street remembers what we used to say.", "I keep walking with the echo of a song.", "Somewhere in the distance I can still hear you sing along.",
    "[Chorus]", "Hold the line, hold the light, we are running out of night.", "Every heartbeat is a drum that keeps the rhythm alive.", "Hold the line, hold the light, let the melody decide.", "We are never really lost while the music is our guide.",
    "[Verse]", "Windows turn to gold as the day begins to climb.", "I can hear the harbour bells keeping their own time.", "Nothing here is broken that a little time won't mend.", "Every road is leading back to where the songs begin.",
    "[Chorus]", "Hold the line, hold the light, we are running out of night.", "Every heartbeat is a drum that keeps the rhythm alive.", "Hold the line, hold the light, let the melody decide.", "We are never really lost while the music is our guide.",
    "[Bridge]", "And if the morning finds us far from home.", "We will sing the only words we know.",
    "[Outro]", "Soft morning light is touching the window.", "I hear the city waking below."
) -join "`n"

$tests = [ordered]@{}

$tests['01-full-score-cond'] = @{
    Desc = 'cot=full + 外部带和弦乐谱(score-jazz.abc)'
    Opt  = @{ style='English, piano pop with jazz harmony, clear lead vocal, gentle bass'; lyrics=$shortLyrics; cot='full'; abc_file="$exDir\score-jazz.abc"; num_inference_steps='8'; seed='831001' }
    Out  = 'test-01-full-score.wav'
}
$tests['02-cfg-scale'] = @{
    Desc = 'cfg_scale=2.0(开启无条件分支,显存与耗时上升)'
    Opt  = @{ style='English, indie pop, warm lead vocal'; lyrics=$shortLyrics; cot='off'; cfg_scale='2.0'; num_inference_steps='8'; seed='831001' }
    Out  = 'test-02-cfg.wav'
}
$tests['03-sampling'] = @{
    Desc = '采样参数细调 temperature=1.3 top_p=0.9'
    Opt  = @{ style='English, indie pop, warm lead vocal'; lyrics=$shortLyrics; cot='off'; temperature='1.3'; top_p='0.9'; num_inference_steps='8'; seed='831001' }
    Out  = 'test-03-sampling.wav'
}
$tests['04-seed-repro'] = @{
    Desc = '同 seed 复现性(与 test-02 不同,单跑对照)'
    Opt  = @{ style='English, indie pop, warm lead vocal'; lyrics=$shortLyrics; cot='off'; num_inference_steps='8'; seed='831001' }
    Out  = 'test-04-seed.wav'
}
if (-not $SkipLong) {
    $tests['05-long-song'] = @{
        Desc = '长曲(6 段歌词,目标 2-3 分钟;最大风险项)'
        Opt  = @{ style='English, cinematic pop, warm male lead vocal, piano, strings, live drums'; lyrics=$longLyrics; cot='off'; num_inference_steps='8'; seed='831001' }
        Out  = 'test-05-long.wav'
    }
}

$results = New-Object System.Collections.ArrayList

# TIMING lines are space separated ("yue2.semantic.tokens 1015"); metrics lines use "=".
function Get-Metric([string] $text, [string] $key) {
    $m = [regex]::Match($text, [regex]::Escape($key) + '[\s=]+([0-9.]+)')
    if ($m.Success) { return [double]$m.Groups[1].Value }
    return $null
}

foreach ($name in $tests.Keys) {
    $t = $tests[$name]
    $log = Join-Path $logDir "test-$name.log"
    $out = Join-Path $outDir $t.Out
    if (Test-Path $log) { Remove-Item $log -Force }
    if (Test-Path $out) { Remove-Item $out -Force }

    $argv = @('--task','gen','--family','yue2','--model',$model,'--backend','cuda','--threads','8')
    foreach ($k in $t.Opt.Keys) { $argv += @('--request-option', "$k=$($t.Opt[$k])") }
    $argv += @('--out', $out, '--log', '--metrics')

    Write-Host ("`n=== {0} :: {1}" -f $name, $t.Desc) -ForegroundColor Cyan
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    & $exe @argv *> $log
    $code = $LASTEXITCODE
    $sw.Stop()

    $text = if (Test-Path $log) { Get-Content $log -Raw } else { '' }
    $err = [regex]::Match($text, '"message"\s*:\s*"([^"]+)"')
    $audioSec = if (Test-Path $out) { (Get-Item $out).Length } else { 0 }
    $audioMs = Get-Metric $text 'metrics.audio_duration_ms'

    $row = [pscustomobject]@{
        Test      = $name
        Exit      = $code
        WallS     = [math]::Round($sw.Elapsed.TotalSeconds, 1)
        AudioS    = if ($audioMs) { [math]::Round($audioMs/1000, 1) } else { $null }
        RTF       = Get-Metric $text 'metrics.rtf'
        ABCtokens = Get-Metric $text 'yue2.semantic.abc_generated_tokens'
        SemTokens = Get-Metric $text 'yue2.semantic.tokens'
        MB        = [math]::Round($audioSec/1MB, 2)
        Error     = if ($err.Success) { $err.Groups[1].Value } else { '' }
    }
    [void]$results.Add($row)
    Write-Host ("    exit={0} wall={1}s audio={2}s rtf={3} mb={4} {5}" -f $row.Exit, $row.WallS, $row.AudioS, $row.RTF, $row.MB, $row.Error)
}

Write-Host "`n=== SUMMARY ===" -ForegroundColor Green
$results | Format-Table -AutoSize | Out-String -Width 200 | Write-Host
$results | ConvertTo-Json -Depth 4 | Set-Content (Join-Path $Root 'results\yue2-tests.json') -Encoding UTF8
Write-Host ("saved: {0}" -f (Join-Path $Root 'results\yue2-tests.json'))
