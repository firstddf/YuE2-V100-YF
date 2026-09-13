#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""气声 / 喘息 / 叹息 实验:生成 6 条变体 + 拼接对比文件 + HTML 试听页。

用法:
    python scripts/breath-vocals.py            # 全部生成(约 3-5 分钟)
    python scripts/breath-vocals.py --build    # 只重建对比 wav 与 html

设计要点:
  * 歌词走 [Tags]/[Lyrics] 纯文本模板(pipeline.cpp request_text()),
    所以括号、破折号、感叹号都只是普通字符 —— 模型有没有学过这种用法正是本实验要回答的。
  * YuE2 没有任何响度/力度参数,"低声"只能通过音色标签去逼近,不能真的让它唱小声。
  * 6 条同 seed / 同步数 / 同时长上限,差异只来自 style 与歌词,方便横向比较。
  * 第 6 条是一组对照:歌词与第 1 条完全相同,只把气声类标签换成普通标签,
    用来回答"气声标签到底有没有起作用"。
"""

import argparse
import html
import json
import sys
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "output" / "breath"
SERVICE = "http://127.0.0.1:1414"

SEED = 831001
STEPS = 16
MIN_TOK = 450      # 18.0 s
MAX_TOK = 500      # 20.0 s
GAP_S = 1.2

VARIANTS = [
    {
        "name": "breath-1-qi",
        "title": "1. 气声短音 · 暗色 R&B",
        "tag": "气声",
        "style": ("Mandarin, dark R&B, breathy female vocal, whispery vocal, "
                  "husky vocal, close-mic, minimal punchy beat, syncopated groove, "
                  "sub bass, warm"),
        "lyrics": """[Verse]
啊
哈
嘿
啊
哈
嘿
[Chorus]
呼
哈
嘿
呼
哈
嘿""",
        "listen": "每个音节是否单独落在拍点上?元音里有没有\"气\"的沙沙成分,还是干净实唱?",
    },
    {
        "name": "breath-2-sigh",
        "title": "2. 叹息 · 氛围慢歌",
        "tag": "叹息",
        "style": ("Mandarin, ambient pop, breathy vocal, intimate close-mic vocal, "
                  "soft vocal, downtempo, sparse piano, warm pad, slow tempo"),
        "lyrics": """[Verse]
唉——
呼——
唉——
嗯——
[Chorus]
啊——
哈——
呼——""",
        "listen": "长元音结尾是不是有\"泄气\"的下行尾巴(叹息感)?破折号有没有被读成长音?",
    },
    {
        "name": "breath-3-paren",
        "title": "3. 括号气声点缀 · 主唱 + ad-lib",
        "tag": "括号测试",
        "style": ("Mandarin, city pop, breathy female vocal, close-mic, "
                  "groovy bass, crisp drums, warm chorus"),
        "lyrics": """[Verse]
夜色落在窗台上
(哈)
霓虹把影子拉长
(嘿)
[Chorus]
如果风还记得那年夏天
(啊——)
请替我说声再见
(呼——)""",
        "listen": "括号里的内容变成了「伴唱/衬托」还是被当成正文唱出来?它和主唱是两条声部吗?",
    },
    {
        "name": "breath-4-hook",
        "title": "4. 节奏喘息 hook · trap soul",
        "tag": "节奏感",
        "style": ("Mandarin, trap soul, ASMR whisper vocal, breathy vocal, "
                  "808 bass, punchy minimal beat, hi-hat rolls, sparse arrangement"),
        "lyrics": """[Verse]
哈!
嘿!
哈!
呼!
啊!
嘿!
哈!
呼!
[Chorus]
嘿!
哈!
嘿!
呼!
啊!
哈!
嘿!
呼!""",
        "listen": "像不像打击乐式的\"吐气推进\"?感叹号有没有带来更短促的收音?",
    },
    {
        "name": "breath-5-chant",
        "title": "5. 组合音节 · 舞曲",
        "tag": "组合",
        "style": ("Mandarin, dance pop, breathy vocal, whispered hook, "
                  "four-on-the-floor, punchy kick, synth bass, bright plucks"),
        "lyrics": """[intro]
啊哈
嘿哈
啊哈
嘿哈
[Verse]
低声啊 低声哈
低声嘿 低声呼
[Chorus]
啊哈 嘿哈 啊哈 嘿哈
呼 哈 嘿 呼 哈 嘿""",
        "listen": "两个音节连读(啊哈)会不会黏在一起?中文\"低声\"两字有没有影响唱法?",
    },
    {
        "name": "breath-6-plain",
        "title": "6. 对照组:同样歌词,去掉气声标签",
        "tag": "对照",
        "style": ("Mandarin, dark R&B, clear female vocal, "
                  "minimal punchy beat, syncopated groove, sub bass, warm"),
        "lyrics": """[Verse]
啊
哈
嘿
啊
哈
嘿
[Chorus]
呼
哈
嘿
呼
哈
嘿""",
        "listen": "与第 1 条逐句对照 —— 如果听不出差别,说明气声标签基本没生效;"
                  "如果第 1 条明显更\"虚\"更\"近\",标签就是有效手段。",
    },
]


# ------------------------------------------------------------------ service
def post_job(payload: dict) -> str:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        SERVICE + "/api/jobs", data=data,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))["id"]


def get_job(job_id: str) -> dict:
    with urllib.request.urlopen(f"{SERVICE}/api/jobs/{job_id}", timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_audio(job_id: str, dest: Path) -> None:
    with urllib.request.urlopen(f"{SERVICE}/api/jobs/{job_id}/audio", timeout=120) as resp:
        dest.write_bytes(resp.read())


def run_variant(v: dict, index: int, total: int) -> dict:
    print(f"[{index}/{total}] {v['title']} ...", flush=True)
    payload = {
        "style": v["style"],
        "lyrics": v["lyrics"],
        "cot": "off",
        "seed": SEED,
        "num_inference_steps": STEPS,
        "semantic_min_tokens": MIN_TOK,
        "semantic_max_tokens": MAX_TOK,
        "threads": 8,
    }
    job_id = post_job(payload)
    t0 = time.time()
    while True:
        job = get_job(job_id)
        status = job.get("status")
        if status in ("done", "failed", "cancelled"):
            break
        print(f"    {job.get('stage','')} {job.get('progress',0)*100:5.1f}%"
              f"  {time.time()-t0:5.1f}s", flush=True)
        time.sleep(5)
    wall = time.time() - t0
    if status != "done":
        print(f"    !! {status}: {job.get('error','')[:200]}", flush=True)
        return {"variant": v, "job_id": job_id, "ok": False,
                "error": job.get("error", status), "wall": wall}

    wav = OUT_DIR / f"{v['name']}.wav"
    fetch_audio(job_id, wav)
    metrics = job.get("metrics") or {}
    audio_s = float(metrics.get("audio_s") or 0.0)
    if not audio_s:
        _ch, _sw, rate, frames = wav_info(wav)
        audio_s = frames / rate
    print(f"    ok  {wall:.1f}s wall / {audio_s:.1f}s audio -> {wav.name}", flush=True)
    return {"variant": v, "job_id": job_id, "ok": True, "wall": wall,
            "audio_s": audio_s, "path": wav, "metrics": metrics}


# ------------------------------------------------------------------ compare
def wav_info(path: Path):
    with wave.open(str(path)) as w:
        return w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()


def build_compare(results: list) -> list:
    ok = [r for r in results if r["ok"] and r["path"].exists()]
    if not ok:
        raise SystemExit("没有成功产物,无法拼接")

    ch, sw, rate, _ = wav_info(ok[0]["path"])
    silence = b"\x00" * int(rate * GAP_S) * ch * sw

    out_path = ROOT / "output" / "breath-compare.wav"
    cursor = 0.0
    spans = []
    with wave.open(str(out_path), "wb") as out:
        out.setnchannels(ch)
        out.setsampwidth(sw)
        out.setframerate(rate)
        for i, r in enumerate(ok):
            if i:
                out.writeframes(silence)
                cursor += GAP_S
            with wave.open(str(r["path"])) as src:
                frames = src.readframes(src.getnframes())
            out.writeframes(frames)
            dur = len(frames) / (rate * ch * sw)
            spans.append({"result": r, "start": cursor, "dur": dur})
            cursor += dur
    print(f"\n对比文件: {out_path}  ({cursor:.1f}s, {len(ok)} 段)")
    return spans


def fmt_ts(seconds: float) -> str:
    return f"{int(seconds)//60}:{seconds%60:04.1f}"


def build_html(spans: list) -> None:
    rows = "\n".join(
        f'    <tr><td>{i+1}. {html.escape(s["result"]["variant"]["title"].split(". ",1)[-1])}</td>'
        f'<td><a href="#" onclick="seek({s["start"]:.2f});return false">{fmt_ts(s["start"])}</a></td>'
        f'<td>{s["dur"]:.1f}s</td></tr>'
        for i, s in enumerate(spans))
    sections = "\n".join(
        f"""<section>
  <h2>{html.escape(s["result"]["variant"]["title"])} <span class="tag">{html.escape(s["result"]["variant"]["tag"])}</span></h2>
  <div>风格:<code>{html.escape(s["result"]["variant"]["style"])}</code></div>
  <pre>{html.escape(s["result"]["variant"]["lyrics"])}</pre>
  <div style="color:#666;font-size:14px;">听点:{html.escape(s["result"]["variant"]["listen"])}</div>
  <audio controls preload="none" src="breath/{html.escape(s["result"]["variant"]["name"])}.wav"></audio>
</section>""" for s in spans)

    doc = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>YuE2 气声 / 喘息 / 叹息 对比试听</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: "Microsoft YaHei", system-ui, sans-serif; max-width: 900px; margin: 0 auto; padding: 24px; line-height: 1.7; }}
  h1 {{ font-size: 22px; margin-bottom: 4px; }}
  .sub {{ color: #666; font-size: 14px; margin-bottom: 22px; }}
  section {{ border: 1px solid #d8d8d8; border-radius: 10px; padding: 14px 16px; margin-bottom: 14px; }}
  h2 {{ font-size: 16px; margin: 0 0 6px; }}
  .tag {{ display: inline-block; font-size: 12px; padding: 1px 8px; border-radius: 10px; background: #eef4ff; color: #2b5aa8; margin-left: 6px; }}
  .warn {{ background: #fff6e5; color: #8a5a00; }}
  audio {{ width: 100%; margin-top: 8px; }}
  pre {{ background: #f7f7f7; padding: 8px 10px; border-radius: 6px; font-size: 13px; white-space: pre-wrap; margin: 6px 0; }}
  code {{ background: #f4f4f4; padding: 1px 5px; border-radius: 4px; font-size: 13px; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 14px; margin-top: 8px; }}
  td, th {{ border-bottom: 1px solid #eee; padding: 4px 6px; text-align: left; }}
  .big {{ padding: 16px; border: 2px solid #2b5aa8; border-radius: 10px; margin-bottom: 22px; }}
  ul, ol {{ padding-left: 22px; }}
</style>
</head>
<body>

<h1>YuE2 气声 / 喘息 / 叹息 对比试听</h1>
<div class="sub">
  同 seed({SEED})、{STEPS} 步 ODE、{MAX_TOK//25} 秒上限。6 条里 <b>第 6 条是对照组</b> ——
  歌词与第 1 条一字不差,只把气声标签换成普通标签。<b>听感判断只能由人做</b>。
</div>

<div class="big">
  <h2 style="margin-top:0">🎧 一次听完({GAP_S} 秒间隔,共 {sum(s['dur'] for s in spans)+GAP_S*(len(spans)-1):.0f} 秒)</h2>
  <audio id="cmp" controls preload="none" src="breath-compare.wav"></audio>
  <table>
    <tr><th>片段</th><th>起始(点击跳转)</th><th>时长</th></tr>
{rows}
  </table>
</div>

{sections}

<section>
  <h2>先读这条:一个硬边界</h2>
  <p>
    YuE2 <b>没有响度 / 力度 / 强弱参数</b>。它输出的是归一化后的立体声 WAV,
    所以你没法命令它"唱小声一点"。"低声"这件事只能在<b>音色</b>层面去逼近
    (<code>breathy</code> / <code>whispery</code> / <code>husky</code> / <code>close-mic</code>),
    不能指望它真的把电平压下去。
  </p>
  <p>
    另外要如实说明:<b>真正的"喘息"是不可听的噪声(无音高)</b>,
    而这是一个学唱歌的模型。它更可能给出"带气声的短元音",而不是纯呼气噪声。
    所以第 1 / 4 条重点听的是<b>元音里有没有气流成分</b>,而不是"有没有喘气声"。
  </p>
</section>

<section>
  <h2>怎么"写"出这类人声(本次使用的手法)</h2>
  <ol>
    <li><b>把每个音节放单独一行</b> —— 换行是节拍线索。一行一个"哈"比一行十个更容易落在拍上。</li>
    <li><b>用语气字而不是词</b>:啊 / 哈 / 嘿 / 呼 / 唉 / 嗯 / 嘶 / 嚯+ 感叹号与破折号。</li>
    <li><b>音色标签点名气声</b>:<code>breathy vocal</code>、<code>whispery vocal</code>、
        <code>husky vocal</code>、<code>intimate close-mic vocal</code>、<code>ASMR whisper vocal</code>。</li>
    <li><b>曲风选本来就大量用气声的类型</b>:dark R&amp;B / trap soul / ambient pop / city pop。
        曲风标签会顺带决定编曲密度,气声需要留白。</li>
    <li><b>节奏感靠编曲标签</b>:<code>minimal punchy beat</code>、<code>syncopated groove</code>、
        <code>808 bass</code>、<code>four-on-the-floor</code>。</li>
  </ol>
</section>

<section>
  <h2>要判断什么(建议顺序)</h2>
  <ol>
    <li><b>第 6 条 vs 第 1 条</b>:同样的歌词,气声标签有没有带来可听的差异?这是整套手法的开关。</li>
    <li><b>气息成分</b>:元音是"实的"还是"虚的"?有没有沙沙的空气声?</li>
    <li><b>落拍</b>:音节是否踩在鼓点上(节奏动感),还是被拉成连贯旋律线?</li>
    <li><b>叹息的下行尾音</b>:第 2 条长音结尾有没有泄气感?</li>
    <li><b>括号</b>:第 3 条括号内容成了伴唱,还是被当正文唱了?</li>
  </ol>
</section>

<section>
  <h2>如果都不到位:还有一条更可控的路</h2>
  <p>
    用 YuE2 出<b>伴奏</b>(空歌词段落 = 器乐),再用 TTS / 语音克隆模型单独生成气声、
    喘息、ad-lib,最后混音。这样"低声""换气""叹息"是<b>直接可控</b>的,不靠模型猜。
    audio.cpp 里已内置多个 TTS 与语音转换模型(如 chatterbox / f5-tts / kokoro / seed-vc),
    这条混合路线需要时可以做。当前这 6 条是<b>纯 YuE2 单模型</b>能走到的上限。
  </p>
</section>

<script>
  const audio = document.getElementById('cmp');
  function seek(t) {{ audio.currentTime = t; audio.play(); }}
</script>
</body>
</html>
"""
    out = ROOT / "output" / "breath-compare.html"
    out.write_text(doc, encoding="utf-8")
    print(f"试听页:   {out}")


# ------------------------------------------------------------------ metrics
def spectral_metrics(path: Path) -> dict:
    """客观指标:高频能量占比 / 谱质心 / 过零率。

    「气声」在物理上就是声门闭合不完全 -> 宽带噪声多 -> 高频能量与过零率偏高。
    所以这三个数是"有没有唱虚"的代理指标。
    注意:风格标签同时改变了编曲(镲片、合成器也会拉高高频),所以它只作参考,
    不能替代人耳;第 1 与第 6 条是唯一可直接对照的一对(歌词与 seed 完全相同)。
    """
    import numpy as np

    with wave.open(str(path)) as w:
        rate = w.getframerate()
        data = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float64)
    if data.size == 0:
        return {}
    data /= 32768.0
    if w.getnchannels() == 2:
        data = data.reshape(-1, 2).mean(axis=1)

    frame, hop = 2048, 1024
    win = np.hanning(frame)
    freqs = np.fft.rfftfreq(frame, 1.0 / rate)
    band = freqs >= 5000.0
    hf_ratio, centroid, energy = [], [], []
    for start in range(0, len(data) - frame, hop):
        seg = data[start:start + frame]
        if np.sqrt((seg ** 2).mean()) < 1e-4:      # 静音帧不计
            continue
        spec = np.abs(np.fft.rfft(seg * win)) ** 2
        total = spec.sum()
        if total <= 0:
            continue
        hf_ratio.append(spec[band].sum() / total)
        centroid.append((spec * freqs).sum() / total)
        energy.append(total)
    zcr = float(np.mean(np.abs(np.diff(np.signbit(data).astype(np.int8)))))

    return {
        "sec": round(len(data) / rate, 2),
        "hf_ratio": round(float(np.mean(hf_ratio)), 4) if hf_ratio else 0.0,
        "centroid_hz": round(float(np.mean(centroid)), 0) if centroid else 0.0,
        "zcr": round(zcr, 4),
    }


def print_metrics() -> int:
    names = [v for v in VARIANTS if (OUT_DIR / f"{v['name']}.wav").exists()]
    if not names:
        print("还没有产物", file=sys.stderr)
        return 1
    print(f"{'变体':32s} {'时长':>6s} {'高频能量占比':>12s} {'谱质心Hz':>9s} {'过零率':>8s}")
    rows = {}
    for v in names:
        m = spectral_metrics(OUT_DIR / f"{v['name']}.wav")
        rows[v["name"]] = m
        print(f"{v['name']:32s} {m['sec']:6.1f} {m['hf_ratio']:12.4f} "
              f"{m['centroid_hz']:9.0f} {m['zcr']:8.4f}")
    a, b = rows.get("breath-1-qi"), rows.get("breath-6-plain")
    if a and b:
        print(f"\n第 1 条 vs 第 6 条(同歌词同 seed,只有气声标签不同):")
        print(f"  高频能量占比 {a['hf_ratio']:.4f} vs {b['hf_ratio']:.4f}"
              f"  -> {'气声版更高' if a['hf_ratio'] > b['hf_ratio'] else '气声版更低/持平'}")
        print(f"  过零率       {a['zcr']:.4f} vs {b['zcr']:.4f}"
              f"  -> {'气声版更高' if a['zcr'] > b['zcr'] else '气声版更低/持平'}")
        print("  提示:编曲差异也会影响这两个数,结论请以人耳为准。")
    return 0


# ------------------------------------------------------------------ main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true", help="只重建对比 wav 与 html")
    ap.add_argument("--metrics", action="store_true", help="只打印客观指标")
    args = ap.parse_args()

    if args.metrics:
        return print_metrics()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path = OUT_DIR / "manifest.json"

    if args.build:
        if not manifest_path.exists():
            print("缺少 manifest.json,无法重建", file=sys.stderr)
            return 1
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        results = []
        for item in raw:
            v = next(x for x in VARIANTS if x["name"] == item["name"])
            results.append({"variant": v, "job_id": item.get("job_id", ""),
                            "ok": item.get("ok", False), "audio_s": item.get("audio_s", 0),
                            "wall": item.get("wall", 0), "path": OUT_DIR / f"{item['name']}.wav"})
        spans = build_compare(results)
        build_html(spans)
        return 0

    try:
        urllib.request.urlopen(SERVICE + "/api/health", timeout=5).read()
    except urllib.error.URLError as exc:
        print(f"服务未启动({SERVICE}):先跑 scripts\\start-gui.ps1", file=sys.stderr)
        return 1

    results = []
    for i, v in enumerate(VARIANTS, 1):
        results.append(run_variant(v, i, len(VARIANTS)))

    manifest_path.write_text(json.dumps(
        [{"name": r["variant"]["name"], "job_id": r.get("job_id", ""),
          "ok": r["ok"], "wall": round(r.get("wall", 0), 1),
          "audio_s": round(r.get("audio_s", 0), 2)} for r in results],
        ensure_ascii=False, indent=2), encoding="utf-8")

    ok = sum(1 for r in results if r["ok"])
    print(f"\n完成 {ok}/{len(results)} 条,manifest: {manifest_path}")
    spans = build_compare(results)
    build_html(spans)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
