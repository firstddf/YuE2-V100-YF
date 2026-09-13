# 命令速查

项目根 `D:\mt-tool\yue2-V100`。下文用 `$PY` = `D:\mt-tool\runtime\Scripts\python.exe`,`$ROOT` = 项目根,
`$EXE` = `$ROOT\build\bin\Release\audiocpp_cli.exe`,相对路径都按 `$ROOT` 解析。

## 启停

```powershell
# 启动服务(1414)+ 界面(7860)
pwsh -NoProfile -ExecutionPolicy Bypass -File $ROOT\scripts\start-gui.ps1
# 停止
pwsh -NoProfile -ExecutionPolicy Bypass -File $ROOT\scripts\start-gui.ps1 -Stop
# 健康检查
Invoke-RestMethod http://127.0.0.1:1414/api/health
```

> 注意:`start-gui.ps1` 用 `netstat` 找端口占用者,不是 `Get-NetTCPConnection`(后者在本机返回空,
> 会导致"重启"其实连的还是旧进程)。

## 生成(推荐走服务)

```powershell
$body = @{
  style = 'Mandarin, city pop, warm female vocal, nostalgic'
  lyrics = "[Verse]`n夜色落在窗台上`n[Chorus]`n如果风还记得那年夏天"
  cot = 'off'                # off 最快无谱 / melody 有谱 / full 有谱带和弦
  seed = 831001
  num_inference_steps = 8
  semantic_min_tokens = 450  # 18 秒
  semantic_max_tokens = 500  # 20 秒
  threads = 8
} | ConvertTo-Json -Depth 4
$job = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:1414/api/jobs `
        -ContentType 'application/json; charset=utf-8' -Body $body
$job.id
# 轮询
Invoke-RestMethod "http://127.0.0.1:1414/api/jobs/$($job.id)"
# 取音频 / 乐谱
Invoke-WebRequest "http://127.0.0.1:1414/api/jobs/$($job.id)/audio" -OutFile out.wav
Invoke-RestMethod "http://127.0.0.1:1414/api/jobs/$($job.id)/abc"
```

产物在 `output\gui\jobs\<id>\`:`audio.wav`、`score.abc`(有谱时)、`score.jianpu.txt`、`run.log`、`request.json`。

## 生成(直接调引擎)

```powershell
& $EXE --task gen --family yue2 --model $ROOT\models\Yue2-3B-GGUF `
  --backend cuda --threads 8 `
  --request-option style="Mandarin, city pop, warm female vocal" `
  --lyrics "[Verse]`n夜色落在窗台上`n[Chorus]`n如果风还记得那年夏天" `
  --request-option cot=off --seed 831001 `
  --request-option semantic_min_tokens=450 --request-option semantic_max_tokens=500 `
  --out $ROOT\output\demo.wav --log --metrics
```

- ABC 条件生成:`--request-option cot=melody --request-option abc_file=<path.abc>`
- 从选项里读 `metrics.wall_ms` / `metrics.rtf` / `metrics.audio_duration_ms`

## 分析参考曲

**三条路,效果一样** —— 优先让用户点界面(他能当场听、当场改)、或走接口、或直接跑脚本。

```powershell
# A. 图形界面:「🔍 参考曲分析」页签(第 5 个),上传 → 选级别 → 分析 → 「标签填入单首生成」

# B. HTTP 接口
$body = @{ audio = 'output\zh-01.wav'; level = 'basic' } | ConvertTo-Json   # basic | full
Invoke-RestMethod http://127.0.0.1:1414/api/analyze -Method Post `
  -Body $body -ContentType 'application/json; charset=utf-8'
#   level=basic 约 9 秒(纯 CPU);level=full 约 21 秒(占 GPU ~0.9 GB)
#   队列里有生成任务时 full 会返回 409 —— MuScriptor 和 YuE2 抢同一块卡
#   可选 bpm=<n>:填了鼓型折叠更准

# C. 直接跑脚本
# 一级:librosa,零成本秒级 —— BPM/律动/调性/频谱/动态/空间 + 标签建议
& $PY $ROOT\scripts\analyze-track.py reference.wav --json $ROOT\results\ref.json

# 二级:MuScriptor 转谱 —— 乐器标注 + 音符级 + 真实鼓型
& $EXE --task midi --family muscriptor `
  --model $ROOT\models\MuScriptor-Small-GGUF\muscriptor-small-f32.gguf `
  --backend cuda --audio reference.wav `
  --request-option output_format=json --out $ROOT\output\ref.notes.json --log --metrics
& $PY $ROOT\scripts\muscriptor-summary.py $ROOT\output\ref.notes.json --json $ROOT\output\ref.summary.json
# 鼓型糊了就指定速度(用 analyze-track 的 BPM)
& $PY $ROOT\scripts\muscriptor-summary.py $ROOT\output\ref.notes.json --bpm 117.5
```

**何时需要二级**:只要风格像 → 一级够;想知道"什么乐器、第几拍有底鼓" → 才跑二级。
转谱耗时由**音符密度**决定(52 秒密集曲 9.9 秒;72 秒稀疏曲 3.8 秒)。

**分析结果只有情绪/氛围类标签**。语言与曲风**必须让用户补** —— 语言决定唱什么语言,
测不出来(要 ASR,且对无词吟唱无效)。界面上也是这么提示的。


## 氛围后期

```powershell
& $PY $ROOT\scripts\dream-atmos.py --process   # 跑 ffmpeg 链 + 出对比页
& $PY $ROOT\scripts\dream-atmos.py --measure   # 客观指标验证
& $PY $ROOT\scripts\dream-atmos.py --asr       # whisper 时间戳(人声进出)
```

单条链的实现细节(滤镜串、必踩的坑)见 `mixing.md`。

## 乐谱相关

```powershell
& $PY $ROOT\scripts\abc-to-jianpu.py output\score-full.abc   # 转简谱 .txt
& $PY $ROOT\scripts\abc-to-jianpu.py --all --html            # 批量 + HTML
& $PY $ROOT\scripts\abc-to-jianpu.py --check                 # 只解析查错
& $PY $ROOT\scripts\analyze-abc.py output\score-full.abc     # 各声部音符/休止比例
```

> `analyze-abc.py` 的比例**不能**用来判断"有没有唱"(实测器乐方案的乐谱里人声部照样有旋律线)。

## 气声 / 吟唱实验

```powershell
& $PY $ROOT\scripts\breath-vocals.py            # 生成 6 条气声变体 + 对比页
& $PY $ROOT\scripts\breath-vocals.py --metrics  # 客观指标(高频占比/过零率)
```

## 回归与校验

```powershell
pwsh -File $ROOT\scripts\run-yue2-tests.ps1        # 5 项功能回归 → results\yue2-tests.json
& $PY $ROOT\scripts\test-style-tags.py             # 标签词表逻辑单测
& $PY $ROOT\scripts\gui-smoke-test.py              # 点击级测试(含简谱/历史试听)
& $PY $ROOT\scripts\asr-check.py a.wav b.wav       # ASR 词数对比(仅对"有无唱词"有效)
```

## 排障

| 现象 | 原因 / 处理 |
|---|---|
| `/api/health` 无响应 | 服务没起来 → `start-gui.ps1` |
| 重启后界面还是旧代码 | 端口没被真正杀掉 → 用 `start-gui.ps1 -Stop` 再起 |
| **生成失败,想知道到底怎么了** | 界面「**📜 日志历史**」→ 选任务 → 行数填 **0**(全部);或点「最新失败任务」。服务端还会**先校验参数**并指明哪个越界(如 `abc_max_tokens(0)必须 ≥ abc_min_tokens(32)`) |
| 生成报 `temp file` / 权限错 | 沙箱禁库自管临时目录;`pip` 与 HF 缓存都会报,但**下载本身通常成功**,去目录看文件在不在 |
| `logs\service.out.log` 是空的 | Python stdout 重定向到文件时**块缓冲**;启动脚本已加 `-u` 修正。仍空就看 `service.err.log` |
| 历史里旧任务没有 `request.json` | 早于该功能,音频/乐谱仍可试听 |
| 历史时间看着不对 | 时间取**产物**(`audio.wav`)的 mtime,不是目录 mtime —— 目录时间会被后续写入的文件刷新 |
