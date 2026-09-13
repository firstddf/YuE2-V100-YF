# 自建中文 GUI

不为 audio.cpp 自带 WebUI 打补丁,而是自己做一层服务 + 前端。原因:

| 自带 WebUI 的问题 | 我们的做法 |
|---|---|
| 为 60+ 模型族共用,不是为 YuE2 而生 | 只服务 YuE2,字段与流程都按 YuE2 设计 |
| YuE2 参数无中文翻译(`lang_zh.json` 里 `yue2` 键 = 0) | 全中文界面 |
| 只暴露 22 个参数 | 参数 + 批量队列 + 历史 + 性能指标 + 乐谱导出 |
| 同步阻塞、无阶段进度 | 服务解析 CLI 的 timing 日志 → **阶段级进度条** |
| 乐谱只能手打路径 | 乐谱可粘贴、可上传、可导出 |
| 后端是通用中转层 | 服务按需调用 CLI,不常驻、不占端口外的资源 |

## 组成

```
app/yue2_service.py    HTTP 服务(默认 127.0.0.1:1414):任务队列 / 进度 / 历史 / 乐谱
app/abc_jianpu.py      ABC 记谱法 → 简谱(HTML 渲染 / 纯文本导出)
app/gui.py             Gradio 中文前端(默认 127.0.0.1:7860)
scripts/start-gui.ps1  一键启停
scripts/abc-to-jianpu.py   ABC → 简谱 .txt 导出 / 批量检查
scripts/analyze-abc.py 统计乐谱各声部的音符/休止符比例(辅助分析,不能判断有无演唱)
scripts/asr-check.py   用 whisper-small 转写对比(判断有无唱词的客观手段)
scripts/gui-smoke-test.py  点击级测试(不需浏览器)
scripts/test-style-tags.py 风格标签逻辑单测
```

引擎调用方式:**每个任务 spawn 一个 `audiocpp_cli.exe` 进程**。
代价是每首多 5–6 秒模型加载;换来的是无状态、无端口依赖,以及能拿到阶段进度
(常驻引擎的 HTTP API 是 offline 同步模式,不提供中间进度)。

## 启动 / 停止

```powershell
# 启动(服务 + 界面)
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-gui.ps1

# 停止
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-gui.ps1 -Stop
```

- 界面:**http://127.0.0.1:7860**
- 服务:**http://127.0.0.1:1414**(自带交互文档 `/docs`)

> ⚠️ 脚本里的端口清理用 `netstat`,**不用 `Get-NetTCPConnection`**。
> 后者在本环境返回空,会让"重启"实际连到旧进程 —— 这个坑踩过一次
> (表现为:任务运行期间接口 500、结束后又正常)。

## HTTP 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 引擎/模型路径、是否就绪、任务数 |
| GET | `/api/presets` | 风格预设 + 阶段名列表 |
| POST | `/api/jobs` | 提交任务,返回 job id |
| GET | `/api/jobs` | 任务列表(历史) |
| GET | `/api/jobs/{id}` | 状态 / 阶段 / 进度 / 指标 / 乐谱 |
| GET | `/api/jobs/{id}/audio` | 结果 WAV |
| GET | `/api/jobs/{id}/abc` | 生成的乐谱文本 |
| GET | `/api/jobs/{id}/log` | 引擎日志尾部(`?tail=N`) |
| POST | `/api/jobs/{id}/cancel` | 取消(杀掉引擎进程) |
| DELETE | `/api/jobs/{id}` | 删除任务与产物 |
| GET | `/api/logs` | **日志文件列表**(`logs\` 下,名字/大小/时间) |
| GET | `/api/logs/{name}` | **读某个日志文件**(`?tail=N`,0 = 全部;只接受裸文件名,拒绝路径穿越) |
| POST | `/api/analyze` | **参考曲分析**(`audio` + `level=basic\|full` + 可选 `bpm`) |

`/api/analyze` 的两级:

- `level=basic` —— 只跑 librosa(纯 CPU,约 9 秒):速度/律动/调性/频谱/动态/空间 + 标签建议
- `level=full` —— 再加 **MuScriptor 转谱**(占 GPU,约 21 秒):乐器标注 + 音符级 + 真实鼓型

返回 `{audio, level, basic, notes?, notes_file?}`。`notes` 结构来自
`scripts\muscriptor-summary.py --json`(`instruments` / `tempo` / `drum_pattern` / `timeline`)。

**GPU 互斥**:`level=full` 时如果队列里有生成任务在跑,直接返回 **409** ——
MuScriptor 和 YuE2 用同一块卡,与其让两边抢显存,不如明确说清楚:

```
有 1 个生成任务在跑(如 a1b2c3)。MuScriptor 会和生成抢显存,请等它跑完再分析
```

错误码:`400` = 音频不存在 / level 非法 / bpm 非数字;`409` = GPU 被生成任务占着。
产物落在 `output\analysis\<音频名>\`(`basic.json` / `notes.json` / `summary.json`)。

提交示例:

```powershell
$body = @{
  style = 'Mandarin, city pop, warm female vocal, nostalgic'
  lyrics = "[Verse]`n夜色落在窗台上`n[Chorus]`n如果风还记得那年夏天"
  cot = 'full'          # off | melody | full
  seed = 831001
  num_inference_steps = 8
} | ConvertTo-Json

$job = Invoke-RestMethod http://127.0.0.1:1414/api/jobs -Method Post `
  -Body $body -ContentType 'application/json; charset=utf-8'
```

可选字段:`abc`(内联乐谱)、`abc_file`(乐谱路径)、`cfg_scale`、
`temperature`、`top_p`、`top_k`、`repetition_penalty`、
`abc_*` 与 `semantic_*` 采样参数。

## 阶段进度怎么来的

服务边跑边读引擎日志,按出现顺序映射:

| 日志键 | 界面阶段 |
|---|---|
| (还没有任何键) | 启动引擎 · 加载模型 |
| `yue2.plan_ms` | 解析请求 |
| `yue2.semantic.abc_generate_ms` | 乐谱规划 (ABC) |
| `yue2.semantic.music_generate_ms` | 语义生成 |
| `yue2.nar_ms` | 声学合成 (NAR) |
| `yue2.vae_decode_ms` | 音频解码 (VAE) |
| `session.wall_ms` | 完成 |

进度百分比 = 已到达的阶段序号 / 总阶段数。**这是阶段级而非 token 级的诚实进度**。

## 界面上手

1. **风格**:按五要素**分类勾选**(见下节),勾中的标签自动拼进风格框;表里没有的词写进「自定义补充」。
2. **歌词**:`[Verse]` / `[Chorus]` / `[Bridge]` / `[Outro]` 分段,按钮可快速插入。
3. **规划路线**:
   - `off` 直接生成(最快,无乐谱)
   - `melody` 只规划旋律 —— **翻唱推荐**,配 `.abc` 输入可自由改编伴奏
   - `full` 旋律 + 和弦,可导出完整乐谱
4. **ABC 乐谱**:粘贴或上传 `.abc`(走 `cot=melody/full`);生成后乐谱会显示在右侧,可复制修改后作为输入重渲染。
5. **批量**:多首歌词用一行 `---` 分隔,顺序生成,结果进表格。

## 风格:分类下拉多选(选项带中文解释)

**风格是自由文本,不是固定枚举** —— 上游原文:*our tags have an open vocabulary*。
界面按上游推荐的五要素做成**分类下拉多选**,选项显示成「english · 中文」:

| 类别 | 标签数 | 作用 |
|---|---|---|
| **语言 language** | 11 | **最关键** —— `Mandarin` / `Cantonese` / `Japanese` / `English`… 决定唱什么语言 |
| 曲风 genre | 71 | pop / rock / city pop / jazz / heavy metal / 民谣 / 摇滚 / 古风… |
| 乐器 instrument | 48 | piano / Rhodes / electric guitar / synth bass / 二胡 / 古筝… |
| 情绪 mood | 52 | nostalgic / uplifting / melancholic / 怀旧 / 抒情 / 黑暗… |
| 人声 gender | 9 | male / female / child / 童声 / soprano… |
| 音色 timbre | 34 | warm vocal / clear vocal / raspy vocal / 温暖 vocal… |

合计 **225 个标签**,在两个文件里:

| 文件 | 内容 |
|---|---|
| `examples/style_tags.json` | 标签词表本身(6 类) |
| `examples/style_tags_zh.json` | 中文对照(纯展示层,缺项自动只显示英文) |

**关键:中文只是显示层。** 下拉显示 `city pop · 城市流行`,但选中的值、以及拼进风格框、
最终下发给模型的,始终是**纯英文标签** `city pop` —— 因为模型的 `[Tags]` 段是按英文标签训练的。

机制:

- **选择变化 → 立即重算风格框**(顺序固定 语言→曲风→乐器→情绪→人声→音色)
- **风格框仍可直接手改**,想写什么写什么
- **「自定义补充」**放表里没有的词,同样会被拼进去
- **常用组合下拉**会反向解析回各类选择(`split_style()`),不是简单覆盖文本框

> 屏幕占用:早先用 225 个复选框铺开,占满整屏;现在 6 个折叠下拉框。
> **页面字节数几乎不变**(203 KB),因为选项文本仍要传给前端,省的是屏幕高度。

> 词表来源:上游 `top_200_tags.json`(YuE-v1 分支)的精简子集,去掉大小写重复、前导空格
> 与明显脏数据(如 `None`、`为了让声乐更加生动,使用了混响效果。 vocal`)。
> 另补了几条官方 demo 用得上但推荐表里没有的:`city pop` / `nu-disco` / `anison` /
> `instrumental` / `古风` / `Rhodes` / `live drums` / `groovy bass` / `cinematic` / `spacious`
> —— 词表开放,列出即合法,文件里以 `_curated_additions` 标注。

**一致性由测试保证**(`scripts\test-style-tags.py`):

1. 每个**英文标签**都必须有中文解释(实测 198/198,中文标签自身不需要)
2. `DEFAULT_STYLE` 必须完全落在词表内,否则界面初始状态与风格框会不一致
3. 9 个预设的每个词都必须能落到某个类别里,否则会静默掉进「自定义补充」
4. 拼回去再解析应保持一致(往返一致)

```powershell
D:\mt-tool\runtime\Scripts\python.exe scripts\test-style-tags.py
# PASS: 所有英文标签都有中文解释 / PASS: 默认风格全部命中词表 / 往返一致性: PASS
```

## 时长怎么控制(没有直接参数)

**YuE2 没有时长参数。** 它一直生成到自然结束(EOS),所以时长是涌现出来的:

| 手段 | 作用 |
|---|---|
| **歌词长度** | **主要因素** —— 段落越多、词越多,歌越长 |
| 最长时长(秒) | 硬上限 → `semantic_max_tokens`,到时会截断 |
| 最短时长(秒) | 下限 → `semantic_min_tokens`,防止过早收尾 |

实测换算关系是**精确的**(1 token = 1 潜帧 = 40 ms):

```
时长(秒) = 语义 tokens ÷ 25        即 1 秒 = 25 tokens
```

7 组实测(1015 / 1030 / 1088 / 1276 / 1304 / 1371 / 4867 tokens)全部精确命中 25.000;
反方向验证:界面设"最长时长 20 秒" → 下发 `semantic_max_tokens=500` → 输出**正好 20.0 秒**。

默认 `semantic_max_tokens=9000` → **6 分钟**上限;`semantic_min_tokens=200` → 8 秒下限。

> ⚠️ 通用参数 `--duration-seconds` 对 YuE2 **无效**。实测请求 10 秒仍输出 34.9 秒、exit=0 无报错:
> 该值会进 `options["duration_seconds"]`,而 YuE2 的 `apply_options` 不读它,
> 且未知选项不做校验、直接忽略。**不要用它控时长。**

## 纯器乐(没有歌词)怎么做?

**歌词是必填的。** 留空会被拒:`--request-option lyrics=` 连参数解析都过不了
(`expected --request-option key=value`),即便传空白也会撞上 `Yue2 requires non-empty lyrics`。

**官方做法**(上游 issue #18 维护者回复,提问者已确认有效):**保留段落标签、把歌词行留空**,
并从风格里去掉所有人声标签。段落标签正好满足"歌词不能为空"的校验:

```
[verse]


 

[chorus]




[chorus]




[outro]

```

界面上的 **「🎹 纯器乐模式(空歌词 + 无人声风格)」** 按钮一次完成这两件事
(填歌词 + 把风格下拉全部设为无人声标签)。

### 验证:用 ASR 对比,而不是靠耳朵

`scripts\asr-check.py` 用 whisper-small 转写,**对照组的词数才是判据** ——
单看"转不出词"没有意义,必须和已知歌词的曲子比:

| 输入 | 转写词数 | 内容 |
|---|---|---|
| 有唱的曲子(对照) | **251** | "Soft morning light is touching the window…" —— 与已知歌词逐字吻合 |
| 官方空歌词方案 | **15** | "Thank you for watching and please subscribe…" |
| 歌词写 `[Instrumental]` | **6** | "Thank you very much for watching!" |
| 走服务再跑一次纯器乐 | **14** | "Thank you so much for watching, and I'll see you…" |

后三条都是 whisper 在**无语音音频**上的经典幻觉(它会吐出训练数据里的常见短语),
说明音频里**没有可识别的唱词**;而对照组能逐字转出歌词,说明这个方法本身可靠。

> ⚠️ 三点如实说明:
> 1. **乐谱不能用来判断有无演唱。** 两个器乐方案的 ABC 乐谱里,人声声部**仍有旋律线**
>    (音符占比 63% / 62%,甚至高于对照组的 58%)。模型规划了人声轨,却因歌词为空/无意义
>    而渲染成器乐。我最初只看开头几小节的休止符就下了结论,是错的 ——
>    `scripts\analyze-abc.py` 的全曲统计纠正了我。
> 2. **"没有可识别的唱词" ≠ "完全没有发声"。** 是否还残留哼唱/元音,需要人耳或人声分离才能定论。
> 3. 上游 v1 的退路是"输出里同时有 vocal 与 instrumental 两条音轨",但
>    **audio.cpp 的 YuE2 移植版只输出单个立体声 WAV**(任务目录里只有 `audio.wav` + `score.abc`),
>    没有分轨,那条不适用。

## 失败时看什么(日志与诊断)

**踩过的坑**:失败时日志被写进了一个 `visible=False` 的框,用户只看到一句引擎报错
(实测:`❌ 失败:audiocpp_cli failed: Yue2 abc sampling options are invalid`),
完全不知道是哪个参数的问题。现在**失败必然摊开三样东西**:

| 面板 | 内容 |
|---|---|
| **失败原因** | 引擎原话 |
| **实际下发的参数** | `cot` / `seed` / `num_inference_steps` / `cfg_scale` / `*_tokens` / `*_temperature` / `*_repetition_penalty`,以及 `style` 与歌词前几行 |
| **引擎日志尾部** | 40 行,且**日志框自动变为可见** |

两条失败路径都覆盖:

- **提交期被拒**(参数越界)→ 任务没创建,所以没有引擎日志,但**参数表最有价值**
- **引擎期失败**(跑到一半才挂)→ 参数表 + 完整日志尾部

### 顺带修掉的根因:浏览器里"没填"的数字框会提交 0

`abc_max_tokens` / `abc_temperature` / `semantic_temperature` /
`semantic_repetition_penalty` 这四项是"留空即用引擎默认"的 `gr.Number`。
但**浏览器里没动过的 `gr.Number` 提交的是 0,不是 None**,而这些参数的默认值都不是 0:

| 参数 | 引擎默认 | `0` 为什么非法 |
|---|---|---|
| `abc_max_tokens` | 4096 | `max_tokens(0) < min_tokens(32)` |
| `semantic_repetition_penalty` | 1.2 | 要求 `> 0` |

结果是"什么都没填"反而生成失败。修法:这四项**把 0 当"没填"**
(标签也写明「留空/0 = 用引擎默认」)。真要用 0(如 `temperature=0` 表示 argmax)走命令行。

### 服务端预校验

`POST /api/jobs` 现在会先校验 `abc_*` / `semantic_*` 的范围再落任务,越界返回 **400** 且说明白:

```
abc_max_tokens(0)必须 ≥ abc_min_tokens(32)(后者未指定,用的是引擎默认值)
semantic_repetition_penalty=0 越界,要求 > 0
abc_temperature=9 越界,要求 ≤ 5
```

引擎原本只说 `Yue2 abc sampling options are invalid`(不说哪个参数、不说哪个值),
而且要**加载完模型**才报错 —— 现在既说清楚了,也不浪费 GPU 时间。

## 日志历史(第 4 个页签)

出问题**先来这里**。两块:

| 区域 | 内容 |
|---|---|
| **任务日志** | 下拉选任务(按生成时间倒序,含失败)→ 读引擎完整输出;行数可调(**0 = 全部**);「最新失败任务」一键定位 |
| **服务 / 构建日志** | 下拉选 `logs\` 下的文件(`service.out.log` / `service.err.log` / 构建日志 / 各实验日志)+ 读取 |

- 每条日志都显示 **总行数 / 实际显示行数 / 文件路径**,方便直接去磁盘找。
- `demo.load` 时会自动刷新两个下拉。

**两个修过的坑**:

1. **`service.out.log` 一直是 0 字节** —— Python stdout 重定向到文件时是**块缓冲**,
   服务跑着的时候日志根本不落盘,诊断价值为零。修法:`start-gui.ps1` 里给服务加 `-u`(unbuffered)。
   修完启动即可见(实测 335 B)。
2. **非法日志名返回 500** —— `safe_log_path()` 是模块级函数,而 `HTTPException` 是在
   `build_app()` **内部**才 import 的,那里抛的是 `NameError`。现在它抛
   `ValueError` / `FileNotFoundError`,由路由转成 400 / 404。

**路径穿越是防住的**(实测):

```
nope.log            -> 404 日志不存在
..                  -> 400 非法的日志名
.                   -> 400 非法的日志名
a/b.log             -> 404(路由不匹配)
%2e%2e%2fREADME.md  -> 404
```

## 历史记录的时间与排序

历史表有 **「时间」列**,显示的是**歌曲生成时间**(`YYYY-MM-DD HH:MM`),按**时间倒序**(新的在最上)。

- 新任务的 `created` = 提交时刻。
- **重启后恢复的任务**用 `audio.wav` 的 mtime,即引擎写出成品那一刻 —— 而不是**目录** mtime:
  目录 mtime 会被之后写进该目录的任何文件(如 `score.jianpu.txt`)刷新,时间会漂。
- 排序按 `created` 排,**不按插入顺序** —— 恢复是从磁盘扫描来的,插入顺序不保证等于时间顺序。

## 参考曲分析(第 5 个页签)

界面上六个页签:`🎼 单首生成` / `📦 批量出歌` / `🗂 历史记录` / `📜 日志历史` /
`🔍 参考曲分析` / `❓ 说明与限制`。

**为什么有这个页签**:MuScriptor 是后加的,当时只接了命令行;GUI 更早是按"YuE2 生成器"设计的。
于是出现"GUI 只能生成、分析只能敲脚本"的断层 —— 这个页签把它接上。

流程:**上传参考曲 → 选级别 → 分析 → 「🎼 标签填入「单首生成」」→ 生成一首类似的**。

| 元素 | 说明 |
|---|---|
| 参考音频 / 路径框 | 路径框优先;上传的文件也会被用 |
| 分析级别 | ① librosa(秒级)/ ② 加 MuScriptor 转谱(占 GPU) |
| 已知 BPM(可选) | 填了鼓型折叠更准(折叠对速度极敏感) |
| 建议标签框 | 可编辑后再填入 |
| **标签填入「单首生成」** | **并入**现有风格框(不覆盖),再反向解析回 6 类下拉 |

两个设计要点:

1. **标签是"并入"不是"覆盖"**,并且复用 `on_preset` 的反向解析(`split_style`)——
   所以你原有的 `Mandarin, city pop, warm female vocal` 会保留,新标签按类归位
   (例:`ambient` 进 genre 类,`groovy/danceable` 进 mood 类)。
2. **只给情绪/氛围类标签**。语言与曲风**必须由你补** —— 语言决定唱什么语言,
   而这是测不出来的(要 ASR,且对无词吟唱无效)。页面上会明确提示这一点。

> ⚠️ 页面上写明了:**分析只给客观特征**,「朦胧、迷离、轻抚」这类**氛围判断只能由用户听** ——
> 助手听不到音频(harness 输入模态只有 text 与 image)。

## 历史记录(重启不丢,可直接试听)

**两个曾经的坑,现已修好:**

| 问题 | 原因 | 修法 |
|---|---|---|
| 服务重启后历史清空 | 任务只存在内存里 | 服务启动时扫描 `output\gui\jobs\` **从磁盘恢复**(音频/乐谱/指标/请求都还原) |
| 列表里的行点不动 | 只能手动把任务 ID 打进文本框 | **点击表格任意一行即试听** + 新增「选择历史任务」下拉框(选中即试听) |

**三处入口都可用**:表格行点击、历史任务下拉、手动输入任务 ID。

任务目录里现在会额外写入 `request.json`(风格/歌词/参数),这样重启后连"当时用的什么提示词"
也能还原。早于这次改动生成的目录没有该文件,请求信息为空,但**音频与乐谱仍可试听/查看**。

```powershell
# 服务启动时会打印恢复数量
#   history        16 个任务从磁盘恢复,共 16 个
GET /api/health   →  { ..., "jobs": 16, "restored_from_disk": 16 }
```

> 文件落在 `output\gui\jobs\<任务id>\`:`audio.wav`、`score.abc`、`run.log`、`request.json`。
> 想清理旧记录直接删对应目录即可;服务下次启动就不会再恢复它。

## 乐谱:ABC 原文 + 自动换算的简谱

模型只写 **ABC 记谱法**,人读不了(`V: Vocal` / `A2A2B2d2` / `"G"z8`)。界面同时给出两样:

| 位置 | 内容 |
|---|---|
| 🎼 简谱(HTML 渲染) | 调号 `1=D`、八度点、减时线、附点、休止 `0`、小节线、和弦标记;声部按小节对齐;段落自动分块 |
| 乐谱原文(ABC) | 模型原始输出,可复制、可改后作为输入重渲染 |
| `score.jianpu.txt` | 任务目录里的纯文本简谱(记号约定写在文件头),机器人生成时自动写出 |

**换算是本地的,不是模型给的**(`app/abc_jianpu.py`)。关键点:

- **`K:` 自带调号**:`K:D` 表示 F、C 全升。第一版漏了这一步,简谱里满屏 `b3`/`b7` —— 是错的。
- **`L:` 是时值单位**:`L:1/16` 时 `A2`=八分、`A4`=四分、`A8`=二分、`A3`=附点八分。
- **`Z` 是整小节休止**(数字=小节数),要按拍号展开;`z8` 只是普通休止。
- 声部**按小节对齐**:截断在 `V:` 之间来回切,按小节序号并排就是同一时间点。

```powershell
# 手工转换 / 批量
D:\mt-tool\runtime\Scripts\python.exe scripts\abc-to-jianpu.py output\score-full.abc
D:\mt-tool\runtime\Scripts\python.exe scripts\abc-to-jianpu.py --check     # 只解析,查有没有崩
```

**边界**:简谱是**只读视图**,不会被反解回 ABC —— 要改谱还是改 ABC 原文。
换算失败只是简谱不显示,不影响出歌(服务里那一步有兜底,且**不能**让生成任务失败)。

> 已知不完美:模型偶尔写出**和弦内时值不一致**的 `[F2F3f2]`(按第一个音处理并在谱下标注);
> 中途转调会被忽略并警告;`>` `<` 破音节奏与装饰音不表达。

## 哪些地方必须用英文

不是界面没翻译 —— 这些是**模型的词表**,改成中文模型就不认:

| 内容 | 原因 |
|---|---|
| `[Verse]` `[Chorus]` `[Bridge]` `[Outro]` `[Intro]` `[Instrumental]` | 模型词表里真实存在的段落标记 |
| 风格提示(如 `Mandarin, city pop, warm female vocal`) | 上游 `top_200_tags.json` 的词表 |
| `off` / `melody` / `full` | 接口取值(界面已给中文说明) |

界面上这些按钮**显示中文 + 保留英文标记**,例如按钮写"副歌 [Chorus]",插入的仍是 `[Chorus]`。

## 已验证

| 项 | 结果 |
|---|---|
| 服务 health / presets / jobs / log / audio 端点 | ✅ |
| `cot=full` 全流程 + 乐谱导出 | ✅ 中文歌导出 835 字节带和弦乐谱 |
| **ABC → 简谱换算** | ✅ 899 字符 ABC → 17.9 KB 简谱 HTML(表格 + 八度点 + 减时线);`--check` 14 个谱全部解析通过 |
| **`score.jianpu.txt` 自动导出** | ✅ 新任务目录里与 `score.abc` 并列写出 |
| `cot=off` 不请求乐谱、不报错 | ✅ |
| 运行期间阶段进度、0 HTTP 错误 | ✅ |
| GUI 页面加载(203 KB,6 类勾选框与中文标签齐全) | ✅ |
| **风格标签逻辑单测**(9 个预设全部命中词表,往返一致) | ✅ `scripts\test-style-tags.py` |
| **点击级测试**(`gradio_client` 直接调处理函数) | ✅ `scripts\gui-smoke-test.py`(含简谱换算检查) |
| **历史持久化 + 可试听**(重启后 23 条全部恢复,`/load` 取到音频 + 简谱 + 乐谱) | ✅ |
| **时长上限生效**(20 秒上限 → 正好 20.0 秒) | ✅ |
| 音频下载(9.35 MB WAV) | ✅ |

点击级测试不需要浏览器:

```powershell
D:\mt-tool\runtime\Scripts\python.exe scripts\gui-smoke-test.py
# PASS: 音频 20.0s ≤ 上限 20s —— 时长控制生效
```

它走的是和按按钮完全相同的 Gradio 处理函数,因此同时覆盖了界面接线
(18 个输入的顺序与数量)与"最长时长 → semantic_max_tokens"的换算。

## 还没做的

- **热态引擎**:目前每首重载模型。升级方向是在服务内保留常驻会话。
- **真实浏览器端到端**:已用 `gradio_client` 做到处理函数级,但没有用 Playwright 之类点真实 DOM。
- 乐谱导出依赖 `scripts\apply-abc-patch.ps1` 打的 C++ 补丁;未打补丁时界面会显示空乐谱。
