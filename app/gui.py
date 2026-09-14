#!/usr/bin/env python3
"""YuE2 中文图形界面(Gradio)。

设计取向:audio.cpp 自带的 WebUI 是给 60+ 个模型族共用的通用界面,YuE2 在里面
只有 22 行配置、没有中文翻译、也没有本项目需要的批量队列 / 阶段进度 / 乐谱导出。
这个界面只为 YuE2 服务,并补齐那些缺口:

  * 风格五要素点选(genre / instrument / mood / gender / timbre)
  * 歌词分段助手([Verse] / [Chorus] / [Bridge] / [Outro])
  * 阶段级进度条(由服务解析 CLI 的 timing 日志得到)
  * ABC 乐谱:既可作为输入(改编 / 翻唱),也可导出模型自己写的谱
  * 批量队列 + 历史记录 + 性能指标(RTF、实时倍率、token 数)

依赖:gradio + 本项目的 yue2_service(默认 127.0.0.1:1414)。
启动:scripts\\start-gui.ps1
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Optional

import gradio as gr
import requests

ROOT = Path(__file__).resolve().parent.parent
PREVIEW = ROOT / "output" / "gui" / "preview"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from abc_jianpu import has_notation, to_html as abc_to_html  # noqa: E402


def render_jianpu(abc_text: str, limit_bars: int = 0) -> str:
    """ABC -> 简谱 HTML。模型不写谱(cot=off)时返回空串,界面自动收起。"""
    if not has_notation(abc_text or ""):
        return ""
    try:
        return abc_to_html(abc_text, limit_bars=limit_bars)
    except Exception as exc:  # noqa: BLE001 - 记谱渲染不值得把整个界面搞崩
        return f'<div style="color:#a00;font-size:13px">简谱渲染失败:{exc}</div>'


def load_style_tags() -> dict[str, list[str]]:
    """Upstream-recommended style tags, curated. See examples/style_tags.json.

    The vocabulary is OPEN - this is only a convenience picker, and the style box
    accepts anything. Upstream: "our tags have an open vocabulary".
    """
    try:
        raw = json.loads((ROOT / "examples" / "style_tags.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    return {k: v for k, v in raw.items() if isinstance(v, list)}


def load_style_zh() -> dict[str, str]:
    """Chinese glosses for the tag dropdowns.

    Display-only: the dropdown shows "english · 中文", but the value handed to the
    engine stays the plain English tag, because that is what the model's [Tags]
    section was trained on. Missing entries simply show English alone.
    """
    try:
        raw = json.loads((ROOT / "examples" / "style_tags_zh.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    return {k: v for k, v in raw.items() if isinstance(v, str) and not k.startswith("_")}


# 界面默认风格:刻意只用词表里有的标签,这样一打开界面,勾选状态与风格框就是一致的。
DEFAULT_STYLE = ("Mandarin, city pop, electric piano, Rhodes, groovy bass, "
                 "live drums, warm vocal, nostalgic")

TAG_CATEGORIES = ("language", "genre", "instrument", "mood", "gender", "timbre")


def split_style(text: str, tags: dict[str, list[str]]) -> dict[str, list[str]]:
    """Reverse-map a style string onto the checkbox groups.

    Used so that picking a preset re-checks the right boxes instead of just
    overwriting the text box. Tokens that are not in any list are preserved
    verbatim under "custom" - the vocabulary is open, so they are legitimate.
    """
    where: dict[str, str] = {}
    for category in TAG_CATEGORIES:
        for tag in tags.get(category, []):
            where.setdefault(tag.strip().lower(), category)

    picked: dict[str, list[str]] = {c: [] for c in TAG_CATEGORIES}
    custom: list[str] = []
    for raw in (text or "").split(","):
        token = raw.strip()
        if not token:
            continue
        category = where.get(token.lower())
        if category is None:
            custom.append(token)
        elif token not in picked[category]:
            picked[category].append(token)
    picked["custom"] = custom
    return picked


SECTIONS: list[tuple[str, str]] = [
    # (界面按钮文字, 插入歌词的标记)。标记必须保持英文:它们是模型词表里真实
    # 存在的段落标签,不是界面文案,改成中文模型就不认了。
    ("主歌 [Verse]", "[Verse]"),
    ("副歌 [Chorus]", "[Chorus]"),
    ("桥段 [Bridge]", "[Bridge]"),
    ("尾声 [Outro]", "[Outro]"),
    ("前奏 [Intro]", "[Intro]"),
    ("纯器乐 [Instrumental]", "[Instrumental]"),
]

# 语义 token 与音频时长的换算(实测 7 组全部精确命中):1 token = 1 潜帧 = 40 ms。
# 因此 时长(秒) = semantic_tokens / 25,反过来说 1 秒 = 25 个 token。
TOKENS_PER_SECOND = 25
APP_VERSION = "0.1"
# 模型默认值换算出来的边界,用作界面默认。
DEFAULT_MAX_SECONDS = 9000 // TOKENS_PER_SECOND   # 360 秒 = 6 分钟
DEFAULT_MIN_SECONDS = 200 // TOKENS_PER_SECOND    # 8 秒

TEMPLATE = """[Verse]
夜色落在窗台上
霓虹把影子拉长

[Chorus]
如果风还记得那年夏天
就让它吹过我的耳边
"""

# 纯器乐(无人声)的官方做法,来自上游 issue #18 维护者回复:
# 保留段落标签、把歌词行留空(多个换行),并从风格里去掉所有人声标签。
# 段落标签同时满足"歌词不能为空"的校验。提问者已确认有效。
#   实测:whisper 只转出 15 个词的经典幻觉("Thank you for watching..."),
#   而有唱的对照组转出 251 个词且逐字吻合 —— 即音频里没有可识别的唱词。
INSTRUMENTAL_LYRICS = "[verse]\n\n\n\n\n \n[chorus]\n\n\n\n\n\n[chorus]\n\n\n\n\n\n[outro]\n"
INSTRUMENTAL_STYLE = "English, instrumental, ambient, piano, strings, calm, relaxing"

NOTES = """
### 这个界面能做什么

| 能力 | 说明 |
|---|---|
| 文本 + 歌词 → 完整歌曲 | 48 kHz 立体声,含人声与伴奏 |
| **可编辑乐谱** | `规划路线=full` 时模型先生成 ABC 记谱,可在此导出、修改后作为输入重渲染 |
| **简谱显示** | 模型写的是 ABC 记谱法,界面自动换算成**简谱**(调号 / 八度点 / 减时线 / 附点 / 休止),各声部按小节对齐,并导出 `score.jianpu.txt` |
| 乐谱条件生成 | `规划路线=melody`,并粘贴/上传 `.abc` —— 即"改编 / 翻唱" |
| 批量出歌 | 批量标签页,多段歌词排队生成 |
| 性能指标 | 每首的 RTF、实时倍率、语义 token 数、乐谱字节数 |

> 简谱是**只读视图**,改谱仍要改 ABC 原文。模型只写 ABC,简谱是本地换算出来的,
> 换算依据是 `K:` 调号(例如 `K:D` 表示 F、C 全升)与 `L:` 时值单位。
> 换算失败不影响出歌。命令行:`scripts\\abc-to-jianpu.py --all`

### 界面有六个页签

| 页签 | 干什么 |
|---|---|
| 🎼 单首生成 | 一首一首调:风格、歌词、时长、乐谱、指标 |
| 📦 批量出歌 | 多段歌词排队生成 |
| 🗂 历史记录 | **按时间倒序**(新的在最上),有「时间」列;点表格任意一行即可试听;**选中后还能看到当时的种子 / 步数 / 全部参数,以及风格与歌词原文** |
| 📜 **日志历史** | **出问题先来这里**:任务日志(引擎完整输出)+ `logs` 目录里的服务/构建日志 |
| 🔍 **参考曲分析** | **分析一首参考曲,把它的特征变成可用的风格标签** |
| ❓ 说明与限制 | 本页 |

**一条龙**:参考曲分析 → 「🎼 标签填入「单首生成」」→ 生成一首**类似的**。

> 是"类似",**不是复刻**。复刻(改词翻唱、换指定音色)属于音色替换那条线,
> 本项目**未编入**该能力,需要重编译。

### 出问题先看哪里

1. **「📜 日志历史」页签** —— 选任务看引擎完整输出(阶段耗时 + 报错原文),可选 `logs` 下的服务/构建日志。
   有个「最新失败任务」按钮,一键定位。行数可调(0 = 全部)。
2. **生成失败时**「🎼 单首生成」页会自动摊开**失败原因 + 实际下发的参数表 + 日志尾部**。

> 历史里的**时间是产物时间**(`audio.wav` 的 mtime),不是目录时间 ——
> 目录 mtime 会被之后写进该目录的任何文件(如 `score.jianpu.txt`)刷新,时间会漂。

### 参考曲分析(第 5 个页签)

| 级别 | 内容 | 耗时 |
|---|---|---|
| ① 快速 | librosa:速度 / 律动 / 调性 / 频谱 / 动态 / 空间 + 标签建议 | 约 **9 秒**,纯 CPU |
| ② 完整 | 再加 MuScriptor 转谱:乐器标注 + 音符级 + **真实鼓型** | 约 **21 秒**,占 GPU |

- **为什么有"已知 BPM"这一栏**:鼓型要把鼓点折叠到小节网格上,而**折叠对速度极其敏感** ——
  115.4 与 117.5 只差 2%,二十几小节累积后反拍军鼓就被抹平。填了真值就准。
- **GPU 互斥**:选②时如果队列里有生成任务,**接口直接拒绝**而不是排队 ——
  MuScriptor 和 YuE2 用同一块卡,排队只会让两边抢显存。
- **建议标签只覆盖情绪/氛围类**。**语言与曲风要你自己补** ——
  语言决定唱什么语言,而这是测不出来的(要 ASR,且对无词吟唱无效)。
- 标签是**"并入"不是"覆盖"**:你原有的风格会保留,新标签按类归位
  (`ambient`→曲风,`groovy/danceable`→情绪)。

> ⚠️ 分析只给**客观特征**。"朦胧、迷离、轻抚"这类**氛围判断只能你自己听**。

### 生成失败时看什么

失败时界面**必然摊开三样**:失败原因、**实际下发的参数表**、引擎日志尾部(日志框自动可见)。

**一个已经修掉的坑,值得知道**:`abc_max_tokens` / `abc_temperature` /
`semantic_temperature` / `semantic_repetition_penalty` 这四栏是"留空即用引擎默认"的数字框,
但**浏览器里没动过的数字框提交的是 0,不是空**。而它们的引擎默认值都不是 0 ——
于是"什么都没填"反而报 `Yue2 abc sampling options are invalid`(引擎还不说是哪个参数)。
现在这四栏**把 0 当"没填"**,服务端也会**先校验再落任务**,越界会直接告诉你是哪个参数:

```
abc_max_tokens(0)必须 ≥ abc_min_tokens(32)(后者未指定,用的是引擎默认值)
```

### 规划路线怎么选

| 值 | 行为 |
|---|---|
| `off` | 直接生成,不写乐谱。最快,但没有乐谱 |
| `melody` | 只规划旋律。**推荐用于翻唱**:配 `.abc` 输入时伴奏可自由改编 |
| `full` | 规划旋律 + 和弦,可导出完整乐谱 |

### 风格:预设还是语义?能自己设定吗?

**是自由文本(语义标签),不是固定枚举。** 上游原文:*our tags have an open vocabulary*。

| 问题 | 答案 |
|---|---|
| 必须从预设里选吗? | **不用**。风格框接受任何字符串,引擎侧没有白名单校验 |
| 那几个预设是什么? | 只是 9 个常用组合的**快捷方式**;选它会覆盖风格框,之后随便改 |
| 推荐表是什么? | 上游 `top_200_tags.json`(五类:曲风 / 乐器 / 情绪 / 人声 / 音色)。选表内的词**结果更稳定**,但非强制 |
| 真的能自定义吗? | **能**。实测一段完全不在表里的风格照样出歌:`Mongolian throat singing, morin khuur, glass harmonica, whispered elder vocal, cavernous reverb, ritual` |

建议结构:**语言 + 曲风 + 乐器 + 情绪 + 音色**,逗号分隔,顺序随意。

```
Mandarin, city pop, electric piano, groovy bass, live drums, warm female vocal, nostalgic
└ 语言     └ 曲风     └ 乐器                        └ 人声           └ 情绪
```

**语言标签是最关键的一个** —— `Mandarin` / `Cantonese` / `Japanese` / `English` / `Korean`
决定唱什么语言。

> 想用推荐词表?展开「🏷 风格标签便捷选择器」逐类点选即可;
> 也可以直接手写表里没有的词,引擎一样接受。

### 纯器乐(没有歌词)怎么做?

**歌词是必填的** —— 留空会被直接拒绝(`Yue2 requires non-empty lyrics`;连
`--request-option lyrics=` 这种空值都过不了参数解析)。

**官方做法**(上游 issue #18 维护者回复,提问者已确认):**保留段落标签、把歌词行留空**,
并从风格里去掉所有人声标签。段落标签正好满足"歌词不能为空"的校验。

点 **「🎹 纯器乐模式(空歌词 + 无人声风格)」** 按钮会一次做好两件事。

实测验证(whisper-small 转写对比):

| 输入 | 转写词数 | 内容 |
|---|---|---|
| 有唱的曲子(对照) | **251** | "Soft morning light is touching the window…" —— 与已知歌词逐字吻合 |
| 官方空歌词方案 | **15** | "Thank you for watching and please subscribe…" —— whisper 在无语音音频上的**经典幻觉** |
| 歌词写 `[Instrumental]` | **6** | "Thank you very much for watching!" —— 同样是幻觉 |

幻觉短语的出现,说明音频里**没有可识别的唱词**;而对照组能逐字转出歌词,说明这个方法本身可靠。

> ⚠️ 两点如实说明:
> 1. **乐谱不能用来判断有无演唱。** 两个器乐方案的 ABC 乐谱里人声声部**仍有旋律线**
>    (音符占比 63% / 62%),但音频里没有唱词 —— 模型规划了人声轨,却因歌词为空而渲染成器乐。
> 2. **"没有可识别的唱词" ≠ "完全没有发声"。** 是否还有哼唱/元音,需要人耳或人声分离才能定论。
> 3. 上游 v1 还有个退路("输出里同时有 vocal 与 instrumental 两条音轨"),但
>    **audio.cpp 的 YuE2 移植版只输出单个立体声 WAV**,没有分轨,所以那条不适用。

### 时长怎么控制(没有直接参数)

**YuE2 没有时长参数。** 它一直生成到自然结束(EOS),所以:

| 手段 | 作用 |
|---|---|
| **歌词长度** | **主要因素** —— 段落越多、词越多,歌越长 |
| 最长时长(秒) | 硬上限 → `semantic_max_tokens`,超时会被截断 |
| 最短时长(秒) | 下限 → `semantic_min_tokens`,防止过早收尾 |

实测换算关系是**精确的**:

```
时长(秒) = 语义 tokens ÷ 25        1 token = 1 潜帧 = 40 ms
```

7 组实测(1015/1030/1088/1276/1304/1371/4867 tokens)全部精确命中 25.000 tokens/秒。
所以界面上"最长时长 360 秒"= 默认的 9000 tokens = **6 分钟**上限。

> ⚠️ 通用参数 `--duration-seconds` 对 YuE2 **无效**:实测请求 10 秒仍输出 34.9 秒。
> 该值会进 `options["duration_seconds"]`,而 YuE2 的 `apply_options` 根本不读它,
> 且未知选项不做校验、直接忽略。**别指望用它控时长。**

### 哪些地方必须用英文

不是界面没翻译,而是这些是**模型的词表**,改了就不认:

| 内容 | 为什么必须英文 |
|---|---|
| `[Verse]` `[Chorus]` `[Bridge]` `[Outro]` `[Intro]` `[Instrumental]` | 模型词表里真实存在的段落标记。**带后缀的(`[Verse 1 - 叙事铺垫]`、`[Pre-Chorus]`、`[Interlude]`)不在词表里**;实测后缀不会被唱出来,但也不保证被当成有效段落标记 —— 能用这 6 个就用这 6 个 |
| 风格提示(如 `Mandarin, city pop, warm female vocal`) | 上游 `top_200_tags.json` 的标签词表 |
| `off` / `melody` / `full` | 接口取值(界面已加中文说明) |

### 已知限制(全部实测,不是猜测)

**模型本身的边界**

| 事项 | 事实 |
|---|---|
| **气声 / 喘息** | 只能做到**"哼唱"**,没达标。真正的喘息要走语音模型混音 |
| **段落留空** | **不会**让人声退场(实测 0:00–41 一直在唱,留空段被唱成无词长音) |
| **人声退场** | 必须用 `[Instrumental]` 标记(实测这样做 0:00–30 是器乐) |
| **歌词里的括号注释** | 整行 `(音乐:…)` `(Fade out)` 这类**实测不会被唱**(ASR 三字组命中 0/298)。但引擎**不剥括号**(提示词原样拼接),这纯是模型学来的习惯、**没有保证** —— 要确定性就自己删掉 |
| **风格标签的影响** | 是**概率性**的 —— 同一提示词两次结果不同。想要某种效果请出多版挑 |
| **ABC 乐谱** | 是模型的"**计划**",与产出音频关系是松的。别拿它判断唱得对不对 |
| **若隐若现 / 若即若离** | 只能做**整轨**(ffmpeg 后期);人声单独进退需要分轨,**未编入** |
| **参考音频 / 人声参考** | **不支持**。YuE2 源码显式拒绝音频输入,能力声明 `supports_speaker_reference=false`;实测传 `--voice-ref` 输出**字节级一致**(被静默忽略) |
| **速度(BPM)** | **没有参数**,只能靠标签粗略影响 |
| **时长** | **没有参数**,由歌词长度决定(换算见上) |

**判断手段的边界(这几条决定了你不能指望什么)**

| 手段 | 能问 / 不能问 |
|---|---|
| ASR | 能问"有没有**可识别的词**";**不能**判断无词吟唱(会把纯元音塞成幻觉短语) |
| ABC 乐谱 | **不能**判断"有没有在唱"(器乐方案的乐谱里人声部照样有旋律线) |
| 本助手 | **听不到音频**(harness 输入模态只有 text 与 image);氛围判断只能你听 |

**工程上的代价**

- **`cot=off` 时没有乐谱**:模型本来就不写谱,界面也不会请求导出。
- 每首歌会**重新加载模型**(约 5–6 秒)—— 这是"每任务一个进程"换来的无状态与真实阶段进度。
- 简谱是**本地换算**的只读视图,不会被反解回 ABC;改谱仍要改 ABC 原文。

### 版本

**0.1** —— 当前形态:引擎编入 `yue2,muscriptor` + 5 页签中文界面 + 参考曲分析 + 简谱 + 氛围后期脚本。

**环境已验证**:Python 3.12.10 / torch 2.8.0+cu128 / CUDA 12.8(锁 sm_70)/ Tesla V100-SXM2-16GB /
驱动 572.83(**不要升到 580 以上**)。实时倍率:出歌 1.4–2×,转谱 5.2–18.9×。
"""


# ---------------------------------------------------------------- service client
class Api:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")

    def _url(self, path: str) -> str:
        return f"{self.base}{path}"

    def health(self) -> dict[str, Any]:
        return requests.get(self._url("/api/health"), timeout=10).json()

    def presets(self) -> dict[str, Any]:
        return requests.get(self._url("/api/presets"), timeout=10).json()

    def submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        r = requests.post(self._url("/api/jobs"), json=payload, timeout=30)
        if r.status_code >= 400:
            detail = ""
            try:
                detail = r.json().get("detail", "")
            except Exception:  # noqa: BLE001
                detail = r.text[:200]
            raise RuntimeError(detail or f"HTTP {r.status_code}")
        return r.json()

    def job(self, job_id: str) -> dict[str, Any]:
        return requests.get(self._url(f"/api/jobs/{job_id}"), timeout=20).json()

    def jobs(self, limit: int = 100) -> list[dict[str, Any]]:
        return requests.get(self._url("/api/jobs"), params={"limit": limit}, timeout=20).json()["jobs"]

    def cancel(self, job_id: str) -> None:
        requests.post(self._url(f"/api/jobs/{job_id}/cancel"), timeout=20)

    def log_tail(self, job_id: str, tail: int = 40) -> str:
        try:
            lines = requests.get(self._url(f"/api/jobs/{job_id}/log"),
                                 params={"tail": tail}, timeout=20).json()["lines"]
            return "\n".join(lines)
        except Exception:  # noqa: BLE001
            return ""

    def job_log(self, job_id: str, tail: int = 200) -> dict[str, Any]:
        """任务日志(tail<=0 = 全部行)。与 log_tail 的区别:返回元信息而不只是文本。"""
        r = requests.get(self._url(f"/api/jobs/{job_id}/log"),
                         params={"tail": tail}, timeout=60)
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code}")
        return r.json()

    def logs(self) -> list[dict[str, Any]]:
        """logs\\ 目录下的日志文件列表。"""
        return requests.get(self._url("/api/logs"), timeout=20).json()["logs"]

    def log_file(self, name: str, tail: int = 200) -> dict[str, Any]:
        r = requests.get(self._url(f"/api/logs/{name}"),
                         params={"tail": tail}, timeout=60)
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code}")
        return r.json()

    def fetch_audio(self, job_id: str) -> Optional[str]:
        """Download the result WAV so Gradio can play it from a local path."""
        try:
            r = requests.get(self._url(f"/api/jobs/{job_id}/audio"), timeout=120)
            if r.status_code != 200:
                return None
            PREVIEW.mkdir(parents=True, exist_ok=True)
            target = PREVIEW / f"{job_id}.wav"
            target.write_bytes(r.content)
            return str(target)
        except Exception:  # noqa: BLE001
            return None

    def analyze(self, payload: dict[str, Any]) -> dict[str, Any]:
        """参考曲分析。level=full 会跑 MuScriptor,可能要十几秒,所以超时给足。"""
        r = requests.post(self._url("/api/analyze"), json=payload, timeout=1800)
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail", "")
            except Exception:  # noqa: BLE001
                detail = r.text[:200]
            raise RuntimeError(detail or f"HTTP {r.status_code}")
        return r.json()


def fmt_metrics(m: dict[str, Any]) -> str:
    if not m:
        return "_尚无指标_"
    bits = []
    if m.get("wall_s") is not None:
        bits.append(f"生成耗时 **{m['wall_s']} s**")
    if m.get("audio_s") is not None:
        bits.append(f"音频时长 **{m['audio_s']} s**")
    if m.get("rtf") is not None:
        bits.append(f"RTF **{m['rtf']}**")
    if m.get("x_realtime") is not None:
        bits.append(f"**{m['x_realtime']}×** 实时")
    if m.get("semantic_tokens"):
        bits.append(f"语义 tokens {m['semantic_tokens']}")
    if m.get("abc_tokens"):
        bits.append(f"乐谱 tokens {m['abc_tokens']}")
    if m.get("abc_bytes"):
        bits.append(f"乐谱 {m['abc_bytes']} 字节")
    if m.get("input_abc_tokens"):
        bits.append(f"输入乐谱 tokens {m['input_abc_tokens']}")
    return " · ".join(bits) if bits else "_尚无指标_"


_GLYPH = "·▁▃▅█"


def _glyph_row(values: list[int]) -> str:
    peak = (max(values) if values else 1) or 1
    return "".join(_GLYPH[min(int(v / peak * 4.999), 4)] if v else "·" for v in values)


# 历史面板里参数的展示顺序。不在这里的键(以及 style/lyrics/abc)按原顺序追加或
# 单独展示 —— 服务端会丢掉"界面没填"的项(数值 0 视为未设置),所以不同任务的
# request 键不完全一样,不能假设固定的一张表。
PARAM_ORDER = ("cot", "seed", "num_inference_steps", "cfg_scale",
               "semantic_min_tokens", "semantic_max_tokens",
               "abc_max_tokens", "abc_temperature",
               "semantic_temperature", "semantic_repetition_penalty",
               "temperature", "top_p", "top_k", "repetition_penalty",
               "abc_file")


def param_markdown(request: dict[str, Any]) -> str:
    """把一次生成的参数摊成表格,给历史记录用。

    和 fmt_diag() 的区别:那个只在失败时显示、且只列会触发校验错误的那几个;
    这里列**全部**参数 —— 回看历史时最想知道的是"当时用的什么种子和步数"。
    """
    if not request:
        return "> 这个任务没有 `request.json`(早期任务不保存请求),看不到参数。"
    keys = [k for k in PARAM_ORDER if k in request]
    keys += [k for k in request
             if k not in PARAM_ORDER and k not in ("style", "lyrics", "abc")]
    rows = "\n".join(f"| `{k}` | `{request[k]}` |" for k in keys)
    return "| 参数 | 值 |\n|---|---|\n" + (rows or "| _(空)_ | |")


def fmt_diag(err: str, request: dict[str, Any], log_txt: str) -> str:
    """失败时把"错在哪、实际发下去了什么、引擎最后说了什么"一次摊开。

    引擎的报错常常只有一句(如 `Yue2 abc sampling options are invalid`),
    不看参数根本不知道是哪个值越界 —— 所以参数表必须跟着一起显示。
    """
    keys = ("cot", "seed", "num_inference_steps", "cfg_scale",
            "semantic_min_tokens", "semantic_max_tokens",
            "abc_max_tokens", "abc_temperature",
            "semantic_temperature", "semantic_repetition_penalty")
    rows = "\n".join(f"| `{k}` | `{request[k]}` |" for k in keys if k in request)
    table = ("| 参数 | 值 |\n|---|---|\n" + rows) if rows else "_(无可显示的参数)_"
    lyrics = (request.get("lyrics") or "").strip().splitlines()
    return f"""#### ❌ 失败原因

```
{err or '(引擎没有给出说明)'}
```

#### 实际下发的参数

{table}

- `style` = `{request.get('style', '')}`
- `lyrics` 前几行 = `{' / '.join(lyrics[:3])}`

> 引擎的校验错误往往不说是哪个参数。若报的是 `sampling options are invalid`,
> 对照上表看 `*_min_tokens` / `*_max_tokens` / `*_temperature` / `*_repetition_penalty`。

#### 引擎日志尾部

```
{log_txt or '(空)'}
```
"""


def fmt_analysis_basic(b: dict[str, Any]) -> str:
    """把 analyze-track.py 的结果渲染成一张人读的表。"""
    bpm_line = f"**{b['bpm']}** BPM(拍网格;按拍点间隔反算 {b['bpm_from_interval']})"
    if b.get("half_time"):
        bpm_line += f"<br>⚠️ 自相关在 **{b['bpm_half']}** 更强 → 可能按半速感知"
    bands = "  ".join(f"{k} {v * 100:.1f}%" for k, v in (b.get("band_ratios") or {}).items())
    return f"""
| 项目 | 结果 |
|---|---|
| 时长 | {b['seconds']} 秒 |
| 速度 | {bpm_line} |
| 拍点 | {b['beats']} 个,间隔中位数 {b['beat_interval_median']}s |
| 律动 | {b['meter']}/4(强弱对比 {b['meter_contrast']})`{b.get('groove') or ''}` |
| 调性 | {b['key']}(模板相关 {b['key_corr']}) |
| 频谱 | 谱质心 {b['centroid_hz']} Hz,85% 滚降 {b['rolloff85_hz']} Hz,过零率 {b['zcr']} |
| 能量分布 | {bands} |
| 动态 | 逐秒起伏 {b['env_db']} dB,LUFS {b['lufs']} |
| 空间 | 立体声宽度 {b['width']} |

> 以上都是**整轨**特征(编曲主导),**推不出唱法** —— 不要从"高频低"得出"唱得虚"。
"""


def fmt_analysis_notes(n: dict[str, Any]) -> str:
    """MuScriptor 转谱结果:乐器、速度、鼓型、进入时间轴。"""
    rows = "".join(
        f"| {i['name']} | {i['notes']} | {i['pitch_min']}–{i['pitch_max']} | "
        f"{i['first']}s | {i['last']}s |\n" for i in n["instruments"])
    out = [f"#### 乐器(MuScriptor 自己标注)\n",
           "| 乐器 | 音符 | 音域 | 首次 | 末次 |", "|---|---|---|---|---|", rows]

    t = n.get("tempo")
    if not t:
        out.append("\n_鼓点太少,跳过节奏分析_")
        return "\n".join(out)

    readings = " / ".join(f"{k} {v}" for k, v in t["readings"].items())
    out += [f"\n#### 节奏\n",
            f"- 鼓点 {t['drum_hits']} 个,{t['first']}s – {t['last']}s",
            f"- 众数间隔 **{t['modal_interval']}s**;三种读数:{readings}",
            f"- 采用 **{t['bpm']} BPM**"
            + (f"(用户指定)" if t["source"] == "user" else f"(细化后,集中度 {t['concentration']})")]

    d = n.get("drum_pattern")
    if d:
        out += [f"\n#### 鼓型(一小节 {d['bar_seconds']}s,约 {d['bars']} 小节)\n",
                "```", "        1 e & a 2 e & a 3 e & a 4 e & a"]
        out += [f"{name:8s}{_glyph_row(row)}" for name, row in d["grid"].items()]
        out.append("```")

    tl = n.get("timeline")
    if tl:
        out += [f"\n#### 进入时间轴(每 {tl['bucket_seconds']} 秒一格)\n", "```"]
        out += [f"{inst:20s}{_glyph_row(row)}" for inst, row in tl["rows"].items()]
        out.append("```")
    return "\n".join(out)


def build_ui(api: Api) -> gr.Blocks:
    try:
        presets = api.presets()
        style_presets: dict[str, str] = presets["styles"]
        stage_labels: list[str] = presets["stages"]
    except Exception:  # noqa: BLE001
        style_presets, stage_labels = {}, []
    style_tags = load_style_tags()
    style_zh = load_style_zh()
    _init = split_style(DEFAULT_STYLE, style_tags)

    def tag_choices(category: str) -> list[tuple[str, str]]:
        """(界面显示, 实际下发) —— 显示带中文解释,值保持纯英文标签。"""
        out: list[tuple[str, str]] = []
        for tag in style_tags.get(category, []):
            gloss = style_zh.get(tag)
            out.append((f"{tag} · {gloss}" if gloss else tag, tag))
        return out

    def _instrumental_preset():
        """一键纯器乐:官方空歌词写法 + 去掉全部人声标签的风格。"""
        picked = split_style(INSTRUMENTAL_STYLE, style_tags)
        return (INSTRUMENTAL_LYRICS, INSTRUMENTAL_STYLE,
                picked["language"], picked["genre"], picked["instrument"],
                picked["mood"], picked["gender"], picked["timbre"],
                ", ".join(picked["custom"]))

    def startup_banner() -> str:
        try:
            h = api.health()
        except Exception as exc:  # noqa: BLE001
            return (f"### ❌ 连不上 YuE2 服务\n\n`{api.base}` 无响应:{exc}\n\n"
                    f"请先运行 `scripts\\start-gui.ps1`。")
        ok = h.get("ok")
        icon = "✅" if ok else "❌"
        return (f"### {icon} 引擎就绪 · `{Path(h['engine']).name}`\n\n"
                f"模型 `{Path(h['model_dir']).name}` · "
                f"后端 `cuda` · 已提交任务 {h['jobs']} 个\n\n"
                f"阶段顺序:{' → '.join(stage_labels)}\n\n"
                f"<sub>yue2-V100 v{APP_VERSION} · 引擎家族 `yue2, muscriptor`</sub>")

    with gr.Blocks(title=f"YuE2 音乐生成 (V100) v{APP_VERSION}",
                   theme=gr.themes.Soft()) as demo:
        gr.Markdown(f"# 🎵 YuE2 音乐生成 · V100(SM70)本地版  <sub>v{APP_VERSION}</sub>")
        banner = gr.Markdown(startup_banner())
        job_state = gr.State("")

        with gr.Tabs():
            # ------------------------------------------------ 单首生成
            with gr.Tab("🎼 单首生成"):
                with gr.Row():
                    with gr.Column(scale=3):
                        gr.Markdown("#### 1. 风格(分类下拉多选)")
                        gr.Markdown(
                            "**风格是自由文本,不是固定枚举** —— 上游原文:*our tags have an open vocabulary*。\n"
                            "下面按五要素**分类下拉多选**,选项显示为「english · 中文」,"
                            "**下发给模型的仍是纯英文标签**。选中的标签会自动拼进下方风格框;"
                            "表里没有的词写进「自定义补充」。\n"
                            "**语言标签决定唱什么语言**,建议必选。改动会立即重算风格框,风格框也可直接手改。",
                            elem_classes="hint")
                        preset = gr.Dropdown(choices=["(自定义)"] + list(style_presets.keys()),
                                             value="(自定义)", label="常用组合(选它会重设下面所有选择)")

                        with gr.Row():
                            with gr.Column():
                                lang_pick = gr.Dropdown(choices=tag_choices("language"),
                                                        value=_init["language"], multiselect=True,
                                                        label="语言 language", info="决定唱什么语言")
                                genre_pick = gr.Dropdown(choices=tag_choices("genre"),
                                                         value=_init["genre"], multiselect=True,
                                                         label="曲风 genre")
                                inst_pick = gr.Dropdown(choices=tag_choices("instrument"),
                                                        value=_init["instrument"], multiselect=True,
                                                        label="乐器 instrument")
                            with gr.Column():
                                mood_pick = gr.Dropdown(choices=tag_choices("mood"),
                                                        value=_init["mood"], multiselect=True,
                                                        label="情绪 mood")
                                gender_pick = gr.Dropdown(choices=tag_choices("gender"),
                                                          value=_init["gender"], multiselect=True,
                                                          label="人声 gender")
                                timbre_pick = gr.Dropdown(choices=tag_choices("timbre"),
                                                          value=_init["timbre"], multiselect=True,
                                                          label="音色 timbre")
                        custom_style = gr.Textbox(label="自定义补充(不在表里的标签,逗号分隔)", lines=1,
                                                  value=", ".join(_init["custom"]),
                                                  placeholder="例如:morin khuur, throat singing, ritual")
                        style = gr.Textbox(label="最终风格提示(自动合成;也可直接手改)", lines=2,
                                           value=DEFAULT_STYLE)

                        _tag_inputs = [lang_pick, genre_pick, inst_pick, mood_pick, gender_pick, timbre_pick]

                        def on_tags_changed(lang, genre, inst, mood, gender, timbre, custom):
                            parts: list[str] = []
                            for group in (lang, genre, inst, mood, gender, timbre):
                                parts.extend(t.strip() for t in (group or []) if t.strip())
                            parts.extend(t.strip() for t in (custom or "").split(",") if t.strip())
                            return ", ".join(parts)

                        for _comp in _tag_inputs + [custom_style]:
                            _comp.change(on_tags_changed,
                                         inputs=_tag_inputs + [custom_style], outputs=style)

                        gr.Markdown("#### 2. 歌词")
                        lyrics = gr.Textbox(label="歌词(用 [Verse] / [Chorus] 分段)", lines=12,
                                            value=TEMPLATE,
                                            placeholder="[Verse]\n第一段\n\n[Chorus]\n副歌")
                        with gr.Row():
                            for chunk in (SECTIONS[:3], SECTIONS[3:]):
                                with gr.Row():
                                    for label, tag in chunk:
                                        gr.Button(label, size="sm").click(
                                            lambda cur, tag=tag: (cur or "") + f"\n{tag}\n",
                                            inputs=lyrics, outputs=lyrics)
                        with gr.Row():
                            gr.Button("填入示例歌词", size="sm").click(lambda: TEMPLATE, outputs=lyrics)
                            gr.Button("🎹 纯器乐模式(空歌词 + 无人声风格)", size="sm",
                                      variant="secondary").click(
                                lambda: _instrumental_preset(),
                                outputs=[lyrics, style, lang_pick, genre_pick, inst_pick,
                                         mood_pick, gender_pick, timbre_pick, custom_style])
                        gr.Markdown(
                            "> 上面这些 `[Verse]` / `[Chorus]` 是**模型词表里的段落标记**,必须保持英文;"
                            "按钮上的中文只是说明。风格提示同理,用英文标签。",
                            elem_classes="hint")

                        gr.Markdown("#### 3. 规划与采样")
                        with gr.Row():
                            cot = gr.Radio(
                                choices=[
                                    ("off · 直接生成(最快,无乐谱)", "off"),
                                    ("melody · 只规划旋律(翻唱推荐)", "melody"),
                                    ("full · 旋律 + 和弦(可导出完整乐谱)", "full"),
                                ],
                                value="off", label="规划路线",
                                info="提供 .abc 输入时必须选 melody 或 full")
                            seed = gr.Number(value=831001, label="随机种子", precision=0)
                        with gr.Row():
                            steps = gr.Slider(1, 64, value=8, step=1, label="NAR 步数",
                                              info="越大音质越好、越慢;官方默认 32")
                            cfg = gr.Slider(0.0, 5.0, value=1.01, step=0.01, label="语义引导强度")

                        gr.Markdown("#### 4. 时长控制")
                        with gr.Row():
                            dur_min = gr.Slider(2, 120, value=DEFAULT_MIN_SECONDS, step=1,
                                                label="最短时长(秒) · 下限",
                                                info="= semantic_min_tokens ÷ 25")
                            dur_max = gr.Slider(15, 360, value=DEFAULT_MAX_SECONDS, step=5,
                                                label="最长时长(秒) · 硬上限",
                                                info="= semantic_max_tokens ÷ 25")
                        gr.Markdown(
                            "> **时长没有直接参数。** YuE2 一直生成到自然结束(EOS),"
                            "**实际时长主要由歌词长度决定**,上面两个滑块只是边界。\n"
                            "> 实测换算是精确的:**时长(秒)= 语义 tokens ÷ 25**"
                            "(1 token = 1 潜帧 = 40 ms)。默认上限 9000 tokens = **6 分钟**。\n"
                            "> 通用参数 `--duration-seconds` 对 YuE2 **无效**:实测请求 10 秒仍输出 34.9 秒,"
                            "它会被静默忽略。",
                            elem_classes="hint")

                        with gr.Accordion("🎼 ABC 乐谱(可输入,也可导出)", open=False):
                            gr.Markdown("粘贴或上传乐谱时,规划路线必须是 `melody` 或 `full`。")
                            abc_in = gr.Textbox(label="ABC 乐谱文本", lines=8,
                                                placeholder='X:1\nM:4/4\nL:1/16\nK:C\nV: Vocal\nE2G2A2G2|')
                            abc_up = gr.File(label="或上传 .abc 文件", file_types=[".abc", ".txt"])

                        with gr.Accordion("⚙️ 高级采样参数(留空即用官方默认)", open=False):
                            with gr.Row():
                                temp = gr.Number(value=None, label="temperature", precision=3)
                                top_p = gr.Number(value=None, label="top_p", precision=3)
                                top_k = gr.Number(value=None, label="top_k", precision=0)
                                rep = gr.Number(value=None, label="repetition_penalty", precision=3)
                            with gr.Row():
                                a_max = gr.Number(value=None, precision=0,
                                                  label="abc_max_tokens(留空/0 = 用引擎默认 4096)")
                                a_temp = gr.Number(value=None, precision=3,
                                                   label="abc_temperature(留空/0 = 用默认 0.7)")
                                s_temp = gr.Number(value=None, precision=3,
                                                   label="semantic_temperature(留空/0 = 用默认 1.0)")
                                s_rep = gr.Number(value=None, precision=3,
                                                  label="semantic_repetition_penalty(留空/0 = 用默认 1.2)")
                            gr.Markdown(
                                "> 未列出的 `abc_*` / `semantic_*` 参数可用命令行传 "
                                "`--request-option <名>=<值>`,完整清单见 `docs/gui.md`。",
                                elem_classes="hint")

                        with gr.Row():
                            go = gr.Button("🎵 开始生成", variant="primary", scale=2)
                            stop = gr.Button("⏹ 取消", scale=1)

                    with gr.Column(scale=2):
                        gr.Markdown("#### 进度")
                        stage = gr.Textbox(label="当前阶段", value="空闲", interactive=False)
                        prog = gr.Slider(0, 1, value=0, label="进度", interactive=False)
                        metrics = gr.Markdown("_尚无指标_")
                        diag = gr.Markdown()
                        audio = gr.Audio(label="🎧 结果", type="filepath", autoplay=False)
                        with gr.Accordion("🎼 简谱(由模型乐谱自动换算)", open=True):
                            abc_jp = gr.HTML(
                                value="<div style='color:#888;font-size:13px'>"
                                      "生成后这里显示简谱。把「规划路线」设为 off 时模型不写谱。</div>")
                        abc_out = gr.Textbox(label="乐谱原文(ABC 记谱法)", lines=10,
                                             show_copy_button=True, interactive=False)
                        logbox = gr.Textbox(label="引擎日志尾部", lines=6, interactive=False,
                                            visible=False)
                        show_log = gr.Checkbox(label="显示引擎日志", value=False)

                def on_preset(name: str, current: str):
                    text = style_presets.get(name)
                    if not text:  # "(自定义)" - leave every control untouched
                        blank = gr.update()
                        return (current, blank, blank, blank, blank, blank, blank, blank)
                    picked = split_style(text, style_tags)
                    return (text, picked["language"], picked["genre"], picked["instrument"],
                            picked["mood"], picked["gender"], picked["timbre"],
                            ", ".join(picked["custom"]))

                preset.change(on_preset, inputs=[preset, style],
                              outputs=[style, lang_pick, genre_pick, inst_pick,
                                       mood_pick, gender_pick, timbre_pick, custom_style])
                show_log.change(lambda v: gr.update(visible=v), inputs=show_log, outputs=logbox)

                def on_generate(style_v, lyrics_v, cot_v, seed_v, steps_v, cfg_v,
                                dur_min_v, dur_max_v,
                                abc_text_v, abc_file_v, temp_v, top_p_v, top_k_v, rep_v,
                                a_max_v, a_temp_v, s_temp_v, s_rep_v):
                    def pack(stage_s, prog_v, metrics_v, audio_v, jp_v, abc_v, log_v,
                             diag_v=""):
                        return (stage_s, prog_v, metrics_v, audio_v, jp_v, abc_v, log_v,
                                diag_v)

                    payload: dict[str, Any] = {
                        "style": style_v, "lyrics": lyrics_v, "cot": cot_v,
                        "seed": int(seed_v) if seed_v is not None else None,
                        "num_inference_steps": int(steps_v) if steps_v is not None else None,
                        "cfg_scale": cfg_v,
                    }
                    abc_path = getattr(abc_file_v, "name", None) or abc_file_v
                    if abc_text_v and abc_text_v.strip():
                        payload["abc"] = abc_text_v
                    elif abc_path:
                        payload["abc_file"] = str(abc_path)
                    # Duration is bounded through the semantic token window - this model
                    # has no duration parameter. Measured: exactly 25 tokens per second.
                    payload["semantic_min_tokens"] = max(1, int(round(float(dur_min_v) * TOKENS_PER_SECOND)))
                    payload["semantic_max_tokens"] = max(
                        payload["semantic_min_tokens"],
                        int(round(float(dur_max_v) * TOKENS_PER_SECOND)))
                    for key, value in (("temperature", temp_v), ("top_p", top_p_v),
                                       ("top_k", top_k_v), ("repetition_penalty", rep_v)):
                        if value is not None and value != "":
                            payload[key] = value
                    # 这四个是"留空即默认"的数字框。浏览器里**没动过**的 gr.Number
                    # 会提交 0,而它们的引擎默认值都不是 0 —— 于是 0 会被当成显式值送下去,
                    # 撞上 max_tokens>=min_tokens / repetition_penalty>0 直接失败
                    # (实测报错:Yue2 abc sampling options are invalid)。
                    # 所以这里把 0 当"没填"。真要用 0(如 temperature=0 表示 argmax)走命令行。
                    for key, value in (("abc_max_tokens", a_max_v),
                                       ("abc_temperature", a_temp_v),
                                       ("semantic_temperature", s_temp_v),
                                       ("semantic_repetition_penalty", s_rep_v)):
                        if value not in (None, "", 0):
                            payload[key] = value

                    try:
                        job = api.submit(payload)
                    except Exception as exc:  # noqa: BLE001
                        # 提交就被拒(通常是参数越界),这时还没有 job,拿不到引擎日志,
                        # 但参数表最有价值 —— 直接把 payload 摊开。
                        yield pack(f"❌ 提交失败:{exc}", 0, "_无_", None, "", "", "",
                                   fmt_diag(str(exc), payload, ""))
                        return

                    job_id = job["id"]
                    last_stage = ""
                    while True:
                        try:
                            s = api.job(job_id)
                        except Exception as exc:  # noqa: BLE001
                            yield pack(f"⚠️ 查询失败:{exc}", 0, "_无_", None, "", "", "", "")
                            return
                        stage_txt = f"{s['stage']}({s['status']},已用 {s['elapsed_s']}s)"
                        if s["stage"] != last_stage or s["status"] != "running":
                            last_stage = s["stage"]
                        audio_path = api.fetch_audio(job_id) if s.get("has_audio") else None
                        log_txt = api.log_tail(job_id) if show_log.value else ""
                        abc_now = s.get("abc") or ""
                        yield pack(stage_txt, s.get("progress", 0), fmt_metrics(s.get("metrics") or {}),
                                   audio_path, render_jianpu(abc_now), abc_now, log_txt)
                        if s["status"] in ("done", "failed", "cancelled"):
                            if s["status"] == "failed":
                                # 失败时**强制**把日志和参数摊开 —— 以前日志写进了
                                # 一个 visible=False 的框,用户只看到一句引擎报错。
                                tail = api.log_tail(job_id, 40)
                                yield pack(f"❌ 失败:{s.get('error', '')}", s.get("progress", 0),
                                           fmt_metrics(s.get("metrics") or {}), None,
                                           render_jianpu(abc_now), abc_now,
                                           gr.update(value=tail, visible=True),
                                           fmt_diag(s.get("error", ""),
                                                    s.get("request") or {}, tail))
                            return
                        time.sleep(1.5)

                out = [stage, prog, metrics, audio, abc_jp, abc_out, logbox, diag]
                go.click(on_generate,
                         inputs=[style, lyrics, cot, seed, steps, cfg, dur_min, dur_max,
                                 abc_in, abc_up, temp, top_p, top_k, rep,
                                 a_max, a_temp, s_temp, s_rep],
                         outputs=out)

                def on_cancel(state_job):
                    return "（取消请求已发送;若已在收尾则无效）"

                stop.click(on_cancel, inputs=job_state, outputs=stage)

            # ------------------------------------------------ 批量
            with gr.Tab("📦 批量出歌"):
                gr.Markdown("多首歌词**用一行 `---` 分隔**,风格与采样参数共用。**顺序执行**"
                            "(单卡一次一首),每首约 30–60 秒。")
                with gr.Row():
                    with gr.Column(scale=3):
                        b_style = gr.Textbox(label="风格提示", lines=2,
                                             value="Mandarin, city pop, female, warm vocal, nostalgic")
                        b_lyrics = gr.Textbox(label="多首歌词(以 --- 分隔)", lines=14, value=(
                            "[Verse]\n夜色落在窗台上\n[Chorus]\n如果风还记得那年夏天\n"
                            "---\n[Verse]\nMorning trains are pulling out.\n[Chorus]\nCarry me along."
                        ))
                        with gr.Row():
                            b_cot = gr.Radio(choices=["off", "melody", "full"], value="off", label="规划路线")
                            b_steps = gr.Slider(1, 64, value=8, step=1, label="NAR 步数")
                            b_seed = gr.Number(value=831001, label="随机种子", precision=0)
                        b_go = gr.Button("📦 排队生成", variant="primary")
                    with gr.Column(scale=2):
                        b_table = gr.Dataframe(
                            headers=["#", "状态", "阶段", "音频(s)", "RTF", "乐谱", "错误"],
                            interactive=False, wrap=True)
                        b_audio = gr.Audio(label="最近一首", type="filepath")

                def on_batch(style_v, blob, cot_v, steps_v, seed_v, progress=gr.Progress()):
                    blocks = [b.strip() for b in (blob or "").split("---") if b.strip()]
                    if not blocks:
                        yield [["-", "失败", "-", "-", "-", "-", "没有解析到歌词"]], None
                        return
                    rows: list[list[Any]] = []
                    for i, lyric in enumerate(blocks, 1):
                        progress((i - 1) / len(blocks), desc=f"第 {i}/{len(blocks)} 首")
                        payload = {"style": style_v, "lyrics": lyric, "cot": cot_v,
                                   "num_inference_steps": int(steps_v), "seed": int(seed_v)}
                        try:
                            job = api.submit(payload)
                        except Exception as exc:  # noqa: BLE001
                            rows.append([i, "失败", "-", "-", "-", "-", str(exc)[:80]])
                            yield rows, None
                            continue
                        while True:
                            s = api.job(job["id"])
                            if s["status"] in ("done", "failed", "cancelled"):
                                break
                            time.sleep(1.5)
                        m = s.get("metrics") or {}
                        rows.append([i, s["status"], s["stage"], m.get("audio_s", "-"),
                                     m.get("rtf", "-"), "有" if s.get("has_abc") else "无",
                                     (s.get("error") or "")[:80]])
                        last = api.fetch_audio(job["id"]) if s.get("has_audio") else None
                        yield rows, last
                    progress(1.0, desc="全部完成")

                b_go.click(on_batch, inputs=[b_style, b_lyrics, b_cot, b_steps, b_seed],
                           outputs=[b_table, b_audio])

            # ------------------------------------------------ 历史
            with gr.Tab("🗂 历史记录"):
                gr.Markdown(
                    "**点击表格任意一行即可试听**,或用下面的下拉框选择。"
                    "历史在服务重启后依然保留 —— 服务启动时会从 `output\\gui\\jobs\\` 扫描恢复。\n\n"
                    "> 选中任务后,下方会显示**当时实际下发的全部参数**(种子 / 步数 / cfg …)"
                    "以及风格与歌词原文。两者都来自该任务的 `request.json`,是引擎真正收到的东西。",
                    elem_classes="hint")
                h_pick = gr.Dropdown(choices=[], value=None, label="选择历史任务(选中即试听)",
                                     interactive=True, filterable=True)
                with gr.Row():
                    h_refresh = gr.Button("🔄 刷新", variant="primary")
                    h_id = gr.Textbox(label="任务 ID(也可手动输入)", scale=2)
                    h_load = gr.Button("载入")
                h_table = gr.Dataframe(
                    headers=["任务", "时间", "状态", "阶段", "音频(s)", "RTF", "乐谱",
                             "种子", "步数", "歌词首行"],
                    interactive=False, wrap=True)
                h_audio = gr.Audio(label="音频", type="filepath")
                h_params = gr.Markdown(
                    value="<div style='color:#888;font-size:13px'>"
                          "选中任务后显示该次生成的全部参数。</div>")
                with gr.Accordion("风格与歌词原文(引擎实际收到的内容)", open=False):
                    h_style = gr.Textbox(label="风格 style", lines=4,
                                         show_copy_button=True, interactive=False)
                    h_lyrics = gr.Textbox(label="歌词 lyrics", lines=16,
                                          show_copy_button=True, interactive=False)
                h_jp = gr.HTML(value="<div style='color:#888;font-size:13px'>"
                                     "选中任务后显示简谱(仅当该任务生成过乐谱)。</div>")
                h_abc = gr.Textbox(label="乐谱原文(ABC 记谱法)", lines=10,
                                   show_copy_button=True, interactive=False)

                def job_choices() -> list[tuple[str, str]]:
                    out: list[tuple[str, str]] = []
                    for j in api.jobs():
                        m = j.get("metrics") or {}
                        label = f"{j.get('created_str') or '?'} · {j['id']} · {j['status']}"
                        if m.get("audio_s"):
                            label += f" · {m['audio_s']}s"
                        if j.get("has_abc"):
                            label += " · 有谱"
                        out.append((label, j["id"]))
                    return out

                def refresh():
                    rows = []
                    # api.jobs() 已按创建时间倒序 —— 新的在最上面
                    for j in api.jobs():
                        req = j.get("request") or {}
                        lyr = (req.get("lyrics") or "").splitlines()
                        first = next((ln for ln in lyr if ln.strip() and not ln.strip().startswith("[")), "")
                        m = j.get("metrics") or {}
                        rows.append([j["id"], j.get("created_str") or "-", j["status"],
                                     j["stage"], m.get("audio_s", "-"),
                                     m.get("rtf", "-"), "有" if j.get("has_abc") else "无",
                                     req.get("seed", "-"), req.get("num_inference_steps", "-"),
                                     first[:36]])
                    return rows, gr.update(choices=job_choices())

                def load(job_id):
                    if not job_id:
                        return None, "", "", "", "", ""
                    try:
                        s = api.job(job_id.strip())
                    except Exception:  # noqa: BLE001
                        return None, "", "", "", "", ""
                    abc = s.get("abc") or ""
                    req = s.get("request") or {}
                    return (api.fetch_audio(job_id.strip()) if s.get("has_audio") else None,
                            render_jianpu(abc), abc,
                            param_markdown(req), req.get("style", ""), req.get("lyrics", ""))

                def on_row(evt: gr.SelectData):
                    row = getattr(evt, "row_value", None)
                    job_id = str(row[0]) if row else ""
                    if not job_id:
                        return None, "", "", gr.update(), gr.update(), gr.update(), gr.update()
                    audio, jp, abc, params, style, lyrics = load(job_id)
                    return audio, jp, abc, job_id, params, style, lyrics

                h_refresh.click(refresh, outputs=[h_table, h_pick])
                h_pick.change(load, inputs=h_pick,
                              outputs=[h_audio, h_jp, h_abc, h_params, h_style, h_lyrics])
                h_load.click(load, inputs=h_id,
                             outputs=[h_audio, h_jp, h_abc, h_params, h_style, h_lyrics])
                h_table.select(on_row,
                               outputs=[h_audio, h_jp, h_abc, h_id, h_params, h_style, h_lyrics])
                demo.load(refresh, outputs=[h_table, h_pick])

            # ------------------------------------------------ 日志历史
            with gr.Tab("📜 日志历史"):
                gr.Markdown(
                    "出问题时**先来这里**。任务日志是每次生成时引擎的完整输出"
                    "(阶段耗时、报错原文),`logs\\` 目录里则是服务与构建/实验的日志。\n\n"
                    "> 生成失败时「🎼 单首生成」页也会自动摊开日志与参数 —— 但这里能**回看历史**。")

                with gr.Row():
                    with gr.Column(scale=1):
                        gr.Markdown("#### 任务日志")
                        lg_job = gr.Dropdown(choices=[], value=None, interactive=True,
                                             filterable=True,
                                             label="任务(按时间倒序,含失败)")
                        lg_tail = gr.Slider(20, 4000, value=200, step=20,
                                            label="显示行数(0 = 全部)")
                        with gr.Row():
                            lg_load = gr.Button("读取任务日志", variant="primary", scale=2)
                            lg_failed = gr.Button("最新失败任务", scale=2)
                        gr.Markdown("#### 服务 / 构建日志")
                        lg_file = gr.Dropdown(choices=[], value=None, interactive=True,
                                              filterable=True, label=f"logs\\ 下的文件")
                        with gr.Row():
                            lg_fload = gr.Button("读取文件", scale=2)
                            lg_refresh = gr.Button("🔄 刷新列表", scale=1)
                    with gr.Column(scale=3):
                        lg_meta = gr.Markdown("_尚未读取_")
                        lg_text = gr.Textbox(label="日志内容", lines=30, max_lines=30,
                                             show_copy_button=True, interactive=False,
                                             autoscroll=True)

                def _fmt_log(meta: str, data: dict[str, Any]) -> tuple[str, str]:
                    lines = data.get("lines") or []
                    total = data.get("total_lines", len(lines))
                    body = "\n".join(lines) if lines else "(空)"
                    return meta, body

                def refresh_log_lists():
                    jobs = api.jobs()
                    choices = [(f"{j.get('created_str') or '?'} · {j['id']} · {j['status']}",
                                j["id"]) for j in jobs]
                    try:
                        files = api.logs()
                    except Exception:  # noqa: BLE001
                        files = []
                    fchoices = [(f"{f['mtime']} · {f['name']} · {f['bytes'] // 1024} KB", f["name"])
                                for f in files]
                    return (gr.update(choices=choices), gr.update(choices=fchoices))

                def load_job_log(job_id, tail_v):
                    if not job_id:
                        return "_请先选一个任务_", ""
                    try:
                        data = api.job_log(str(job_id), int(tail_v))
                    except Exception as exc:  # noqa: BLE001
                        return f"❌ 读取失败:{exc}", ""
                    n = len(data.get("lines") or [])
                    meta = (f"**任务日志** `{job_id}` — 共 {data.get('total_lines', n)} 行,"
                            f"显示 {n} 行\n\n`{data.get('path', '')}`")
                    return _fmt_log(meta, data)

                def load_latest_failed():
                    for j in api.jobs():          # 已按时间倒序,第一个失败的就是最新的
                        if j["status"] == "failed":
                            meta, body = load_job_log(j["id"], lg_tail.value)
                            return j["id"], meta, body
                    return gr.update(), "_没有失败任务_", ""

                def load_file_log(name, tail_v):
                    if not name:
                        return "_请先选一个日志文件_", ""
                    try:
                        data = api.log_file(str(name), int(tail_v))
                    except Exception as exc:  # noqa: BLE001
                        return f"❌ 读取失败:{exc}", ""
                    n = len(data.get("lines") or [])
                    meta = (f"**文件日志** `{data.get('name')}` — 共 {data.get('total_lines', n)} 行,"
                            f"显示 {n} 行\n\n`{data.get('path', '')}`")
                    return _fmt_log(meta, data)

                lg_load.click(load_job_log, inputs=[lg_job, lg_tail],
                              outputs=[lg_meta, lg_text])
                lg_failed.click(load_latest_failed, outputs=[lg_job, lg_meta, lg_text])
                lg_fload.click(load_file_log, inputs=[lg_file, lg_tail],
                               outputs=[lg_meta, lg_text])
                lg_refresh.click(refresh_log_lists, outputs=[lg_job, lg_file])
                demo.load(refresh_log_lists, outputs=[lg_job, lg_file])

            # ------------------------------------------------ 参考曲分析
            with gr.Tab("🔍 参考曲分析"):
                gr.Markdown(
                    "分析一首参考曲,**把它的风格特征变成可直接用的风格标签** —— "
                    "然后在「🎼 单首生成」里生成一首**类似的**(不是复刻)。\n\n"
                    "> ⚠️ 分析只给**客观特征**(速度/律动/调性/频谱/乐器)。"
                    "「朦胧、迷离、轻抚」这类**氛围判断只能由你听** —— 本助手听不到音频。")

                with gr.Row():
                    with gr.Column(scale=1):
                        a_audio = gr.Audio(label="参考音频", type="filepath",
                                           sources=["upload"])
                        a_path = gr.Textbox(label="或直接填路径(优先用它)",
                                            placeholder=r"output\zh-01.wav")
                        a_level = gr.Radio(
                            choices=[("① 快速:librosa(秒级,零成本)", "basic"),
                                     ("② 完整:加 MuScriptor 转谱(音符级,占 GPU)", "full")],
                            value="basic", label="分析级别")
                        a_bpm = gr.Number(label="已知 BPM(可选;填了鼓型更准)", value=None,
                                          precision=1)
                        with gr.Row():
                            a_go = gr.Button("🔍 开始分析", variant="primary", scale=2)
                            a_fill = gr.Button("🎼 标签填入「单首生成」", scale=2)
                    with gr.Column(scale=2):
                        a_status = gr.Markdown("_尚未分析_")
                        a_tags = gr.Textbox(label="建议标签(英文;可编辑后再填入)", lines=2,
                                            show_copy_button=True)
                        a_basic = gr.Markdown()
                        a_notes = gr.Markdown()

                def on_analyze(audio_file, path_text, level, bpm_v):
                    src = (path_text or "").strip() or \
                          (getattr(audio_file, "name", None) or audio_file or "")
                    if not src:
                        return ("⚠️ 请先上传参考音频,或填一个路径", "", "", "")
                    payload: dict[str, Any] = {"audio": str(src), "level": level}
                    if bpm_v not in (None, ""):
                        payload["bpm"] = bpm_v
                    try:
                        res = api.analyze(payload)
                    except Exception as exc:  # noqa: BLE001
                        return (f"❌ 分析失败:{exc}", "", "", "")

                    basic = res["basic"]
                    tags = [t["tag"] for t in basic.get("suggested_tags", [])]
                    tag_line = ", ".join(tags)
                    head = (f"✅ 分析完成 —— **{basic['file']}**,{basic['seconds']} 秒,"
                            f"级别 `{res['level']}`")
                    if res["level"] == "basic":
                        head += "\n\n_(只跑了 librosa。要乐器与鼓型请把级别改成「完整」)_"
                    else:
                        head += f"\n\n_转谱事件:{res['notes']['events']['start']} 个音符_"
                    warn = ("\n\n⚠️ 这些标签只覆盖**情绪/氛围**类(由整轨特征推出)。"
                            "**语言(language)与曲风(genre)需要你补** —— 语言决定唱什么语言。")
                    notes_md = fmt_analysis_notes(res["notes"]) if res.get("notes") else ""
                    return head + warn, tag_line, fmt_analysis_basic(basic), notes_md

                def on_fill(current: str, tags_text: str):
                    """把建议标签**并入**现有风格框(不覆盖),再反向解析回各类下拉。"""
                    keep = [p.strip() for p in (current or "").split(",") if p.strip()]
                    have = {p.lower() for p in keep}
                    extra = [t.strip() for t in (tags_text or "").split(",")
                             if t.strip() and t.strip().lower() not in have]
                    merged = ", ".join(keep + extra)
                    picked = split_style(merged, style_tags)
                    return (merged, picked["language"], picked["genre"],
                            picked["instrument"], picked["mood"], picked["gender"],
                            picked["timbre"], ", ".join(picked["custom"]))

                a_go.click(on_analyze, inputs=[a_audio, a_path, a_level, a_bpm],
                           outputs=[a_status, a_tags, a_basic, a_notes])
                a_fill.click(on_fill, inputs=[style, a_tags],
                             outputs=[style, lang_pick, genre_pick, inst_pick,
                                      mood_pick, gender_pick, timbre_pick, custom_style])

            # ------------------------------------------------ 说明
            with gr.Tab("❓ 说明与限制"):
                gr.Markdown(NOTES)

        refresh_banner = gr.Button("🔄 刷新引擎状态")
        refresh_banner.click(startup_banner, outputs=banner)

    return demo


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--service", default="http://127.0.0.1:1414")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7860)
    ap.add_argument("--share", action="store_true")
    args = ap.parse_args()

    api = Api(args.service)
    print(f"YuE2 GUI  ->  http://{args.host}:{args.port}")
    print(f"service   ->  {args.service}")
    demo = build_ui(api)
    demo.queue().launch(server_name=args.host, server_port=args.port,
                        share=args.share, show_api=False, inbrowser=False,
                        quiet=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
