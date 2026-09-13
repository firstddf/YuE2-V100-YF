#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把一段音频"读"成可用的风格提示词(不用重编译,纯 CPU)。

为什么要有这个:YuE2 不接受参考音频,唯一的音频相关条件是 ABC 乐谱。
所以"参考一首歌"这件事只能拆成:先**测出**它的客观特征,再把特征**翻译**成
风格标签和简谱,喂给 YuE2。这个脚本负责第一步和第二步的一半。

测什么(全部来自 librosa 的信号分析,不是猜):
    速度     BPM + 倍频歧义判定(用 onset 自相关挑半速/原速/倍速)
    律动     拍号(3/4 vs 4/4,梳状对比)+ 每格强弱型(小节折叠加平均)
    调性     平均 chroma 与 Krumhansl 大小调模板相关
    音色     高频占比 / 谱质心 / 谱滚降 / 过零率
    动态     逐秒 RMS 起伏(std dB)+ LUFS
    空间     立体声宽度(side/mid)

⚠️ 边界(必须说清):
  * 测的是**整轨**,不是人声 —— 频谱特征由编曲主导,不能拿来推"唱法"。
  * 标签建议里凡是**词表外的**,需要手打进风格框(词表开放,模型仍接受)。
  * 调性→情绪 那条是**弱推断**,别当结论。
  * 这条链只给"像什么",给不了"一模一样"。ABC 是计划,不是复刻。

用法:
    python scripts\\analyze-track.py output\\zh-01.wav output\\dream\\dream-A-structure.wav
    python scripts\\analyze-track.py --json results\\track-profile.json <files...>
"""
from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[1]

# Krumhansl-Schmuckler 调性模板
_MAJOR_PROFILE = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
_MINOR_PROFILE = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
_NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def load_vocab() -> set[str]:
    try:
        data = json.loads((ROOT / "examples" / "style_tags.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return set()
    out: set[str] = set()
    for key, value in data.items():
        if not key.startswith("_") and isinstance(value, list):
            out.update(str(v) for v in value)
    return out


# ------------------------------------------------------------------ 测量
def measure(path: Path) -> dict:
    import librosa
    import numpy as np
    import pyloudnorm

    # 单声道 22.05k:速度 / 律动 / 调性 / 频谱
    y, sr = librosa.load(str(path), sr=22050, mono=True)
    hop = 512
    fps = sr / hop
    oenv = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)

    tempo, beat_frames = librosa.beat.beat_track(
        onset_envelope=oenv, sr=sr, hop_length=hop, units="frames")
    bpm_raw = float(np.atleast_1d(tempo)[0])

    # 倍频歧义:onset 自相关在 半速/原速/倍速 上的强度。
    # ⚠️ 不能拿"最强"直接当 BPM —— 4/4 流行里底鼓 1、3 拍 + 军鼓 2、4 拍,
    # 会让**两拍(半小节)**的周期性往往强于单拍,于是自动选半速是**系统性偏差**。
    # 所以:BPM 以 librosa 的节拍网格为准(并用拍点间隔自洽校验),
    # 自相关只用来判断"是否存在半速感知",两个档位都报给用户。
    ac = librosa.autocorrelate(oenv - oenv.mean())
    ac0 = float(ac[0]) or 1e-9

    def ac_at(candidate: float) -> float:
        lag = int(round(60.0 / candidate * fps))
        return float(ac[lag]) / ac0 if 0 < lag < len(ac) else 0.0

    options = {round(bpm_raw * k, 1): ac_at(bpm_raw * k) for k in (0.5, 1.0, 2.0)}
    options = {k: v for k, v in options.items() if 40.0 <= k <= 240.0} or {round(bpm_raw, 1): 1.0}
    half = round(bpm_raw * 0.5, 1)
    half_competitive = bool(half in options
                            and options[half] >= 0.9 * max(options.values())
                            and options[half] > options.get(round(bpm_raw, 1), 0.0))

    beat_frames = np.asarray(beat_frames)
    intervals = np.diff(beat_frames) / fps if len(beat_frames) > 1 else np.array([0.0])
    median_interval = float(np.median(intervals)) if len(intervals) else 0.0
    bpm_from_interval = round(60.0 / median_interval, 1) if median_interval else 0.0
    strengths = oenv[beat_frames] if len(beat_frames) else np.array([0.0])

    # 拍号:把每拍强度按 N 相位折叠,看哪个 N 的强弱对比更明显
    meter, meter_contrast = 4, 0.0
    for candidate in (3, 4):
        if len(strengths) < candidate * 2:
            continue
        phase = np.arange(len(strengths)) % candidate
        prof = np.array([strengths[phase == i].mean() for i in range(candidate)])
        contrast = float((prof.max() - prof.min()) / (prof.mean() + 1e-9))
        if contrast > meter_contrast:
            meter, meter_contrast = candidate, contrast

    # 小节内强弱型:每拍再切 2 格
    cells = meter * 2
    groove = ""
    if len(strengths) >= cells:
        phase = np.arange(len(strengths)) % cells
        prof = np.array([strengths[phase == i].mean() for i in range(cells)])
        prof = prof / (prof.max() + 1e-9)
        groove = "".join("·▁▃▅█"[min(int(v * 4.999), 4)] for v in prof)

    # 调性
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr).mean(axis=1)
    best = ("?", "?", -2.0)
    for root in range(12):
        for mode, profile in (("大调", _MAJOR_PROFILE), ("小调", _MINOR_PROFILE)):
            corr = float(np.corrcoef(chroma, np.roll(profile, root))[0, 1])
            if np.isnan(corr):
                continue
            if corr > best[2]:
                best = (_NOTE_NAMES[root], mode, corr)

    # 频谱:全部走 librosa 的标准实现,别自己重定义"谱质心"。
    # 踩过的坑:我原先用**功率**加权算质心,得到 558 Hz;librosa 用**幅度**(标准定义)
    # 是 2131 Hz,差 4 倍 —— 因为这首歌 71% 的能量在 400 Hz 以下,功率加权会被低频带偏。
    S = np.abs(librosa.stft(y, n_fft=2048)) ** 2          # 功率谱
    freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
    total = float(S.sum()) or 1e-9
    bands = [("<150Hz", 0, 150), ("150-400", 150, 400), ("400-1k", 400, 1000),
             ("1k-2.5k", 1000, 2500), ("2.5k-5k", 2500, 5000), (">5k", 5000, sr / 2)]
    band_ratios = {name: round(float(S[(freqs >= lo) & (freqs < hi)].sum()) / total, 4)
                   for name, lo, hi in bands}
    magnitude = np.sqrt(S)
    centroid = float(librosa.feature.spectral_centroid(S=magnitude, sr=sr).mean())
    rolloff = float(librosa.feature.spectral_rolloff(
        S=magnitude, sr=sr, roll_percent=0.85).mean())
    flatness = float(librosa.feature.spectral_flatness(S=magnitude).mean())
    zcr = float(librosa.feature.zero_crossing_rate(y).mean())

    # 动态
    seg_len = sr
    rms_db = [20 * np.log10(max(float(np.sqrt((y[i:i + seg_len] ** 2).mean())), 1e-6))
              for i in range(0, len(y) - seg_len, seg_len)]

    # 空间 + 响度(用原始采样率/立体声)
    y_st, sr_native = librosa.load(str(path), sr=None, mono=False)
    if y_st.ndim == 1:
        y_st = np.stack([y_st, y_st])
    mid, side = (y_st[0] + y_st[1]) / 2, (y_st[0] - y_st[1]) / 2
    width = float(np.sqrt((side ** 2).mean()) / max(float(np.sqrt((mid ** 2).mean())), 1e-9))
    try:
        lufs = float(pyloudnorm.Meter(sr_native).integrated_loudness(y_st.T))
    except Exception:  # noqa: BLE001
        lufs = float("nan")

    return {
        "file": path.name,
        "seconds": round(len(y) / sr, 1),
        "bpm": round(bpm_raw, 1),                       # 节拍网格(librosa)
        "bpm_from_interval": bpm_from_interval,          # 自洽校验
        "half_time": half_competitive,                   # 是否有半速感知
        "bpm_half": half,
        "bpm_options": {str(k): round(v, 4) for k, v in sorted(options.items())},
        "beats": int(len(beat_frames)),
        "beat_interval_median": round(median_interval, 3),
        "meter": meter,
        "meter_contrast": round(meter_contrast, 3),
        "groove": groove,
        "key": f"{best[0]} {best[1]}",
        "key_corr": round(best[2], 3),
        "band_ratios": band_ratios,
        "hf_ratio": band_ratios[">5k"],
        "centroid_hz": round(centroid),
        "rolloff85_hz": round(rolloff),
        "flatness": round(flatness, 5),
        "zcr": round(zcr, 4),
        "env_db": round(float(np.std(rms_db)) if rms_db else 0.0, 2),
        "lufs": round(lufs, 1) if lufs == lufs else None,
        "width": round(width, 3),
    }


# ------------------------------------------------------------------ 翻译
def _tempo_tags(bpm: float, label: str) -> list[tuple[str, str, bool]]:
    if bpm < 75:
        return [("calm", f"{label} {bpm} 很慢", True),
                ("ambient", f"{label} 慢速铺底", True)]
    if bpm < 105:
        return [("chillout", f"{label} {bpm} 中慢", True),
                ("dreamy", f"{label} 中慢速", True)]
    if bpm < 135:
        return [("groovy", f"{label} {bpm} 中快", True),
                ("danceable", f"{label} 可舞动", True)]
    return [("energetic", f"{label} {bpm} 快", True),
            ("upbeat", f"{label} 快速", True)]


def suggest(m: dict, vocab: set[str]) -> list[tuple[str, str, bool]]:
    """把数字翻成风格标签。返回 (标签, 依据, 是否在精选词表内)。"""
    out: list[tuple[str, str, bool]] = []

    # 速度:有倍频歧义时**两个档位都给**,由用户听决定,不由脚本替他决定
    out += _tempo_tags(m["bpm"], "声学脉冲")
    if m["half_time"]:
        out += _tempo_tags(m["bpm_half"], "半速感知")

    if m["width"] >= 0.35:
        out.append(("spacious", f"立体声宽度 {m['width']} 很宽", True))
        out.append(("atmospheric", "宽而散的空间感", True))
    elif m["width"] <= 0.18:
        out.append(("meditative", f"宽度仅 {m['width']},干而集中", True))

    if m["hf_ratio"] < 0.005:
        out.append(("relaxing", f">5kHz 能量仅占 {m['hf_ratio']*100:.2f}%(偏暗/柔)", True))
    elif m["hf_ratio"] > 0.02:
        out.append(("bright", f">5kHz 能量占 {m['hf_ratio']*100:.2f}% 偏亮", False))

    if m["env_db"] >= 7.0:
        out.append(("powerful", f"逐秒音量起伏 {m['env_db']} dB 很大", True))
        out.append(("intense", "动态强", True))
    elif m["env_db"] <= 2.5:
        out.append(("meditative", f"起伏仅 {m['env_db']} dB,平稳", True))

    if m["key_corr"] >= 0.5:
        if "小调" in m["key"]:
            out.append(("melancholic", f"{m['key']}(相关 {m['key_corr']}),弱推断", True))
        else:
            out.append(("uplifting", f"{m['key']}(相关 {m['key_corr']}),弱推断", True))

    if m["meter_contrast"] >= 0.6 and m["groove"]:
        out.append(("groovy", f"拍内强弱分明({m['groove']})", True))

    seen, uniq = set(), []
    for tag, why, in_vocab in out:
        if tag not in seen:
            seen.add(tag)
            uniq.append((tag, why, in_vocab or tag in vocab))
    return uniq


# ------------------------------------------------------------------ 输出
def report(m: dict, tags: list[tuple[str, str, bool]]) -> None:
    print(f"\n{'═' * 78}")
    print(f"  {m['file']}   {m['seconds']} 秒")
    print(f"{'═' * 78}")
    print(f"  速度    BPM {m['bpm']}(节拍网格;按拍点间隔反算 {m['bpm_from_interval']}"
          f" → {'自洽' if abs(m['bpm'] - m['bpm_from_interval']) <= 1.5 else '不自洽!'})")
    print(f"          拍点 {m['beats']} 个,间隔中位数 {m['beat_interval_median']}s"
          f"   倍频候选 {m['bpm_options']}")
    if m["half_time"]:
        print(f"          ⚠ 自相关在 {m['bpm_half']} 更强 → 可能按半速感知(两者都已给标签)")
    print(f"  律动    {m['meter']}/4(强弱对比 {m['meter_contrast']})"
          + (f"   小节内强弱型 {m['groove']}" if m["groove"] else ""))
    print(f"  调性    {m['key']}(模板相关 {m['key_corr']})")
    print(f"  频谱    谱质心 {m['centroid_hz']} Hz  85%滚降 {m['rolloff85_hz']} Hz"
          f"  平坦度 {m['flatness']}  过零率 {m['zcr']}")
    print(f"          能量分布 " + "  ".join(f"{k} {v*100:.1f}%" for k, v in m["band_ratios"].items()))
    print(f"  动态    音量起伏 {m['env_db']} dB   LUFS {m['lufs']}")
    print(f"  空间    立体声宽度 {m['width']}")
    print("  建议标签(整轨特征,不是人声特征):")
    for tag, why, in_vocab in tags:
        mark = "词表内" if in_vocab else "词表外·需手打"
        print(f"      · {tag:14s} [{mark}]  {why}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+")
    ap.add_argument("--json", help="把全部结果写成一个 JSON")
    args = ap.parse_args()

    vocab = load_vocab()
    results = []
    for name in args.files:
        path = Path(name)
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.exists():
            print(f"找不到 {path}", file=sys.stderr)
            continue
        m = measure(path)
        tags = suggest(m, vocab)
        report(m, tags)
        results.append({**m, "suggested_tags": [
            {"tag": t, "reason": w, "in_vocab": iv} for t, w, iv in tags]})

    if args.json and results:
        out = Path(args.json)
        if not out.is_absolute():
            out = Path.cwd() / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nJSON: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
