# yue2-V100

**版本 0.1**(见 `VERSION` / `CHANGELOG.md`)

在 Windows + Tesla V100(SM70,16 GB)上本地运行 **YuE2** 音乐生成模型,
外加 **MuScriptor** 参考曲分析,配自建中文图形界面。

## 结论摘要

| 项 | 结论 |
|---|---|
| 路线 | **自编译 [audio.cpp](https://github.com/0xShug0/audio.cpp) (dev 分支) + GGUF 量化权重** |
| 为什么不用官方 Python 路线 | YuE2 硬检查 BF16,V100 无此硬件;且 PyTorch 2.10 的 cu128 wheel 已移除 Volta |
| 为什么不用预编译包 | audio.cpp 的 Windows CUDA 预编译包要求 CUDA 13 + 驱动 580+,**不覆盖 Volta** |
| 权重档位(已定) | **Q8_0 + VAE f16**,共 4.22 GiB(官方默认包) |
| 构建方式(已定) | 手工 cmake,**Visual Studio 17 2022** 生成器,单架构 `70-real`,Release |
| **编入的模型家族** | **`yue2,muscriptor`** —— 生成 + 参考曲转谱 |
| 环境检测 | **READY WITH WARNINGS**(PASS 16 / WARN 3 / FAIL 0),见 `docs/preflight-plan.md` |
| 本地补丁 | 2 处:`fattn.cu` 的 Volta 闪存注意力 bug + ABC 乐谱导出,见 `docs/v100-patches.md` |
| **图形界面** | **双击 `start.bat` 一键启动**(自检 → 起服务 → 开界面 → 开浏览器)→ **http://127.0.0.1:7860**(服务 1414),见 `docs/gui.md` |
| **DSH 技能** | `skills\song-production\` —— 让 DSH 按固化流程与边界做歌,见 `docs/skill.md` |
| 实测实时倍率 | 出歌 **1.4–2×**;转谱 **5.2–18.9×** |
| 磁盘占用 | audio.cpp 204 MB + 构建 605 MB + 权重 4.7 GiB;D: 剩约 25 GB |

**本机实测关键项**:`Tesla V100-SXM2-16GB` / CC 7.0 / 驱动 572.83 / nvcc 12.8.61 且 `compute_70` 可用 /
MSVC 14.44.35207(`_MSC_VER` 1944,低于 CUDA 12.8 的 1950 上限)。

> 注:实际是 **SXM2** 版而非 PCIe 版。软件路径完全一致(同为 SM70 / 16 GB),仅带宽与 FP16 算力略高。

## 已验证的配置结果

```
生成器        Visual Studio 17 2022 (x64, Release only)
C/CXX         VS2022 BuildTools cl.exe 14.44.35207
CUDA          nvcc 12.8.61, host MSVC 19.44.35220.0
CUDAToolkit   v12.8   <- 确认不是同机安装的 v13.1
架构          70-real <- 只编 V100 一层,不含默认的 10 个架构
模型族        custom selected [yue2] linked [yue2]
ggml          0.12.0 @ 87544b5 (与仓库 HEAD 一致)
```

## 目录结构

```
yue2-V100/
├─ README.md                  本文件(主文档;文档索引见 docs\README.md)
├─ CHANGELOG.md               版本变更(当前 0.1)
├─ start.bat                  **一键启动**(双击即可,体检 → 起服务 → 起界面 → 开浏览器)
├─ stop.bat                   **一键停止**(双击即可,杀 1414 / 7860 / 引擎进程)
├─ VERSION                    版本号
├─ audio.cpp/                 源码(dev @ 87544b5)+ 2 处本地补丁
├─ build/                     VS 2022 产物 → build\bin\Release\audiocpp_cli.exe
├─ app/
│  ├─ yue2_service.py         自建服务(端口 1414):任务队列 / 阶段进度 / 历史 / 乐谱 / 分析 / 日志
│  ├─ abc_jianpu.py           ABC 记谱法 → 简谱(调号/八度点/减时线/附点,按小节对齐声部)
│  └─ gui.py                  Gradio 中文前端(端口 7860),6 个页签
├─ docs/
│  ├─ README.md               文档索引 + 代码位置 + 术语
│  ├─ skill.md                DSH 技能:是什么 / 怎么装 / 怎么改
│  ├─ muscriptor.md           参考曲转谱:性质 / 许可 / 实测效率 / 两个陷阱
│  ├─ preflight-plan.md       检测计划 + 实测 + 配置阶段记录
│  ├─ v100-patches.md         两处本地补丁的根因 / 取舍 / 回退命令
│  └─ gui.md                  自建 GUI 的组成、接口、阶段映射、日志与诊断
├─ skills/song-production/    DSH 技能的**规范副本**(用脚本安装到 .dsh\skills)
├─ scripts/
│  ├─ check-env.ps1           环境检测(只读,可重复执行)
│  ├─ configure.ps1           cmake 配置(不含编译),-Models 可加模型家族
│  ├─ fetch_weights.py        权重下载(headless,按字节校验)
│  ├─ apply-abc-patch.ps1     ABC 导出补丁(幂等,支持 -Revert)
│  ├─ install-skill.ps1       安装 DSH 技能(-Check 只查同步)
│  ├─ run-yue2-tests.ps1      功能回归测试 → results\yue2-tests.json
│  ├─ abc-to-jianpu.py        ABC → 简谱 .txt 导出 / 批量检查
│  ├─ analyze-track.py        音频 → 速度/律动/调性/频谱 + 风格标签建议(librosa,零安装)
│  ├─ muscriptor-summary.py   转谱事件 → 乐器统计 / 节奏 / 真实鼓型(--json 给服务端)
│  ├─ dream-atmos.py          氛围后期链(朦胧 / 迷离 / 若即若离 / 轻抚)
│  ├─ breath-vocals.py        气声 / 喘息实验
│  ├─ gui-smoke-test.py       点击级测试(含简谱与历史试听)
│  ├─ launch.ps1              启动前的环境体检 + 启动(被根目录 start.bat / stop.bat 调用)
│  ├─ open-when-ready.ps1     轮询 7860 直到能连上,再开浏览器(避免"无法连接")
│  └─ start-gui.ps1           启停服务 + 界面(被 launch.ps1 调用,也可单独跑)
├─ examples/                  中文歌词、上游官方 melody.abc / score-jazz.abc、批量歌词、225 条标签词表
├─ results/                   检测报告、回归测试、参考曲画像 JSON
├─ models/                    Yue2-3B-GGUF(Q8_0 + VAE f16)+ MuScriptor-Small-GGUF(f32)
├─ output/                    生成结果(含 tests\、gui\jobs\、analysis\、各实验目录)
├─ cache/                     本项目的 HF 缓存
└─ logs/                      构建、下载、生成、服务日志(界面「📜 日志历史」可浏览)
```

> **⚠️ 清理时注意**:`output\breath\` `output\dream\` `output\vocal-*` `output\instr-*`
> `output\gui\jobs\` 都是**文档与试听页引用的证据**,别当垃圾删。
> `output\gui\preview\` 是 jobs 的副本,可随时删(界面会按需重建)。

## 许可与再分发

本项目以 **GPL-3.0-or-later** 分发(见 `LICENSE`)。目标是**允许使用与商用,但不允许闭源再分发**。

| 组件 | 许可 | 与 GPLv3 |
|---|---|---|
| 本项目代码 | GPL-3.0-or-later | — |
| **audio.cpp**(引擎,不随仓库分发) | **Apache-2.0**(Copyright 2026 ShugoAI LLC) | ✅ 兼容 |
| ggml / cJSON / cpp-httplib / libyaml | MIT | ✅ |
| sentencepiece | Apache-2.0 | ✅ |
| CUDA 运行库(发布包内) | NVIDIA CUDA EULA | 与 GPL 无冲突(独立程序) |

**我们改了 audio.cpp 的 6 个文件(+90/−1 行)** —— 改动声明在 `NOTICE`,
精确补丁在 `patches/`,拉取上游固定提交的脚本是 `scripts/fetch-audio-cpp.ps1`。
按 Apache-2.0 §4(b),分发含改动的衍生作品必须声明改过;`NOTICE` 就是那份声明。

### ⚠️ 权重不在本仓库,且许可各不相同

**本仓库不分发任何模型权重。** 详见 `WEIGHTS.md`,其中两条必须知道:

- **MuScriptor 权重是 CC-BY-NC-4.0(禁止商用)**,与 GPLv3 **不兼容**(GPLv3 §7 禁止附加进一步限制)
- **YuE2 权重没有任何许可声明** —— 官方模型卡、第三方镜像、以及 `audio-cpp` 自己的许可表里
  **都没有 YuE2 这一行**。无许可 = 保留所有权利,**不能推定可以再分发**

所以仓库只提供**下载指引**,让用户各自接受各自的条款。

### 发布:两个包,合计 205 MB

```powershell
pwsh -File .\scripts\make-release.ps1            # 生成两个包
pwsh -File .\scripts\make-release.ps1 -DryRun    # 只看会打包什么
pwsh -File .\scripts\make-release.ps1 -SkipAudioCppSource   # 只要运行包
```

| 产物 | 体积 | 给谁 |
|---|---|---|
| `yue2-v100-v0.1-win-x64-v100.zip` | **100.7 MB** | **只想跑的人**(解压 138.8 MB) |
| `yue2-v100-v0.1-audio.cpp-source-patched.zip` | **104.0 MB** | **想自己编译的人** + GPL 要求的对应源码(解压 204.0 MB) |

**运行包内容:**

| 内容 | 体积 |
|---|---|
| 本项目代码 + 文档 + 补丁 | 0.7 MB |
| **`bin\audiocpp_cli.exe`**(已编译,免去用户 30 分钟编译) | 29.1 MB |
| **`bin\cudart64_12.dll` + `cublas64_12.dll`** | 109.0 MB |
| `bin\CUDA-EULA.txt`(再分发要求随附) | 0.06 MB |
| `requirements.txt` / `README-RELEASE.txt` / `LICENSE` / `NOTICE` / `WEIGHTS.md` | 小 |

> **为什么不是 800 MB**:exe 的 **PE 导入表**里只有 `cudart64_12.dll` 和 `cublas64_12.dll`,
> **没有 `cublasLt64_12.dll`** —— 那一个文件就 660 MB。按导入表打包,体积差了 5 倍。

**源码包**是 `audio.cpp` 工作树(固定提交 `87544b5`)**已打好本项目 2 个补丁**、去掉 `.git`(95 MB)后的整棵树:

- **为什么不裁剪**:裁掉 47 MB 测试音频确实能省,但裁剪是"版本出问题"的头号来源。整棵树 204 MB → 51% 压缩后 104 MB,这个代价换"绝对一致"值得。
- **为什么单独一个包**:用预编译 exe 的人根本不需要源码;而 GPLv3 §6(d) 只要求"在**同一处**可获得对应源码" —— 放在同一个 Release 页面即可。
- 包内有 `SOURCE-NOTES.txt` 写明上游、提交号、改了哪 6 个文件,以及如何用 `patches\*.patch` 反向复核这棵树确实与补丁一致。

用户自备三样:**NVIDIA 驱动**、**Python 3.10+ 与 `requirements.txt`**、**模型权重**(约 4.7 GB)。

> 📌 **发布包放 GitHub Releases,不要提交进 git 仓库** —— 200 MB 二进制会永久留在历史里,
> 每次 clone 都得拖它。Release 附件无此问题。`.gitignore` 已排除 `audio.cpp/`,仓库里只有自己的代码。

> 想在**仓库内**钉死版本而不是靠 Release?可以用 git submodule 记录同一个提交号,
> 但那样仍需用户自己跑 `git apply patches\*.patch` —— 源码包已经把这一步做完了。

## 三个必须先处理的警告

1. **CUDA 13.1 与 12.8 共存** —— 配置时必须同时写死 `CUDAToolkit_ROOT` 和 `CMAKE_CUDA_COMPILER` 指向 v12.8。
   (CUDA 13 编不出 sm_70;且 CUDA 13 程序需要驱动 ≥580,本机 572.83 也跑不了。)
2. **`CUDA_PATH` 为空** —— 由 `configure.ps1` 显式传参规避,未做系统级 `setx`。
3. **`HF_HOME` 指向只剩 5.8 GB 的 E 盘** —— 下载权重时改用本项目 `cache/` 目录。

## 两个环境坑(已规避)

- **Ninja 在本执行环境挂死**:PATH 上的 ninja 是 `jobserver-pipe` 构建(走命名管道),换成 VS 自带的 1.12.1 同样挂死。已改用 Visual Studio 生成器;该生成器还顺带把 VS 版本钉死在 2022,绕开 VS 18 的 `_MSC_VER` 问题。
- **VS 18 会被自动选中**:`vswhere -latest` 返回 VS 18 Community,而 CUDA 12.8 拒绝 `_MSC_VER >= 1950`。VS 生成器已从机制上排除。

## 复检环境

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\check-env.ps1
```

## 下载权重(Q8_0 + VAE f16,共 4.22 GiB)

两种方式等价,底层都是 `hf_hub_download` API。

> ⚠️ **不要用 `hf` CLI。** `D:\mt-tool\runtime` 里 huggingface_hub 1.13.0 配 typer 0.16.0,CLI 在导入期就崩:
> `TypeError: Typer.__init__() got an unexpected keyword argument 'suggest_commands'`。
> 下载 API 本身没问题,坏的只是 CLI 入口。

### 方式 A:现成的 hf-download GUI

`D:\mt-tool\hf-download\ad.bat`(用 PATH 上的 `python` = `D:\Python\Python312`,hub 0.26.2,不受上述问题影响)。

- 镜像框保持 `https://hf-mirror.com`
- 保存位置填 `D:\mt-tool\yue2-V100\models`
- URL **必须写 `huggingface.co`**:`add_url()` 会校验 netloc 含 `huggingface.co`,粘贴 hf-mirror.com 链接会被拒绝(镜像由脚本内部 `HF_ENDPOINT` 生效)
- 整仓库模式会连 demo 音频一起下(代码里只 ignore 了 `*.safetensors`,约多 150 MB);要精确只下这 6 个文件就逐个粘贴单文件 URL
- 文件落在 `models\audio-cpp_Yue2-3B-GGUF\`(脚本会自动追加 `<org>_<repo>` 子目录)

### 方式 B:headless(已实测通过)

```powershell
D:\Python\Python312\python.exe scripts\fetch_weights.py `
  --local-dir D:\mt-tool\yue2-V100\models\Yue2-3B-GGUF
```

只下 loader 真正读取的 6 个文件,下载后按字节数逐项校验。文件直接落在 `--local-dir`,不再套子目录。

> 两者产出的 `--model` 参数不同:A 用 `models\audio-cpp_Yue2-3B-GGUF`,B 用 `models\Yue2-3B-GGUF`。

## 状态

- [x] 环境检测(READY WITH WARNINGS)
- [x] 固定工具链并克隆源码(dev @ 87544b5)
- [x] cmake 配置(CUDA 12.8 / VS 2022 / sm_70 / 仅 yue2)
- [x] 编译 `audiocpp_cli`(在普通终端并行跑;我用单核增量重编过补丁)
- [x] 下载 Q8_0 权重(4.22 GiB,字节校验通过)
- [x] 首次生成并记录性能基线
- [x] 补丁 1:Volta 闪存注意力崩溃(`fattn.cu`)
- [x] 补丁 2:ABC 乐谱导出(BPE detokenize + `text_output`)
- [x] 功能回归测试 6 项(`scripts\run-yue2-tests.ps1`),含长曲 3 分 15 秒
- [x] 自建中文服务 + Gradio 前端(1414 / 7860)
- [ ] 热态引擎(免去每首 5–6 秒重载)

## 性能基线(Tesla V100-SXM2-16GB,实测)

统一条件:Q8_0 + VAE f16、`cot=off`、`num_inference_steps=8`、seed 831001、`--backend cuda --threads 8`。

| 指标 | 实测 |
|---|---|
| 输出 | **40.60 秒** / 48 kHz / 立体声 |
| 端到端耗时 | **25.7 秒**(第二次 26.0 秒) |
| RTF | **0.63**(约 1.6× 实时) |
| AR 语义生成 | 18.56 秒 / 1015 tokens → **54.7 tok/s** |
| NAR 声学合成 | 3.51 秒 |
| VAE 解码 | 1.08 秒 |
| 权重上传等初始化 | 约 2.2 秒 |
| **峰值显存** | **6137 MiB**(桌面基线 1187 MiB,余量充足) |
| 可复现性 | 同 seed 两次输出 **SHA256 完全一致** |
| 音频有效性 | mean −17.4 dB / max −0.5 dB(真实内容,非静音) |

参照 vendor 在 **RTX 4090** 上的数据:**139.48 tok/s**、3.6 分钟歌 71 秒(RTF 0.33)。
即 V100 的 AR 阶段约为 4090 的 **1/2.5**,端到端约 1/1.9。

**显存结论:默认 arena 全部够用**,不需要下调 `yue2.*_graph_arena_mb` 或 `model_weight_context_mb`。

## 功能矩阵(实测)

| 功能 | 状态 | 证据 |
|---|---|---|
| 风格 + 歌词 → 完整歌曲 | ✅ 可用 | 40.6–65.0 秒输出 / 48 kHz 立体声 / mean −17 dB |
| 确定性 seed | ✅ 可用 | 同 seed 两次输出 SHA256 完全一致 |
| `num_inference_steps` | ✅ 可用 | 8 步(RTF 0.63)与默认 32 步(RTF 0.74)均测 |
| `--metrics` 性能统计 | ✅ 可用 | 直接给出 wall / audio / rtf / x_realtime |
| HTTP API + 内嵌 WebUI | ✅ 可用 | `http://127.0.0.1:8080`,API 实测出歌 |
| **乐谱条件生成** `cot=melody` + `abc` / `abc_file` | ✅ 可用 | 315 字节 `.abc` → 219 tokens → 22.4 秒音频,RTF 0.47 |
| `cfg_scale` 文本引导 | ✅ 可用 | `cfg_scale=2.0` 出 52.2 秒音频,RTF 0.57(略慢于单分支) |
| 采样参数细调 | ✅ 可用 | `temperature=1.3 top_p=0.9` 正常 |
| **批量出歌** | ✅ 可用 | `--batch-text-file` 3 行 → `line_1/2/3.wav`,222 秒跑完 |
| **长曲** | ✅ 可用 | 3 分 15 秒(194.7 秒)音频,峰值显存 **7985 MiB / 16384**,无 OOM |
| **符号规划** `cot=full` | ✅ 可用 | 规划阶段生成 509–874 个 ABC token,可导出完整乐谱 |
| **导出可编辑 ABC 乐谱** | ✅ **已补齐**(本地补丁 2) | 中文歌导出 835 字节带和弦乐谱;导出→喂回往返验证通过 |
| **自建中文 GUI** | ✅ 可用 | `http://127.0.0.1:7860`(服务 1414),见 `docs/gui.md` |

### 完整测试结果(`scripts\run-yue2-tests.ps1`)

| 测试 | 耗时 | 音频 | RTF | 语义 tokens | 显存峰值 |
|---|---|---|---|---|---|
| `cot=full` + 带和弦乐谱 | 13.0 s | 21.7 s | 0.48 | 543 | — |
| `cfg_scale=2.0` | 32.9 s | 52.2 s | 0.57 | 1304 | — |
| 采样参数细调 | 32.2 s | 51.0 s | 0.57 | 1275 | — |
| 同 seed 对照 | 32.4 s | 51.0 s | 0.58 | 1275 | — |
| 长曲 | 100.5 s | 194.7 s | 0.49 | 4867 | **7985 MiB** |
| 批量 3 首 | 222.4 s | 429 s 合计 | ~0.52 | — | — |
| 短曲基线(8 步) | 25.7 s | 40.6 s | 0.63 | 1015 | 6137 MiB |

全部 `exit=0`,12 个产物 WAV 均通过 ffmpeg 音量校验(mean −14.6 ~ −19.1 dB)。

### 中文歌曲(已实测)

歌词:`examples\zh-lyrics.txt`(6 行结构,89 字符);风格标签 `Mandarin, city pop, bright electric piano, groovy bass, live drums, warm female vocal, nostalgic`。

**两条链路都能正确送达中文,且产物字节完全一致**:

| 链路 | prefix_tokens | 产物 SHA256(前 16) |
|---|---|---|
| CLI `--request-option lyrics=<中文>`(走 argv) | 107 | `DE42033DF3B230B9` |
| HTTP API(UTF-8 JSON body) | — | `DE42033DF3B230B9` |

| 分词对照 | prefix_tokens |
|---|---|
| 英文歌词(同 6 行结构) | 78 |
| 中文歌词(89 字符) | **107** |

结论:**Windows 下 argv 传中文没有损坏**,CLI 与 WebUI/API 都可直接用中文。
风格标签建议保持 ASCII(如 `Mandarin, city pop, ...`)—— **`Mandarin` 这个标签是语言选择的关键**。

产物:`output\zh-01.wav`,52.60 秒 / 48 kHz / 立体声 / mean −15.8 dB。

> ⚠️ **文本正确 ≠ 演唱正确。** 上面只证明了歌词文本正确送达模型且音频有效;
> 唱词是否清晰、是否真的是中文发音,需要人耳判断。也可以用仓库里现成的 ASR
> (`FunASR` / `whisper_mimo`)做客观转写对照。

### ✅ 关于"导出可编辑乐谱"(已解决)

YuE2 的招牌能力是"先生成乐谱、再渲染成音频,而乐谱可读可改"。原始移植版把生成的乐谱丢掉了:

- 规划确实执行(`cot=full` 实测 509–874 个 ABC token),但 `pipeline.cpp:140` 只在**输入**
  提供 ABC 时写 `out.abc`;`session.cpp:234` 只设置 `audio_output`;
- 根因是 `Yue2TextTokenizer` **只有 `encode()`,没有 `decode()`**。

**已由 `scripts\apply-abc-patch.ps1` 补齐**(10 处锚点 / 6 个文件 / 49 行新增):
加 BPE detokenize + 把乐谱写进框架本就存在的 `TaskResult.text_output`。
有利条件是 `id_to_token` 反查表在词表加载时已经建好(`tokenizer_text.cpp:128`)。

实测:

```
cot=full --text-out score.abc  →  824 字节合法 ABC
X:1  M:4/4  L:1/16  Q:1/4=106  K:F
V: Vocal / V: Ins
"Gm7"fz8z3"C7sus4"z2ez|"C7"z6c'z3bz4z|"Fmaj7"ez8z3"Dm7"z4|
```

**往返验证**:导出的谱用 `abc_file` 喂回去 → `plan.abc_tokens 510`(对应生成时的 509)→ 正常出歌。
即"生成谱 → 改谱 → 重渲染"闭环可用。

> `cot=off` 不产生乐谱,此时传 `--text-out` 会报 `task result has no text output` ——
> 服务层已按此规则只在 `cot=melody/full` 时请求导出。

## 图形界面(自建)

audio.cpp 自带的 WebUI 是给 60+ 模型族共用的通用界面,YuE2 在里面只有 22 行配置、
没有中文翻译、也没有批量队列 / 阶段进度 / 乐谱导出。所以本项目自带一层服务 + 中文前端:

### 一键启动

**双击根目录的 `start.bat`**。它按顺序做四件事:

1. **体检** —— Python、9 个依赖、引擎 exe、CUDA 运行库、NVIDIA 驱动、YuE2 / MuScriptor 权重。
   缺什么当场说清缺什么、去哪儿补,而不是启动到一半抛一句看不懂的错。
2. 起服务(1414),等 `/api/health` 真的通了才继续。
3. 起界面(7860)。
4. 轮询 7860,能连上了**才**开浏览器 —— 界面本身要十几秒才 listen,抢跑只会看到"无法连接"。

关掉那个控制台窗口 = 停止;或**双击 `stop.bat`**(杀 1414 / 7860 / 残留的 `audiocpp_cli.exe`,释放显存)。

```bat
start.bat -CheckOnly      :: 只体检,不启动
start.bat -NoBrowser      :: 不自动开浏览器
start.bat -ServicePort 1415 -GuiPort 7861   :: 换端口
stop.bat                  :: 停止
```

> `start.bat` / `stop.bat` 本身只有 30 行,是**纯 ASCII**的瘦启动器 —— 中文全在
> `scripts\launch.ps1` 里。原因:`.bat` 由 cmd.exe 按代码页(936 / 65001)解码,
> 中文写进 `.bat` 在不同机器上会变成乱码;PowerShell 读写 UTF-8 没这个问题。
> 两个 `.bat` 优先用 `pwsh`,没有就退回 Windows 自带的 `powershell` 5.1。

### 手动启动(不给别人用,自己调试时)

```
scripts\start-gui.ps1          # 启动(服务 + 界面),不做体检
scripts\start-gui.ps1 -Stop    # 停止
```

- **界面:http://127.0.0.1:7860**
- 服务:http://127.0.0.1:1414(自带 `/docs` 交互文档)

组成:`app/yue2_service.py`(任务队列 / 阶段进度 / 历史 / 乐谱)+ `app/gui.py`(Gradio 中文界面)。
完整说明见 **`docs/gui.md`**。

> 引擎调用方式:每个任务 spawn 一个 `audiocpp_cli.exe`,每首多 5–6 秒模型加载;
> 换来无状态与**真实的阶段进度**。热态引擎留作后续升级。

### 生成失败时怎么查

失败时界面**必然摊开三样东西**:失败原因、**实际下发的参数表**、引擎日志尾部(40 行,日志框自动可见)。

| 失败类型 | 能看到什么 |
|---|---|
| 提交期被拒(参数越界) | 参数表最重要 —— 任务没创建,没有引擎日志 |
| 引擎期失败(跑到一半才挂) | 参数表 + 完整日志尾部 |

**根因修复**:`abc_max_tokens` / `abc_temperature` / `semantic_temperature` /
`semantic_repetition_penalty` 这四项是"留空即默认"的数字框,但**浏览器里没动过的
`gr.Number` 提交的是 0 而不是 None**。而 `abc_max_tokens=0` 会撞上
`max_tokens >= min_tokens(32)`、`semantic_repetition_penalty=0` 会撞上 `> 0` ——
于是"什么都没填"反而报 `Yue2 abc sampling options are invalid`(引擎还不说是哪个参数)。
现在这四项**把 0 当"没填"**,并且服务端**先校验再落任务**(400 + 说清哪个参数越界,
不再等加载完模型才失败)。

### 参考曲分析(界面第 5 个页签)

**MuScriptor 是后加的,当时只接了命令行** —— GUI 更早是按"YuE2 生成器"写的,于是出现
"界面只能生成、分析只能敲脚本"的断层。现在补上了:

**上传参考曲 → 选级别 → 分析 → 「标签填入单首生成」→ 生成一首类似的。**

| 级别 | 内容 | 耗时 |
|---|---|---|
| ① 快速 | librosa:速度/律动/调性/频谱/动态/空间 + 标签建议 | **约 9 秒**,纯 CPU |
| ② 完整 | 再加 MuScriptor 转谱:乐器标注 + 音符级 + 真实鼓型 | **约 21 秒**,占 GPU ~0.9 GB |

服务端接口 `POST /api/analyze`(`audio` / `level` / 可选 `bpm`),产物落 `output\analysis\<名>\`。

三个设计要点:

- **GPU 互斥**:`level=full` 时若队列里有生成任务,直接返回 **409** ——
  MuScriptor 和 YuE2 用同一块卡,与其让两边抢显存,不如明确说清楚。
- **标签是"并入"不是"覆盖"**:原有风格保留,新标签按类归位
  (`ambient`→genre、`groovy/danceable`→mood),因为你原来的 `Mandarin, city pop` 不该被冲掉。
- **只给情绪/氛围类标签**:语言与曲风**必须你补** —— 语言决定唱什么语言,而这是测不出来的。

> ⚠️ 页面上写明:**分析只给客观特征**;"朦胧、迷离、轻抚"这类**氛围判断只能你听**。

### 历史记录(重启不丢,点一下就能听)

历史任务**从磁盘恢复**:服务启动时扫描 `output\gui\jobs\`,把音频、乐谱、指标、请求参数一起还原,
所以重启服务不会清空列表。试听有两种方式:**点击表格任意一行**,或从「选择历史任务」下拉里选一个
(选中即试听)。每个任务目录额外写入 `request.json`,连当时用的风格与歌词也能看到。

> 清理旧记录 = 删掉对应的 `output\gui\jobs\<任务id>\` 目录。早于本次改动生成的目录没有
> `request.json`,请求信息为空,但音频/乐谱仍可正常试听与查看。

### 时长怎么控制(没有直接参数)

YuE2 **没有时长参数**,一直生成到自然结束(EOS),所以**实际时长主要由歌词长度决定**。
界面上给的是两个边界(最长/最短时长),它们映射到 `semantic_max_tokens` / `semantic_min_tokens`。

实测换算是精确的:**时长(秒)= 语义 tokens ÷ 25**(1 token = 1 潜帧 = 40 ms)。
7 组实测全部命中 25.000;反方向验证:设"最长 20 秒" → 下发 500 tokens → 输出正好 20.0 秒。
默认上限 9000 tokens = **6 分钟**。

> ⚠️ 通用参数 `--duration-seconds` 对 YuE2 **无效**:实测请求 10 秒仍输出 34.9 秒且 exit=0,
> 该值被静默忽略(未知选项不校验)。

### 风格:分类下拉多选(选项带中文解释)

**风格是自由文本,不是固定枚举** —— 上游原文:*our tags have an open vocabulary*。
界面按上游推荐的五要素做成**分类下拉多选**,选项显示为「english · 中文」:

| 类别 | 数量 | 说明 |
|---|---|---|
| **语言** | 11 | **最关键**,`Mandarin` / `Cantonese` / `Japanese` / `English`… 决定唱什么语言 |
| 曲风 / 乐器 / 情绪 / 人声 / 音色 | 71 / 48 / 52 / 9 / 34 | 含中文标签(民谣、摇滚、二胡、怀旧、抒情…) |

**中文只是显示层**:下拉显示 `city pop · 城市流行`,但选中的值与下发给模型的始终是**纯英文标签**,
因为模型的 `[Tags]` 段是按英文标签训练的。

- 选择变化 **立即重算风格框**;**风格框仍可手改**;表外的词写进「自定义补充」
- 「常用组合」下拉会**反向解析回各类选择**,不是覆盖文本框
- 词表:`examples/style_tags.json`(225 标签)+ `examples/style_tags_zh.json`(中文对照)
- `scripts\test-style-tags.py` 保证:每个英文标签都有中文解释(198/198)、默认风格全部命中词表、
  9 个预设零残留、往返一致

> 屏幕占用:225 个复选框铺开 → 6 个折叠下拉框(页面字节数不变,省的是屏幕高度)。

### 纯器乐(没有歌词)怎么做

**歌词必填**(留空或空白都会被拒)。官方做法(上游 issue #18 维护者回复,提问者确认有效):
**保留 `[verse]`/`[chorus]`/`[outro]` 段落标签、把歌词行留空**,并从风格里去掉所有人声标签 ——
段落标签同时满足"歌词非空"的校验。界面上的 **「🎹 纯器乐模式」** 按钮一键完成。

**验证靠 ASR 对比,不能靠耳朵也不能靠乐谱**:

| 输入 | whisper 转写词数 |
|---|---|
| 有唱的曲子(对照,歌词已知) | **251**(与歌词逐字吻合) |
| 官方空歌词方案 | 15 |
| 歌词写 `[Instrumental]` | 6 |

后两条都是 whisper 在无语音音频上的**经典幻觉**("Thank you for watching…"),
说明音频里没有可识别的唱词。工具:`scripts\asr-check.py`。

> ⚠️ **乐谱不能用来判断有无演唱**:两个器乐方案的乐谱里人声声部仍有旋律线(音符占比 63%/62%)。
> 我最初只看开头小节的休止符就下结论,被 `scripts\analyze-abc.py` 的全曲统计纠正了。
> 另外,"没有可识别的唱词" ≠ "完全没有发声",是否残留哼唱需人耳或人声分离定论。
> 上游 v1 有"输出含 vocal + instrumental 双音轨"的退路,但本移植版只输出单个立体声 WAV。

### 气声 / 喘息 / 叹息怎么写

目标:低声的「啊、哈、嘿」这类**带气声的节奏化吐字**。三个东西要分开处理 ——
**音色**(虚不虚)、**音节**(唱什么)、**节奏**(踩不踩拍)。

**先记住一个硬边界:YuE2 没有响度 / 力度参数。** 它输出归一化立体声 WAV,
所以命令不了它"唱小声"。"低声"只能往**音色**方向逼近,不能指望电平真的降下来。
另外这是个**学唱歌的模型**:真正的"喘息"是不可听的噪声(无基频),
它更可能给出"带气流的短元音",而不是纯呼气声。

写法(实测有效,`scripts\breath-vocals.py`):

1. **一个音节一行** —— 换行是节拍线索,一行一个「哈」比一行十个更容易落在拍上
2. **用语气字而不是词**:啊 哈 嘿 呼 唉 嗯 嘶,配 `!` 与 `——`
3. **音色标签点名要气声**:`breathy vocal` `whispery vocal` `husky vocal`
   `intimate close-mic vocal` `ASMR whisper vocal`(词表里已有中文对照:气声/耳语感/沙哑)
4. **曲风选本来就大量用气声的类型**:dark R&B / trap soul / ambient pop / city pop ——
   曲风标签顺带决定编曲密度,气声需要留白
5. **节奏感靠编曲标签**:`minimal punchy beat` `syncopated groove` `808 bass` `four-on-the-floor`

6 条实测(同 seed 831001 / 16 步 / 20 秒 ×6,**含一组对照**):

| 变体 | 做法 | 高频能量占比 | 过零率 |
|---|---|---|---|
| 1 气声短音(暗色 R&B) | 单音节 + 气声标签 | **0.0436** | **0.0601** |
| 2 叹息(氛围慢歌) | `唉——` `呼——` 长音 | 0.0058 | 0.0266 |
| 3 括号点缀(城市流行) | 正文 + `(哈)` `(啊——)` | 0.0411 | 0.0564 |
| 4 节奏 hook(trap soul) | 短促 `哈!` 密排 | 0.0036 | 0.0275 |
| 5 组合音节(舞曲) | `啊哈` `嘿哈` | 0.0118 | 0.0420 |
| **6 对照:同歌词去气声标签** | 只换人声标签 | 0.0212 | 0.0396 |

**第 1 与第 6 条是唯一可直接对照的一对**(歌词与 seed 逐字相同,只有人声标签不同):
气声版的**高频能量占比约 2 倍、过零率更高** —— 与"声门闭合不完全 → 宽带噪声多"的物理预期一致,
说明标签确实在起作用。但编曲差异也会拉高这两个数,所以**这只是参考,听感判断只能由人耳做**。

试听:**`output\breath-compare.html`**(6 段拼接 `breath-compare.wav`,点起始时间可跳转),
单条在 `output\breath\`,也全部进了界面「历史记录」页签。

> 我(助手)**没有听觉能力**,不能说"这条听起来够虚"。上面给的是可测量的代理指标 + 你自己听。

**如果重复的音节被"唱散了"**(hook 听不出规律的 哈-嘿-哈-嘿),先调 `semantic_repetition_penalty`:
默认 **1.2** 会主动压制字面重复,而这类 hook 靠的就是字面重复,可以降到 **1.0–1.05**。
本次 6 条全部用默认值,没动这个参数。

**如果纯 YuE2 都不到位,还有一条更可控的路**:YuE2 出伴奏 → 语音模型单独生成气声/叹息 → 混音。
audio.cpp 内置了带**显式非语言 token** 的 TTS,例如 CosyVoice3 的
`[breath]` `[quick_breath]` `[sigh]` `[hissing]` `[laughter]` `[lipsmack]` `[vocalized-noise]`
以及 `<strong>…</strong>` 重音控制 —— 这些是模型词表里的真 token,比"希望它唱出来"可控得多。
代价:需要额外下载权重(CosyVoice3 Q8_0,数十 GB 盘位里放得下但需取舍),且必须提供参考音频
(`--voice-ref`),可以直接拿 YuE2 生成的人声当参考以保持音色接近。这条路线需要时再做。

### 乐谱看不懂?ABC 自动换算成简谱

模型导出的是 **ABC 记谱法**(`V: Vocal` / `A2A2B2d2` / `"G"z8`),给机器读的,人看着费劲。
现在界面里直接显示**简谱**:调号 `1=D`、八度点、减时线、附点、休止 `0`、小节线、和弦标记,
**各声部按小节对齐并排**(人声 / 伴奏同一列就是同一小节),段落(`% verse` → 【主歌】)自动分块。

换算规则(`app/abc_jianpu.py`):

| ABC | 简谱 | 说明 |
|---|---|---|
| `K:D` | `1=D`,且 F/C 全升 | **`K:` 自带调号** —— 漏掉这步会满屏 `b3`/`b7`(踩过) |
| `F` / `f` | `3` / `3'` | 大写=中音区,小写低八度记号反向 |
| `A2` `A4` `A8` | `_5` `5` `5 -` | `L:1/16` 下 2=八分(1 条减时线)、4=四分、8=二分 |
| `A3` | `_5.` | 附点八分 |
| `z8` / `Z4` | `0 -` / 四小节 `0 -` | `Z` 是整小节休止,数字=小节数 |
| `[CEG]` | `[1 3 5]` | 和弦(堆叠显示) |

**同时导出文本**:有乐谱的任务会在任务目录里多一个 `score.jianpu.txt`
(与 `audio.wav` / `score.abc` 并列),纯文本记号约定写在文件头。
手工转换任意 `.abc`:`scripts\abc-to-jianpu.py 文件.abc` 或 `--all` 批量。

> 简谱是**只读视图**,改谱仍要改 ABC 原文再作为输入重渲染 —— 简谱不会被反解回 ABC。
> 换算失败只会让简谱不显示,不影响出歌(服务里那一步是兜底的)。

### 朦胧 / 迷离 / 轻抚 / 若隐若现:一半在模型,一半在后期

**先说结论:这一类"氛围"要求,靠换风格标签到不了。** 原因不是标签写得不够,是两件硬事实:

1. YuE2 **没有响度 / 力度参数**,"忽明忽暗""忽远忽近"这类**自动化**它做不了;
2. 它输出的是**已经混好的单轨**,分不出人声,没法只对人声做处理。

所以拆成两层:

| 层 | 谁做 | 做什么 |
|---|---|---|
| **R1 模型层** | YuE2 | 让声音本身有进有出、有虚有实(见上一节的气声标签 + 下面的结构手法) |
| **R2 后期层** | ffmpeg | 空间与动态:朦胧=高频滚降,迷离=加宽/相位,若即若离=干湿进退,轻抚=去浑浊+中频靠近 |

> 值得说明的是:dream pop 本来就是**把整体埋进空间**,而不是把人声单独拿出来做。
> 所以"整轨处理"在这个风格里不是退而求其次 —— 它接近这个风格本来的做法。

`scripts\dream-atmos.py` 一条命令跑完:生成两条源 → 套 6 条链 → 出对比带与试听页 → 客观测量。

**结构上的若隐若现(不需要后期)** —— 这一条**只做到一半**,如实记录三条实测:

| 源 | 手法 | 人声实际出现在 | 结论 |
|---|---|---|---|
| A | 段落**留空** | 0:00–0:41 全程(开头 7 秒是"啊×N"),0:41 之后才器乐 | ❌ **没退场**;留空段被唱成**无词长音**,不是器乐 |
| B | 织体标签(写满歌词) | 0:00–0:26 器乐 → 0:26–1:12 一直唱 | 26 秒器乐前奏是**模型自己排的**,不是我们控的 |
| C | `[Instrumental]` 段落标记 | 0:00–0:30 **器乐**(转出 whisper 的经典幻觉句)→ 0:30–1:03 唱 | ✅ 标记真的生效 |

**两个可复用的结论**:

- **`[Instrumental]` 是模型词表里的真 token,段落留空不是。** 想让人声退场要用前者。
  (之前只验证过"整首留空 → 器乐";"部分留空 → 器乐"这个推论是**错的**。)
- **人声坐在哪由模型自己决定。** 三条都只做到**一次进出**,反复进出没做到 ——
  歌词结构只能弱影响编排。要真正可控地"人声单独进退",**只能走分轨**(见文末)。

ASR 时间戳这条证据本身是可信的:转写里能读出歌词原文("夜色很轻落在我肩上" → "夜色很寂寞在我肩上"),
说明这些位置确实有人在唱;而器乐段落的标志是 whisper 的经典幻觉短语
("Thank you very much for watching…")。工具:`scripts\dream-atmos.py --asr`。

**实测(72 秒源,同 seed,全部对齐到 -16 LUFS 再对比)**:

| 变体 | 高频占比 | 3 kHz 占比 | 立体声宽度 | **锁相相关** | 周期起伏 |
|---|---|---|---|---|---|
| 原样 | 0.46% | 0.52% | 0.317 | 0.062 | 0.030 |
| 朦胧 `hazy` | **0.08%** ↓ | 0.65% | 0.354 | 0.023 | 0.009 |
| 迷离 `dreamy` | 0.43% | 0.53% | **0.525** ↑ | 0.037 | 0.037 |
| **若即若离 `looming`** | 0.47% | 0.56% | 0.417 | **0.479** ↑↑ | **0.212** ↑↑ |
| 轻抚 `caress` | 0.84% | **1.54%** ↑↑ | 0.400 | 0.159 | 0.069 |
| 全链 `full` | 0.20% ↓ | 0.98% ↑ | **0.533** ↑ | 0.254 ↑ | 0.129 ↑ |

**锁相相关**是这轮最关键的指标:它衡量"立体声宽度是否按我设定的 14 秒周期在起伏"。
直接用标准差没用 —— 源本身因编曲变化,宽度就在 0.70~0.17 之间大幅波动,会把调制淹没;
换成和调制正弦做最小二乘拟合,原样 0.062、若即若离 0.479,才说明**起伏是我们加的**。

试听:**`output\dream-compare.html`**(两条源 × 6 个变体,点起始时间跳转)。

**过程中踩的两个坑**(都写进脚本注释了):

- `loudnorm` 是**动态归一化**,会把音量包络压平(实测起伏 7.49 → 4.99 dB),
  而且它内部升采样到 **192 kHz**,不锁 `-ar` 就会得到 4 倍长度的垃圾。
  现在改成两遍:**先跑链路量 LUFS,再用固定增益对齐** —— 动态完全保留。
- 湿信号若**左右用同样的延迟**,它和干声一样宽,调制只能改"回声量"而非"距离"。
  改成左右不同延迟后,湿 = 宽而散(远)、干 = 实而窄(近),调制才真的在改距离。

**还差什么**:现在的呼吸是**整轨**的,而且"人声进出一次数"不受控(见上)。
要做到"只有人声忽远忽近、想几次就几次",需要**人声分离** ——
audio.cpp 自带 `htdemucs` 与 `bs_roformer`(`--task sep`,后者直接出 `vocals.wav` + `instrumental.wav`),
但本次编译只启用了 `yue2`(`AUDIOCPP_MODELS=yue2`,二进制里搜不到这两个家族),
要用得**重新 configure + 编译一次** + 下一个约 100 MB 的权重。
拿到分轨后,人声轨可以套**完全独立**的自动化:音量包络、滤波扫频、延迟抛掷、声像游走、侧链闪避。
另外倒放混响尾巴(`areverse`)这轮没加。

### 分析一首歌:把音频"读"成可用的风格标签

YuE2 **不接受参考音频**(源码显式拒绝 `audio_input`,能力声明 `supports_speaker_reference = false`,
实测传 `--voice-ref` 输出字节级一致 —— 详见 `docs/` 里的记录)。所以"参考一首歌"只能拆成
**先测出客观特征,再把特征翻译成风格标签**。`scripts\analyze-track.py` 做这件事,纯 CPU、不用重编译:

```powershell
D:\mt-tool\runtime\Scripts\python.exe scripts\analyze-track.py output\zh-01.wav --json results\track-profile.json
```

测:速度(BPM + 倍频歧义)、律动(3/4 vs 4/4 + 小节内强弱型)、调性(chroma 对大小调模板相关)、
频谱(谱质心 / 85% 滚降 / 平坦度 / 各频段能量占比)、动态(逐秒起伏 + LUFS)、空间(立体声宽度),
最后按规则**翻译成标签并标注是否在精选词表内**。

**两个踩过的坑,都是"看起来对但定义错了"**:

- **谱质心不能用功率加权。** 我原先写 `Σ(f·P)/ΣP`,得到 558 Hz;librosa 标准实现用**幅度**是
  **2131 Hz**,差 4 倍 —— 因为那首歌 71% 的能量在 400 Hz 以下,功率加权被低频彻底带偏。
  现在全部改用 librosa 的 `spectral_centroid` / `spectral_rolloff` / `spectral_flatness`。
- **不能拿 onset 自相关的"最强周期"当 BPM。** 4/4 流行里底鼓在 1、3 拍、军鼓在 2、4 拍,
  会让**两拍(半小节)**的周期性常常强于单拍,自动选"最强"就**系统性变成半速**。
  现在 BPM 以 librosa 节拍网格为准,并用拍点间隔反算校验(三首全部自洽),自相关只用来提示
  "存在半速歧义",两个档位的标签都给出来由人听决定。

> ⚠️ 测的是**整轨**不是人声:频谱特征由编曲主导,推不出唱法。调性→情绪那条是弱推断。
> 这条链给的是"像什么",给不了"一模一样"。

**想把 BPM / 拍号真的喂进生成**:YuE2 没有速度参数,标签只能粗略影响。理论上更精确的通道是
写进 ABC 的 `Q:`(速度)与 `M:`(拍号)字段配 `cot=melody` —— **但这条没验证过**,别当既有能力。

### 参考曲的节奏怎么"读"出来:MuScriptor 转谱

YuE2 不接受参考音频,**节奏只能先测出来、再翻译成标签或简谱**。两级工具:

| 工具 | 给什么 | 代价 |
|---|---|---|
| `scripts\analyze-track.py` | BPM / 拍点 / 调性 / 频谱 / 动态 / 空间 | librosa,CPU,秒级,**零安装** |
| `scripts\muscriptor-summary.py` + 引擎 | **音符级事件 + 乐器标注 + 真实鼓型** | 见下 |

MuScriptor 是 **Kyutai + Mirelo** 的多乐器音乐转谱模型(decoder-only Transformer,
代码 MIT / **权重 CC-BY-NC-4.0 非商用**)。已编进引擎:`AUDIOCPP_MODELS=yue2,muscriptor`,
权重用 `audio-cpp/audio.cpp-gguf` 那份(**`gated=False`,不需要 HF 账号**;
官方 HF 仓库是 gated 的,需账号+接受许可,我们没走那条)。

```powershell
audiocpp_cli.exe --task midi --family muscriptor `
  --model models\MuScriptor-Small-GGUF\muscriptor-small-f32.gguf `
  --backend cuda --audio output\zh-01.wav `
  --request-option output_format=json --out output\zh-01.notes.json --log --metrics
D:\mt-tool\runtime\Scripts\python.exe scripts\muscriptor-summary.py output\zh-01.notes.json
```

**实测效率**(V100,详细数据见 `docs\muscriptor.md`):

| 曲目 | 时长 | 音符事件 | 耗时 | RTF | 实时倍率 | 峰值显存 |
|---|---|---|---|---|---|---|
| 城市流行(密) | 52.6 s | 1282 | 9.9 s | 0.192 | **5.2×** | 2653 MiB(基线 1747,实增 ~906) |
| dream pop(疏) | 72.0 s | 324 | 3.8 s | 0.053 | **18.9×** | — |

**耗时由音符密度决定,不由时长决定。** 对比 YuE2 生成是 1.4–2× 实时 —— **转谱比生成快得多**。

**节奏结果,两个独立方法互相印证**:`zh-01` MuScriptor 116.8 BPM vs librosa 117.5(差 0.6%);
`dream-A` MuScriptor 79.9 vs librosa 半速读数 80.7(差 1.0%)。

从音频里读出的鼓型(不是猜的):

```
          1 e & a 2 e & a 3 e & a 4 e & a
底鼓      █·······█·······     1、3 拍
军鼓      ····█·······█···     2、4 拍(标准反拍)
闭镲      █·█·█·█·█·█·█·█·     八分音符贯穿
```

> ⚠️ 两个坑:**(1)** 众数间隔是拍/八分/十六分不能猜 —— 脚本先用"70–165 BPM"启发式选倍频,
> 再 ±15% 网格搜索取"图案最锐利"的速度;`--bpm` 可覆盖。**(2)** 折叠对速度极敏感:
> 115.4 与 117.5 只差 2%,23 小节累积后反拍军鼓就被抹平了 —— **不细化速度等于白折叠**。
>
> ⚠️ MuScriptor 是面向**器乐**的转谱模型,不是人声提取器。它在两条曲子里都标出
> `voice` 音符(各约 20 个),算音符级的人声在场证据,但远不是完整人声转写。

### 界面上哪些地方必须用英文

不是没翻译,而是**模型的词表**,改了就不认:`[Verse]` `[Chorus]` `[Bridge]` `[Outro]`
`[Intro]` `[Instrumental]`(段落标记)、风格标签(如 `Mandarin, city pop`)、
以及 `off`/`melody`/`full`(接口值)。界面在这三处都是**显示中文 + 保留英文标记**。

### 点击级测试

不需要浏览器,直接调 Gradio 的处理函数(与按按钮同一条路径,连 18 个输入的接线一起覆盖):

```powershell
D:\mt-tool\runtime\Scripts\python.exe scripts\gui-smoke-test.py
# PASS: 音频 20.0s ≤ 上限 20s —— 时长控制生效
```

## 已知限制

- **CUDA graphs 在 Volta 上被 ggml 自动禁用**(日志:`disabling CUDA graphs due to GPU architecture`),这是主要速度损失来源之一。
- **闪存注意力走 TILE 内核而非 Volta 张量核 MMA**(本地补丁 1 所致),见 `docs/v100-patches.md`。
- **每首歌重载模型**(约 5–6 秒):引擎按任务 spawn,未做常驻会话。
- **阶段进度是阶段级而非 token 级**:引擎的 HTTP API 是 offline 同步模式,只有 CLI 的 timing 日志能反映阶段。
- **GUI 没做过浏览器点击级自动化测试**:已验证页面加载(102 KB / 中文标签齐全)与服务链路,但没有脚本化点过按钮。
- 长曲只测到 3 分 15 秒(194.7 秒),峰值显存 7985 MiB;更长曲目未测。
- `--threads 8` 未调优(CPU 为 Xeon E5-2666 v3,10C/20T)。
- 中文歌的**演唱发音质量**未做客观评测(文本送达已用哈希对照验证)。

## 启用 WebUI(图形界面)

WebUI 是**内嵌在 `audiocpp_server` 里的单文件应用**(694 KB,已确认含 yue2 面板:HTML 里 `yue2` 出现 98 次、`music` 140 次)。需要额外编译这一个目标:

```powershell
cd D:\mt-tool\yue2-V100
cmake --build build --config Release --target audiocpp_server --parallel 16
```

配置 `server.json` 已就绪(端口 8080、`lazy_load`、只注册 yue2)。启动:

```powershell
$env:PATH = "C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.8\bin;$env:PATH"
build\bin\Release\audiocpp_server.exe --config server.json --ui
```

浏览器打开 **http://127.0.0.1:8080**

### 已实测的端点

```
GET /health    → {"status":"ok","backend":"cuda","models":1,"ui":true,"ui_management":false}
GET /v1/models → yue2 (gen / offline / loaded:false ← 懒加载)
GET /          → HTTP 200, 693,982 bytes 单页应用
```

### 通过 API 生成(WebUI 的按钮走的就是这条)

```powershell
$lyrics = "[Verse]`n第一段歌词`n[Chorus]`n副歌"
$body = @{
  model   = 'yue2'
  request = @{
    text    = $lyrics                     # 通用输入,模型侧作为 lyrics 的回退
    options = @{
      style  = 'English, indie pop, bright acoustic guitar, warm lead vocal'
      lyrics = $lyrics
      cot    = 'off'                      # off | melody | full
      seed   = '831001'
    }
  }
} | ConvertTo-Json -Depth 8

Invoke-RestMethod http://127.0.0.1:8080/v1/tasks/run -Method Post `
  -Body $body -ContentType 'application/json'
```

返回 `{"audio": "<base64 编码的 WAV>", ...}`。实测:32.4 秒返回 7.81 MB,
解码后为 42.64 秒 / 48 kHz / 立体声,`mean −16.8 dB`(真实音频)。

> ⚠️ **`style` 必须放进 `request.options`,不能放顶层。**
> 服务端把 JSON 交给 `app/cli/request.cpp:184` 的 `build_request_from_json`,它**只把 `options`
> 对象展开成模型选项映射**。放顶层会返回 `500 Yue2 requires non-empty style`。
> CLI 的 `--request-option style=...` 与之是同一套,只是入口不同。

### 其他端点

```powershell
# 释放显存(模型常驻,直到显式卸载或服务退出)
Invoke-RestMethod http://127.0.0.1:8080/v1/tasks/unload_all_models -Method Post
Invoke-RestMethod http://127.0.0.1:8080/v1/tasks/unload_models -Method Post -Body '{"model_ids":["yue2"]}' -ContentType 'application/json'
```

### 两点说明

- 本构建**没有**编 native model manager(`AUDIOCPP_BUILD_NATIVE_MODEL_MANAGER=OFF`),
  所以 UI 是**只读模式**:可以用配置里的模型生成,但不能在 UI 里下载/删除模型
  (`/health` 的 `ui_management:false` 即此意)。
- 服务端首次请求会懒加载模型(数秒),之后常驻显存(`max_loaded_models:1`);
  需要完整 UI(浏览/下载/切换模型)必须重新配置 `-DAUDIOCPP_BUILD_NATIVE_MODEL_MANAGER=ON`。

## 复现这次的完整流程

```powershell
cd D:\mt-tool\yue2-V100
# 1) 环境
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\check-env.ps1
# 2) 配置(需已克隆 audio.cpp dev 分支)
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\configure.ps1
# 3) 编译 —— 在普通终端里并行跑,快 8-15 倍
cmake --build build --config Release --target audiocpp_cli --parallel 16
# 4) 权重(4.22 GiB)
D:\Python\Python312\python.exe scripts\fetch_weights.py --local-dir models\Yue2-3B-GGUF
# 5) 生成
build\bin\Release\audiocpp_cli.exe --task gen --family yue2 `
  --model models\Yue2-3B-GGUF --backend cuda --threads 8 `
  --request-option "style=English, indie pop, bright acoustic guitar, soft drums, warm lead vocal" `
  --request-option "lyrics=[Verse]`nYour lyrics here.`n[Chorus]`nAnd here." `
  --request-option cot=off --request-option seed=831001 --out output\song.wav --log
```

> 运行 `audiocpp_cli` 需要 CUDA 的 DLL 在 PATH 上:
> `$env:PATH = "C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.8\bin;$env:PATH"`

## 接管编译:在普通终端里跑

沙箱化的 shell 里 MSBuild `/m` 和 Ninja 都不可用(命名管道),只能单核串行。在普通终端里并行可快 8–15 倍:

```powershell
cd D:\mt-tool\yue2-V100
cmake --build build --config Release --target audiocpp_cli --parallel 16 2>&1 | Tee-Object -FilePath logs\build-fast.log
```

- **保持同一个 VS 生成器和同一个 `build\` 目录**,这样已完成的对象文件会被增量跳过。换生成器需要清空 `build\` 重来,前面的成果全丢。
- 中断时的进度已保留:`ggml-base.lib`、`ggml-cpu.lib`、`cjson_vendor.lib` 以及 49 个 `.obj`(构建目录 79 MB)。
- 预计产出:`build\bin\Release\audiocpp_cli.exe`

