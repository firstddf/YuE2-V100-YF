#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""朦胧 / 迷离 / 轻抚 / 若隐若现 —— dream pop 氛围实验。

分两半,对应两件不同的事:

  R1 让模型先走到它能走的地方
      A 结构型:歌词段落**交替留空** —— 有词的段落唱,留空的段落变器乐,
               人声于是"进进出出"(结构上的若隐若现)。
      B 织体型:歌词写满,但风格用 dream pop / shoegaze / 埋人声一类标签,
               让人声化成一片织体(音色上的朦胧)。

  R2 再用 ffmpeg 做模型做不到的那一半
      YuE2 没有响度/力度参数,输出又是已经混好的单轨,所以"忽明忽暗、忽远忽近"
      只能后期加。四个单项 + 一个全链,每项对应一个词:
        朦胧   = 高频滚降 + 短延迟
        迷离   = 立体声加宽 + 缓慢相位 + 多级延迟
        若隐若现 = 长延迟 + 音量包络(整轨在呼吸)
        轻抚   = 去浑浊 + 中频靠近 + 轻柔压缩(不呼吸,保持贴近)
        全链   = 以上相加

  响度统一:所有变体先过 loudnorm 到 -16 LUFS **再** 叠包络 ——
  否则各变体音量不同,A/B 对比就没有意义了。

用法:
    python scripts\\dream-atmos.py              # 生成 + 处理 + 试听页(约 2 分钟)
    python scripts\\dream-atmos.py --process    # 只重跑 ffmpeg 链与试听页
    python scripts\\dream-atmos.py --asr        # 只跑 whisper 时间戳地图
"""
from __future__ import annotations

import argparse
import html
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "dream"
SERVICE = "http://127.0.0.1:1414"
FFMPEG = (os.environ.get("FFMPEG") or shutil.which("ffmpeg")
          or r"D:\mt-tool\ffmpeg\bin\ffmpeg.exe")

SEED = 831001
STEPS = 16
MIN_TOK, MAX_TOK = 1200, 1800          # 48–72 秒

# ------------------------------------------------------------------ R1 源
SOURCES = [
    {
        "name": "dream-A-structure",
        "title": "A 结构型:人声进进出出",
        "note": "歌词段落交替留空 —— 留空的段落按官方做法会变成器乐,人声于是时唱时停。",
        "style": ("Mandarin, dream pop, ethereal wave, airy female vocal, breathy vocal, "
                  "shimmering guitar, warm pad, soft drums, sub bass, spacious, "
                  "washed in reverb, dreamy, slow tempo"),
        "lyrics": """[Intro]



[Verse]
夜色很轻
落在我肩上
你的名字
我念得很慢



[Chorus]



[Verse]
再靠近一点
别说破
别说破



[Bridge]



[Verse]
灯还亮着
风还没停



[Outro]


""",
    },
    {
        "name": "dream-B-texture",
        "title": "B 织体型:人声化进混响",
        "note": "歌词写满,靠风格标签把人声推远成织体(Cocteau Twins 那种听不清词但人味很重)。",
        "style": ("Mandarin, shoegaze, dream pop, buried vocal, distant vocal, "
                  "vocal as texture, washed in reverb, wall of sound, "
                  "reverb-drenched guitar, soft drums, sub bass, hypnotic, slow tempo"),
        "lyrics": """[Verse]
夜色很轻 落在我肩上
你的名字 我念得很慢
[Chorus]
再靠近一点 别说破
再远一点 别回头
[Verse]
灯还亮着 风还没停
我在你身后 一直没走
[Chorus]
再靠近一点 别说破
再远一点 别回头
[Outro]
""",
    },
    {
        "name": "dream-C-callresponse",
        "title": "C 应答型:用 [Instrumental] 强制间奏",
        "note": "A 的教训:段落**留空**并不能让人声退场(实测 0:00–0:41 一直在唱)。"
                "改用 [Instrumental] 这个模型词表里真实存在的段落标记来强制间奏。",
        "style": ("Mandarin, dream pop, ethereal wave, airy female vocal, breathy vocal, "
                  "sparse arrangement, shimmering guitar, warm pad, sub bass, "
                  "washed in reverb, dreamy, slow tempo, spacious"),
        "lyrics": """[Intro]



[Verse]
夜色很轻 落在我肩上
[Instrumental]



[Verse]
再靠近一点 别说破
[Instrumental]



[Verse]
灯还亮着 风还没停
[Outro]


""",
    },
]

# ------------------------------------------------------------------ R2 链
# graph 是 ffmpeg 滤镜;**loudnorm 不能放在链里** —— 它自带动态压缩,会把包络吃掉
# (实测:起伏从 7.49 dB 掉到 4.99 dB)。所以改成两遍:先跑链路,量出 LUFS,
# 再用**固定增益**拉到 -16,这样动态完全保留。
#
# "若隐若现"不再用整轨音量包络(那是"忽明忽暗",不是"若即若离"),
# 而是干/湿分离:混响尾巴缓慢涨落,同时干声反向让位。
# 关键:湿信号左右用**不同延迟**,所以湿 = 宽而散(远),干 = 实而窄(近),
# 调制湿的电平就真的在改"距离";若左右同延迟,湿和干一样宽,只能改回声量(踩过)。
_PAN = "(0.5+0.5*sin(2*PI*t/{period}))"


def _wet_morph(period: int = 14, dry_duck: float = 0.45,
               wet_low: float = 0.05) -> str:
    """干湿进退图。左右用**不同**的密集抽头,湿 = 宽而散(远),干 = 实而窄(近)。"""
    pan = _PAN.format(period=period)
    rev_l = "55|125|215|330|470|640|840"
    rev_r = "75|160|260|395|560|760|980"
    dec = "0.60|0.50|0.42|0.34|0.27|0.20|0.15"
    return (
        "asplit=2[d][w];"
        f"[d]volume=volume='1-{dry_duck}*{pan}':eval=frame[dm];"
        "[w]channelsplit=channel_layout=stereo[l][r];"
        f"[l]aecho=0.85:0.9:{rev_l}:{dec}[lw];"
        f"[r]aecho=0.85:0.9:{rev_r}:{dec}[rw];"
        "[lw][rw]join=inputs=2:channel_layout=stereo[ws];"
        f"[ws]volume=volume='{wet_low}+{1 - wet_low:.2f}*{pan}':eval=frame[wm];"
        "[dm][wm]amix=inputs=2:normalize=0")      # 输出标签由 run_ffmpeg 追加

RECIPES = [
    {"id": "raw", "title": "0. 原样(模型直出)", "word": "基线",
     "desc": "未经任何处理的模型输出,作为对比基准。", "graph": None},
    {"id": "hazy", "title": "1. 朦胧", "word": "朦胧",
     "desc": "高频滚降到 5 kHz(抹掉齿音与空气感)+ 短延迟补一点空间。",
     "graph": "highpass=f=60,lowpass=f=5000,aecho=0.8:0.9:120|320:0.40|0.22"},
    {"id": "dreamy", "title": "2. 迷离", "word": "迷离",
     "desc": "侧向电平拉到 1.75(立体声加宽)+ 相位翻转 + 缓慢相位器 + 三级延迟。",
     "graph": ("stereotools=mlev=1.0:slev=1.75:phaser=1:phasel=1,"
               "aphaser=decay=0.35:speed=0.25,"
               "aecho=0.8:0.9:90|260|610:0.45|0.30|0.18")},
    {"id": "looming", "title": "3. 若隐若现 / 若即若离", "word": "远近",
     "desc": "干湿分离:立体声混响尾以 19 秒周期涨落,干声反向让位 —— 在贴近与远去之间来回。",
     "graph": _wet_morph(), "complex": True},
    {"id": "caress", "title": "4. 轻抚", "word": "轻抚",
     "desc": "切掉 110 Hz 以下的浑浊 + 3 kHz 抬 2 dB 让气息靠近 + 轻柔压缩,不加任何起伏(保持贴近)。",
     "graph": ("highpass=f=110,equalizer=f=3000:t=q:w=1:g=2,"
               "acompressor=threshold=-18dB:ratio=3:attack=20:release=400,"
               "aecho=0.8:0.9:70:0.25,stereotools=mlev=1.1:slev=1.15")},
    {"id": "full", "title": "5. 全链", "word": "全部",
     "desc": "朦胧 + 迷离 + 轻抚 打底,再叠干湿进退。",
     "graph": ("highpass=f=70,lowpass=f=6500,"
               "equalizer=f=3000:t=q:w=1:g=1.5,"
               "acompressor=threshold=-18dB:ratio=3:attack=20:release=400,"
               "aphaser=decay=0.35:speed=0.25,"
               "stereotools=mlev=1.05:slev=1.45,"
               + _wet_morph(period=14, dry_duck=0.40, wet_low=0.08)),
     "complex": True},
]

GAP_S = 1.2
TARGET_LUFS = -16.0


# ------------------------------------------------------------------ 服务
def post_job(payload: dict) -> str:
    req = urllib.request.Request(
        SERVICE + "/api/jobs", data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=30).read())["id"]


def get_job(job_id: str) -> dict:
    return json.loads(urllib.request.urlopen(f"{SERVICE}/api/jobs/{job_id}", timeout=30).read())


def fetch_audio(job_id: str, dest: Path) -> None:
    with urllib.request.urlopen(f"{SERVICE}/api/jobs/{job_id}/audio", timeout=180) as resp:
        dest.write_bytes(resp.read())


def generate_sources() -> None:
    for i, src in enumerate(SOURCES, 1):
        wav = OUT / f"{src['name']}.wav"
        if wav.exists():
            print(f"[{i}/{len(SOURCES)}] {src['title']} 已存在,跳过")
            continue
        print(f"[{i}/{len(SOURCES)}] 生成 {src['title']} ...", flush=True)
        job_id = post_job({
            "style": src["style"], "lyrics": src["lyrics"], "cot": "off",
            "seed": SEED, "num_inference_steps": STEPS,
            "semantic_min_tokens": MIN_TOK, "semantic_max_tokens": MAX_TOK, "threads": 8,
        })
        t0 = time.time()
        while True:
            job = get_job(job_id)
            if job.get("status") in ("done", "failed", "cancelled"):
                break
            print(f"    {job.get('stage','')} {job.get('progress',0)*100:5.1f}%"
                  f"  {time.time()-t0:5.1f}s", flush=True)
            time.sleep(5)
        if job.get("status") != "done":
            print(f"    !! {job.get('status')}: {job.get('error','')[:200]}")
            continue
        fetch_audio(job_id, wav)
        (OUT / f"{src['name']}.job.json").write_text(
            json.dumps({"job_id": job_id, "request": job.get("request"),
                        "metrics": job.get("metrics")}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        print(f"    ok {time.time()-t0:.1f}s -> {wav.name} "
              f"({(job.get('metrics') or {}).get('audio_s')}s)", flush=True)


# ------------------------------------------------------------------ R2
def measure_lufs(path: Path) -> float:
    p = subprocess.run([FFMPEG, "-hide_banner", "-nostats", "-i", str(path),
                        "-af", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True)
    val = 0.0
    for line in (p.stderr or "").splitlines():
        if "I:" in line and "LUFS" in line:
            try:
                val = float(line.split("I:")[1].split("LUFS")[0].strip())
            except ValueError:
                pass
    return val


def _run(args: list[str]) -> tuple[bool, str]:
    p = subprocess.run(args, capture_output=True, text=True)
    return p.returncode == 0, (p.stderr or "").strip()


def run_ffmpeg(src: Path, dst: Path, recipe: dict) -> tuple[bool, float]:
    """两遍:先跑链路量响度,再用**固定增益**对齐到 -16 LUFS。

    固定增益(而不是 loudnorm)是关键:loudnorm 是动态归一化,会把包络压平。
    """
    graph = recipe.get("graph")
    if not graph:
        shutil.copyfile(src, dst)
        return True, measure_lufs(dst)

    # 输出侧的 -ar/-ac 必须写在 -i 之后(写在前面会被当成输入选项:
    # "Option sample_rate not found");锁 48 kHz 是防那个升采样坑。
    head = ["-y", "-hide_banner", "-loglevel", "error"]
    out_opts = ["-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le"]
    stage1_path = dst.with_suffix(".stage1.wav")
    if recipe.get("complex"):
        stage1 = [FFMPEG] + head + ["-i", str(src), "-filter_complex", graph + "[pre]",
                                    "-map", "[pre]", *out_opts, str(stage1_path)]
    else:
        stage1 = [FFMPEG] + head + ["-i", str(src), "-af", graph, *out_opts,
                                    str(stage1_path)]
    ok, err = _run(stage1)
    if not ok:
        print(f"    !! stage1 失败 {dst.name}: {err[:300]}")
        stage1_path.unlink(missing_ok=True)
        return False, 0.0

    lufs = measure_lufs(stage1_path)
    gain = TARGET_LUFS - lufs if lufs else 0.0
    # 固定增益,不用 loudnorm:动态归一化会把包络压平(实测起伏 7.49 -> 4.99 dB)
    stage2 = [FFMPEG] + head + ["-i", str(stage1_path), "-af",
                                f"volume={gain:.2f}dB,alimiter=limit=0.97",
                                *out_opts, str(dst)]
    ok, err = _run(stage2)
    stage1_path.unlink(missing_ok=True)
    if not ok:
        print(f"    !! stage2 失败 {dst.name}: {err[:300]}")
        return False, lufs
    return True, lufs


def process() -> dict[str, list[dict]]:
    made: dict[str, list[dict]] = {}
    for src in SOURCES:
        source_wav = OUT / f"{src['name']}.wav"
        if not source_wav.exists():
            print(f"缺少源文件 {source_wav.name},先跑生成")
            continue
        rows = []
        for rec in RECIPES:
            dst = OUT / f"{src['name']}__{rec['id']}.wav"
            ok, lufs = run_ffmpeg(source_wav, dst, rec)
            if ok:
                print(f"  {src['name']:20s} {rec['id']:8s} ok  "
                      f"{dst.stat().st_size/1e6:6.2f} MB  链路后 {lufs:6.1f} LUFS")
                rows.append({**rec, "path": dst, "lufs_before_gain": round(lufs, 1)})
        made[src["name"]] = rows
    (OUT / "recipes.json").write_text(
        json.dumps({k: [{"id": r["id"], "title": r["title"], "graph": r.get("graph"),
                         "complex": r.get("complex", False)} for r in v]
                    for k, v in made.items()}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    return made


# ------------------------------------------------------------------ 对比页
def wav_params(path: Path):
    with wave.open(str(path)) as w:
        return w.getnchannels(), w.getsampwidth(), w.getframerate()


def build_compare(made: dict[str, list[dict]]) -> dict:
    """每个源拼一条对比带,返回各变体的起始秒数。"""
    spans: dict[str, list[dict]] = {}
    for name, rows in made.items():
        if not rows:
            continue
        ch, sw, rate = wav_params(rows[0]["path"])
        for r in rows:                      # 采样率不一致会让时长被算成 4 倍
            if wav_params(r["path"]) != (ch, sw, rate):
                print(f"  !! {r['path'].name} 采样格式与 {rows[0]['path'].name} 不一致,跳过该源")
                rows = []
                break
        if not rows:
            continue
        silence = b"\x00" * int(rate * GAP_S) * ch * sw
        out_path = ROOT / "output" / f"dream-compare-{name.split('-')[1]}.wav"
        cursor, out_rows = 0.0, []
        with wave.open(str(out_path), "wb") as out:
            out.setnchannels(ch); out.setsampwidth(sw); out.setframerate(rate)
            for i, r in enumerate(rows):
                if i:
                    out.writeframes(silence); cursor += GAP_S
                with wave.open(str(r["path"])) as s:
                    frames = s.readframes(s.getnframes())
                out.writeframes(frames)
                dur = len(frames) / (rate * ch * sw)
                out_rows.append({**r, "start": cursor, "dur": dur})
                cursor += dur
        spans[name] = out_rows
        print(f"  对比带 {out_path.name}  {cursor:.1f}s  {len(rows)} 段")
    return spans


def fmt_ts(sec: float) -> str:
    return f"{int(sec)//60}:{sec%60:04.1f}"


def build_html(spans: dict, asr: dict | None = None) -> None:
    blocks = []
    for src in SOURCES:
        rows = spans.get(src["name"])
        if not rows:
            continue
        table = "\n".join(
            f'<tr><td>{html.escape(r["title"])}</td>'
            f'<td><a href="#" onclick="seek(\'{src["name"]}\',{r["start"]:.2f});return false">'
            f'{fmt_ts(r["start"])}</a></td><td>{r["dur"]:.1f}s</td>'
            f'<td>{html.escape(r["word"])}</td></tr>' for r in rows)
        cards = "\n".join(
            f"""<section>
  <h3>{html.escape(r['title'])} <span class="tag">{html.escape(r['word'])}</span></h3>
  <div class="desc">{html.escape(r['desc'])}</div>
  <audio controls preload="none" src="dream/{html.escape(r['path'].name)}"></audio>
</section>""" for r in rows)
        asr_block = ""
        if asr and src["name"] in asr:
            asr_block = ('<div class="desc">whisper 时间戳(仅供参考,见文末说明):<br><code>'
                         + html.escape(asr[src["name"]]) + "</code></div>")
        blocks.append(f"""
<h2>{html.escape(src['title'])}</h2>
<div class="desc">{html.escape(src['note'])}</div>
<div class="big">
  <audio id="cmp-{src['name']}" controls preload="none"
         src="dream-compare-{src['name'].split('-')[1]}.wav"></audio>
  <table><tr><th>变体</th><th>起始(点击跳转)</th><th>时长</th><th>对应的词</th></tr>
{table}</table>
</div>
{asr_block}
{cards}
""")

    doc = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>YuE2 氛围实验:朦胧 / 迷离 / 轻抚 / 若隐若现</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: "Microsoft YaHei", system-ui, sans-serif; max-width: 940px;
          margin: 0 auto; padding: 24px; line-height: 1.75; }}
  h1 {{ font-size: 22px; margin-bottom: 2px; }}
  h2 {{ font-size: 18px; margin-top: 30px; border-top: 2px solid #2b5aa8; padding-top: 10px; }}
  h3 {{ font-size: 15px; margin: 0 0 4px; }}
  .sub, .desc {{ color: #666; font-size: 13.5px; }}
  .desc {{ margin: 2px 0 6px; }}
  section {{ border: 1px solid #dcdcdc; border-radius: 10px; padding: 12px 14px; margin-bottom: 10px; }}
  .tag {{ display:inline-block; font-size:12px; padding:1px 8px; border-radius:10px;
          background:#eef4ff; color:#2b5aa8; margin-left:6px; }}
  audio {{ width: 100%; margin-top: 6px; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 13.5px; margin-top: 8px; }}
  td, th {{ border-bottom: 1px solid #eee; padding: 4px 6px; text-align: left; }}
  code {{ background:#f4f4f4; padding:1px 5px; border-radius:4px; font-size:12.5px; }}
  .big {{ padding: 14px 16px; border: 2px solid #2b5aa8; border-radius: 10px; margin: 10px 0 16px; }}
  ul, ol {{ padding-left: 22px; }}
</style></head><body>

<h1>YuE2 氛围实验:朦胧 / 迷离 / 轻抚 / 若隐若现</h1>
<div class="sub">
  同 seed({SEED}) / {STEPS} 步 / {MIN_TOK//25}–{MAX_TOK//25} 秒。
  两条源分别走"结构"与"织体"两条路;每个源下面 6 个变体 = 原样 + 4 个单项 + 全链。
  <b>所有变体都先统一到 -16 LUFS 再叠包络</b>,所以音量差异不会骗你的耳朵。
</div>

{''.join(blocks)}

<section>
  <h2>为什么必须分两步</h2>
  <p>
    YuE2 <b>没有响度 / 力度参数</b>,输出又是<b>已经混好的单轨</b>。
    而"若隐若现、若即若离"在真实制作里靠的是<b>自动化</b>(音量包络、干湿比、滤波扫频、声像游走),
    单轨 + 无力度参数 = 模型这一层给不了。<b>这不是标签写得不够。</b>
  </p>
  <p>
    所以 R1 只负责"让声音本身有进有出、有虚有实",R2 才负责空间与动态。
    值得注意的是:dream pop 本来就是<b>把整体埋进空间</b>,而不是把人声单独拿出来做 ——
    所以"整轨处理"在这个风格里不是退而求其次,它接近这个风格本来的做法。
  </p>
</section>

<section>
  <h2>还差什么(以及代价)</h2>
  <ul>
    <li><b>人声单独进退</b> —— 现在的呼吸是整轨的。要做到只有人声忽远忽近,
        需要<b>人声分离</b>:audio.cpp 自带 <code>htdemucs</code> 与 <code>bs_roformer</code>
        (<code>--task sep</code>,后者直接出 vocals + instrumental),
        但我们这次编译只启用了 <code>yue2</code>(<code>AUDIOCPP_MODELS=yue2</code>),
        要用得<b>重新 configure + 编译一次</b> + 下一个约 100 MB 的权重。</li>
    <li><b>倒放混响尾巴</b> —— dream pop 的招牌音色之一(<code>areverse</code> 可做),
        本轮没加,需要时补。</li>
    <li><b>真正的"轻抚"</b>需要近讲干声 + 呼吸声本身,而呼吸声是模型最难给的东西。</li>
  </ul>
</section>

<section>
  <h2>关于 whisper 时间戳(为什么只能"参考")</h2>
  <p>
    唱歌的 ASR 天然不准,<b>无词吟唱更是它最无能为力的情形</b> —— 它会把纯元音硬塞成幻觉短语。
    所以时间戳只能回答"这一段大概有没有<em>可识别的词</em>",回答不了"人声在不在、有多虚"。
    判断请以耳朵为准。
  </p>
</section>

<script>
  const players = {{}};
  document.querySelectorAll('audio[id^="cmp-"]').forEach(a => players[a.id.slice(4)] = a);
  function seek(name, t) {{ const a = players[name]; if (a) {{ a.currentTime = t; a.play(); }} }}
</script>
</body></html>
"""
    out = ROOT / "output" / "dream-compare.html"
    out.write_text(doc, encoding="utf-8")
    print(f"试听页: {out}")


# ------------------------------------------------------------------ ASR
def asr_timeline() -> dict:
    os.environ.setdefault("HF_HUB_CACHE", r"D:\mt-tool\models_cache")
    os.environ.setdefault("HF_HOME", r"D:\mt-tool\models_cache")
    # 脚本名带连字符,不能直接 import,按路径加载
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "asr_check", ROOT / "scripts" / "asr-check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    import soundfile as sf

    asr = mod.load_asr("openai/whisper-small", False)
    out: dict[str, str] = {}
    for src in SOURCES:
        path = OUT / f"{src['name']}.wav"
        if not path.exists():
            continue
        audio, sr = sf.read(str(path), always_2d=True)
        res = asr({"array": audio.mean(axis=1), "sampling_rate": sr},
                  generate_kwargs={"language": None, "task": "transcribe"},
                  return_timestamps=True)
        chunks = res.get("chunks") or []
        bits = []
        for c in chunks:
            ts = c.get("timestamp") or (None, None)
            text = (c.get("text") or "").strip()
            if not text:
                continue
            bits.append(f"{fmt_ts(ts[0] or 0)}–{fmt_ts(ts[1] or 0)} {text}")
        line = " | ".join(bits) if bits else "(没有转出任何词)"
        out[src["name"]] = line
        print(f"\n{src['title']}\n  {line}")
    return out


# ------------------------------------------------------------------ 测量
def measure() -> None:
    """客观验证每个链是否真做了它宣称的事(我听不到,只能拿数字说话)。

      高频占比   -> 朦胧 应当明显下降
      立体声宽度  -> 迷离 应当上升
      宽度起伏    -> 若即若离 应当上升(远近在变)
      3 kHz 占比 -> 轻抚 应当上升
      LUFS      -> 都应当落在 -16 附近,否则 A/B 不公平
    """
    import numpy as np

    def stats(path: Path) -> dict:
        with wave.open(str(path)) as w:
            rate, nch = w.getframerate(), w.getnchannels()
            data = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float64)
        data = data.reshape(-1, nch) / 32768.0
        left, right = data[:, 0], data[:, -1]
        mid, side = (left + right) / 2, (left - right) / 2
        mono = data.mean(axis=1)

        frame, hop = 4096, 2048
        freqs = np.fft.rfftfreq(frame, 1.0 / rate)
        band_hf, band_3k = freqs >= 5000.0, (freqs >= 2500.0) & (freqs <= 4000.0)
        hf_ratio, k3_ratio = [], []
        win = np.hanning(frame)
        for i in range(0, len(mono) - frame, hop):
            seg = mono[i:i + frame]
            if np.sqrt((seg ** 2).mean()) < 1e-4:
                continue
            spec = np.abs(np.fft.rfft(seg * win)) ** 2
            total = spec.sum()
            if total > 0:
                hf_ratio.append(spec[band_hf].sum() / total)
                k3_ratio.append(spec[band_3k].sum() / total)

        # 每 1 秒一个窗口,既看整体宽度,也看它**是否按调制周期在起伏**。
        # 直接看标准差没用:源本身因编曲变化,宽度就在 0.70~0.17 之间大幅波动,
        # 会把调制淹没。所以改成和调制正弦做最小二乘拟合 —— 锁相相关高才说明
        # 起伏是**我们加的**,而不是编曲带来的。
        win_s = 1.0
        seg_len = int(rate * win_s)
        widths, rms_db = [], []
        for i in range(0, len(mono) - seg_len, seg_len):
            m, s = mid[i:i + seg_len], side[i:i + seg_len]
            mr = np.sqrt((m ** 2).mean())
            if mr < 1e-4:
                continue
            widths.append(float(np.sqrt((s ** 2).mean()) / mr))
            rms_db.append(20 * np.log10(max(np.sqrt((mono[i:i + seg_len] ** 2).mean()), 1e-6)))

        period = 14.0
        tv = np.arange(len(widths)) * win_s
        ref = np.sin(2 * np.pi * tv / period)
        ref = ref - ref.mean()
        wv = np.array(widths) - np.mean(widths)
        denom = float(np.sqrt((ref ** 2).sum() * (wv ** 2).sum()))
        pan_corr = float((ref * wv).sum() / denom) if denom > 1e-12 else 0.0
        rr = float((ref ** 2).sum())
        pan_pp = 2 * abs(float((ref * wv).sum() / rr)) if rr > 1e-12 else 0.0

        return {
            "hf": float(np.mean(hf_ratio)) if hf_ratio else 0.0,
            "k3": float(np.mean(k3_ratio)) if k3_ratio else 0.0,
            "width": float(np.mean(widths)) if widths else 0.0,
            "pan_corr": pan_corr,        # 与 14 秒调制周期的锁相相关
            "pan_pp": pan_pp,            # 该周期成分的峰峰值(宽度单位)
            "env_db": float(np.std(rms_db)) if rms_db else 0.0,
            "lufs": measure_lufs(path),
        }

    print(f"{'变体':26s} {'LUFS':>6s} {'高频%':>6s} {'3kHz%':>6s} {'宽度':>6s} "
          f"{'锁相相关':>8s} {'周期起伏':>8s} {'音量起伏':>8s}")
    table: dict[str, dict[str, dict]] = {}
    for src in SOURCES:
        print(f"\n【{src['title']}】")
        table[src["name"]] = {}
        for rec in RECIPES:
            p = OUT / f"{src['name']}__{rec['id']}.wav"
            if not p.exists():
                continue
            s = stats(p)
            table[src["name"]][rec["id"]] = s
            print(f"  {rec['id']:9s} {rec['word']:6s} {s['lufs']:6.1f} {s['hf']*100:6.2f} "
                  f"{s['k3']*100:6.2f} {s['width']:6.3f} {s['pan_corr']:8.3f} "
                  f"{s['pan_pp']:8.3f} {s['env_db']:8.2f}")
    (OUT / "measurements.json").write_text(
        json.dumps(table, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n期望 vs 实测(相对原样):")
    for name, rows in table.items():
        base = rows.get("raw")
        if not base:
            continue
        print(f"\n  {name}")
        for rec in RECIPES:
            if rec["id"] == "raw" or rec["id"] not in rows:
                continue
            s = rows[rec["id"]]
            print(f"    {rec['id']:10s} 高频{(s['hf']-base['hf'])*100:+6.2f}pp  "
                  f"宽度{s['width']-base['width']:+.3f}  "
                  f"锁相相关{s['pan_corr']:+.3f}  "
                  f"周期起伏{s['pan_pp']:+.3f}  "
                  f"3kHz{(s['k3']-base['k3'])*100:+5.2f}pp  LUFS{s['lufs']-base['lufs']:+.1f}")


# ------------------------------------------------------------------ main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--process", action="store_true", help="只重跑 ffmpeg 链与试听页")
    ap.add_argument("--asr", action="store_true", help="只跑 whisper 时间戳")
    ap.add_argument("--measure", action="store_true", help="只打印客观测量")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    if not Path(FFMPEG).exists() and not shutil.which(FFMPEG):
        print(f"找不到 ffmpeg: {FFMPEG}", file=sys.stderr)
        return 1

    if args.measure:
        measure()
        return 0

    if args.asr:
        build_html(build_compare(_load_existing()), asr_timeline())
        return 0

    if not args.process:
        try:
            urllib.request.urlopen(SERVICE + "/api/health", timeout=5).read()
        except Exception:  # noqa: BLE001
            print(f"服务未启动({SERVICE}):先跑 scripts\\start-gui.ps1", file=sys.stderr)
            return 1
        generate_sources()

    made = process()
    if not made:
        print("没有可处理的源文件", file=sys.stderr)
        return 1
    build_html(build_compare(made))
    return 0


def _load_existing() -> dict[str, list[dict]]:
    made: dict[str, list[dict]] = {}
    for src in SOURCES:
        rows = []
        for rec in RECIPES:
            p = OUT / f"{src['name']}__{rec['id']}.wav"
            if p.exists():
                rows.append({**rec, "path": p})
        made[src["name"]] = rows
    return made


if __name__ == "__main__":
    sys.exit(main())
