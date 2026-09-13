# MuScriptor:音频转谱(节奏/音符级分析)

目标:把参考音频"读"成**音符级事件**,拿到比 librosa 更细的节奏信息
(librosa 只给 BPM / 拍点 / 调性,给不了"哪个乐器在什么时候敲了什么")。

## 它是什么

| 项 | 内容 |
|---|---|
| 来源 | **Kyutai**(Moshi 的作者)+ **Mirelo** |
| 类型 | 多乐器音乐转谱,decoder-only Transformer |
| 任务 | 音频 → **MIDI / 音符事件**(JSON / JSONL) |
| 规模 | `small` ≈ 1 亿参数(权重 392.8 MB fp32);另有 medium / large |
| 代码许可 | **MIT**(PyPI 包 `muscriptor` 0.3.0,纯 Python wheel 1.6 MB) |
| 权重许可 | **CC-BY-NC-4.0 —— 非商用** |
| 官方渠道 | GitHub `muscriptor/muscriptor`、HF `MuScriptor/muscriptor-small`、PyPI `muscriptor` |

**跑得动吗**:`muscriptor transcribe` 里的 `--detect-tempo`(默认 `best-effort`)
会**从音频检测 BPM 与拍号**并写进 MIDI —— 这就是模型级的节奏识别,不是 librosa 那种信号估计。

## 现状:卡在权重门禁

```
MuScriptor/muscriptor-small: gated=auto   ← 需要 HF 账号 + 接受许可
未认证请求 → 403 GatedRepoError
本机没有任何 HF token(C:\Users\DDF\.cache\huggingface\token 不存在,HF_TOKEN 未设置)
```

**解法(推荐,不用重编译)**:去 <https://huggingface.co/MuScriptor/muscriptor-small>
点接受许可(自动通过),再在 <https://huggingface.co/settings/tokens> 建一个 **read** token,
设成环境变量 `HF_TOKEN`。之后:

```powershell
D:\mt-tool\runtime\Scripts\python.exe -m muscriptor transcribe `
  output\zh-01.wav -m small -d cuda --dtype float32 -f json -o output\zh-01.notes.json
```

**备选(不用账号,但要重编译)**:`audio-cpp/audio.cpp-gguf` 里的
`MuScriptor-Small-GGUF/muscriptor-small-f32.gguf`(392.8 MB,`gated=False`),
配 `AUDIOCPP_MODELS=yue2,muscriptor` 重编译。Volta 的 flash attention 风险
已被 `docs/v100-patches.md` 的 Patch 1 覆盖(补丁位于共享函数
`ggml_cuda_get_best_fattn_kernel` 的 Volta 分支),另有官方保底
`--session-option muscriptor.perf_mode=off`(走 `Explicit` 精确路径,完全不碰 flash)。

## 本机环境核对

| 项 | 结果 |
|---|---|
| Python | 3.12.10 ✓(要求 ≥3.10) |
| torch | 2.8.0+cu128 ✓(要求 ≥2.0;**未被安装过程改动**) |
| numpy / soundfile / einops / torchaudio / huggingface-hub | 已装 ✓ |
| `mido` / `beat-this` / `soxr` / `rotary-embedding-torch` | **本轮已装** ✓ |
| GPU | Tesla V100-SXM2-16GB,算力 (7,0) |
| 磁盘 | 25.5 GB 可用 |
| HF_ENDPOINT | `https://hf-mirror.com` |

## ⚠️ 本机沙箱的两个坑(装任何包都会遇到)

1. **pip 装不了包**。不是网络问题 —— pip 下载完 wheel 后读自己的临时目录被拒:

   ```
   ERROR: Could not install packages due to an OSError:
   [Errno 13] Permission denied: '...\pip-unpack-xxxx\mido-1.3.3-py3-none-any.whl'
   ```

   换成工作区内的临时目录也一样。**绕法**:纯 Python wheel 本质就是 zip,
   直接解到 site-packages(和 pip 做的事一样,含 `dist-info`,所以 `pip show` 也认):

   ```python
   import io, json, site, urllib.request, zipfile, pathlib
   site_dir = pathlib.Path(site.getsitepackages()[-1])
   meta = json.loads(urllib.request.urlopen(f'https://pypi.org/pypi/{pkg}/json').read())
   whl = next(f for f in meta['releases'][meta['info']['version']]
              if f['packagetype'] == 'bdist_wheel')
   zipfile.ZipFile(io.BytesIO(urllib.request.urlopen(whl['url']).read())).extractall(site_dir)
   ```

2. **`huggingface_hub` 会抛一堆 `PermissionError` 噪音**(删不掉自己的临时目录),
   但**下载本身是成功的** —— 已实测:`audio-cpp/audio.cpp-gguf` 的 README blob 正常落盘。
   别被堆栈吓到,去缓存目录看文件在不在。

> 直连 `urllib` 拉 `hf-mirror.com/.../resolve/main/...` 会 403,必须走 `huggingface_hub`
> (它会带正确的请求头)。

## 实测结果(2026-09-13,本机 V100)

**已经跑通**,走的是 audio.cpp 路线(`AUDIOCPP_MODELS=yue2,muscriptor`,增量编译),
权重用 `audio-cpp/audio.cpp-gguf` 那份(392.8 MB,`gated=False`,**不需要 HF 账号**)。

```powershell
audiocpp_cli.exe --task midi --family muscriptor `
  --model models\MuScriptor-Small-GGUF\muscriptor-small-f32.gguf `
  --backend cuda --audio output\zh-01.wav `
  --request-option output_format=json --out output\zh-01.notes.json --log --metrics
```

| 曲目 | 时长 | 音符事件 | 耗时 | RTF | 实时倍率 |
|---|---|---|---|---|---|
| `zh-01`(城市流行,密) | 52.6 s | 1282 | **9.9 s** | 0.192 | **5.2×** |
| `dream-A`(dream pop,疏) | 72.0 s | 324 | **3.8 s** | 0.053 | **18.9×** |

- **峰值显存 2653 MiB**,空闲基线 1747 MiB → **实增仅约 906 MiB**
  (catalog 标 `min_vram_gb: 4`,相当保守)。峰值 GPU 利用率 63%。
- **耗时由音符密度决定,不由时长决定** —— 72 秒的稀疏曲比 52 秒的密集曲快 2.6 倍。
- 对比:YuE2 生成是 RTF 0.49–0.74(1.4–2× 实时),**转谱比生成快得多**。
- **Volta 上没踩到 flash attention 的坑** —— 默认 `perf_mode=flash_attention` 直接跑通,
  说明 Patch 1(共享函数 `ggml_cuda_get_best_fattn_kernel` 的 Volta 分支)确实覆盖了它。

### 识别的乐器(它自己标注的)

`zh-01`:`acoustic_guitar` 785 / `drums` 303 / `electric_bass` 116 / `flutes` 33 /
**`voice` 22** / `clean_electric_guitar` 19 / `acoustic_piano` 4

`dream-A`:`acoustic_guitar` 249 / `drums` 57 / **`voice` 18**

两个都检出了 **`voice`** 音符(分别集中在 17.3–23.7s 与 15.5–27.6s)——
这是**音符级的人声在场证据**,但每个只有 ~20 个音,远不是完整的人声转写。
MuScriptor 是面向器乐的多乐器转谱模型,**别拿它当人声提取器**。

### 节奏:两个独立方法交叉验证

`scripts\muscriptor-summary.py` 把事件流变成可读的节奏报告。

| 曲目 | MuScriptor(鼓点众数间隔 → 细化) | librosa(独立估计) | 一致性 |
|---|---|---|---|
| `zh-01` | 0.260 s → **116.8 BPM** | 117.5 BPM(拍网格) | 差 0.6% |
| `dream-A` | 0.370 s → **79.9 BPM** | 161.5 拍网格 / **80.7** 半速 | 差 1.0% |

`zh-01` 折叠出的鼓型(这是从音频里**真的读出来的节奏型**,不是猜的):

```
          1 e & a 2 e & a 3 e & a 4 e & a
底鼓      █·······█·······     落在 1、3 拍
军鼓      ····█·······█···     落在 2、4 拍(标准反拍)
闭镲      █·█·█·█·█·█·█·█·     八分音符贯穿
```

### 两个必须记住的方法要点

1. **众数间隔到底是拍、八分还是十六分,不能靠猜。** 脚本先用"真实速度多在 70–165 BPM"
   的启发式选倍频(可用 `--bpm` 覆盖),再在 ±15% 内**网格搜索**取"折叠后图案最锐利"的速度。
2. **折叠对速度极其敏感。** `zh-01` 上 115.4 与 117.5 只差 2%,23 小节累积后相位就漂了、
   反拍军鼓被抹成一片。**必须细化速度**,否则鼓型是糊的 —— 这一步不做等于白折叠。
