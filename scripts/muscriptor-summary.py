#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 MuScriptor 转出的音符事件变成人能读的节奏报告。

MuScriptor 输出的是扁平事件流(start/end 各一条,带 instrument / pitch / start_time)。
直接看是一堆 JSON,这个脚本负责:

  * 各乐器的音符数与音域
  * 从鼓点间隔推出速度(并给出半速读数)
  * **按小节折叠出真实鼓型**(按 GM 音高分成 底鼓/军鼓/踩镲/吊镲)
  * 各乐器的进入时间轴(谁在什么时候进来)

为什么要折叠而不是"全曲平均":整首歌的十六分音符铺满时,把所有鼓点丢进
16 个格子求平均必然得到一条平线,什么也看不出来。必须先按速度量化到格,
**再按小节折叠**,才能看出"第几拍有底鼓"。

用法:
    python scripts\\muscriptor-summary.py output\\zh-01.notes.json
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

import numpy as np

# General MIDI 打击乐音高 -> 名称
_DRUM_NAMES = {
    35: "底鼓", 36: "底鼓", 38: "军鼓", 40: "军鼓", 37: "边击", 39: "拍手",
    42: "闭镲", 44: "踏镲", 46: "开镲", 49: "吊镲", 57: "吊镲", 55: "吊镲",
    51: "叮叮镲", 53: "叮叮镲", 59: "叮叮镲",
}
_DRUM_ORDER = ["底鼓", "军鼓", "边击", "拍手", "闭镲", "踏镲", "开镲", "叮叮镲", "吊镲"]
_GLYPH = "·▁▃▅█"


def load_events(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):                     # 容错:有的版本包一层
        data = data.get("events") or data.get("notes") or []
    return [e for e in data if isinstance(e, dict)]


def estimate_beat(modal: float) -> tuple[float, int]:
    """众数间隔既可能是拍、八分或十六分。返回 (拍长秒, 倍数 k)。

    判据:真实速度绝大多数落在 70–165 BPM,先按这个窗口筛掉不可能的读数,
    再在剩下的里取最接近 110 BPM 的(流行音乐的速度中心)。
    这只是启发式 —— 有真值时用 --bpm 覆盖。
    """
    candidates = []
    for k in (1, 2, 4):
        beat = modal * k
        bpm = 60.0 / beat
        if 70.0 <= bpm <= 165.0:
            candidates.append((abs(bpm - 110.0), beat, k))
    if not candidates:
        return modal, 1
    _d, beat, k = min(candidates)
    return beat, k


def fold_pattern(times: np.ndarray, bpm: float, steps: int = 16) -> np.ndarray:
    """把鼓点按 bpm 量化到 steps 格并折叠,返回每个格子的命中次数。"""
    bar = 60.0 / bpm * 4.0
    cells = np.zeros(steps)
    t0 = times[0]
    for t in times:
        cells[int(round((t - t0) / (bar / steps))) % steps] += 1
    return cells


def refine_bpm(times: np.ndarray, bpm0: float, span: float = 0.15) -> tuple[float, float]:
    """在启发式估计附近搜速度,取"折叠后图案最锐利"的那个。

    为什么必须做:折叠对速度极其敏感 —— 115.4 与 117.5 只差 2%,但 23 小节累积
    下来相位就漂了,反拍军鼓会被抹成一片。锐利度用格子计数的集中度衡量
    (Σn²/(Σn)²:全部集中在一格时最大)。
    """
    best_bpm, best_score = bpm0, -1.0
    for bpm in np.arange(bpm0 * (1 - span), bpm0 * (1 + span), 0.05):
        cells = fold_pattern(times, bpm)
        total = cells.sum()
        if total <= 0:
            continue
        score = float((cells ** 2).sum()) / (total ** 2)
        if score > best_score:
            best_bpm, best_score = float(bpm), score
    return best_bpm, best_score


def summarize(path: Path, bpm_override: float | None = None) -> dict:
    """算出全部结构,供打印或 JSON 输出(--json 给服务端消费)。"""
    events = load_events(path)
    starts = [e for e in events if e.get("type") == "start"]
    if not starts:
        raise ValueError("没有 start 事件")

    by_inst: dict[str, list[float]] = collections.defaultdict(list)
    pitches: dict[str, list[int]] = collections.defaultdict(list)
    for e in starts:
        inst = e.get("instrument") or "(未标注)"
        by_inst[inst].append(float(e.get("start_time") or 0.0))
        pitches[inst].append(int(e.get("pitch") or 0))

    out: dict = {
        "file": path.name,
        "events": {"total": len(events), "start": len(starts),
                   "end": len(events) - len(starts)},
        "instruments": [
            {"name": inst, "notes": len(pitches[inst]),
             "pitch_min": min(pitches[inst]), "pitch_max": max(pitches[inst]),
             "first": round(min(by_inst[inst]), 2), "last": round(max(by_inst[inst]), 2)}
            for inst, _ in sorted(by_inst.items(), key=lambda kv: -len(kv[1]))
        ],
        "tempo": None, "drum_pattern": None, "timeline": None,
    }

    # ---- 速度:由鼓点间隔推
    drums = sorted(zip(by_inst.get("drums", []), pitches.get("drums", [])))
    if len(drums) < 8:
        return out

    times = np.array([t for t, _ in drums])
    iv = np.diff(times)
    iv = iv[(iv > 0.04) & (iv < 2.0)]
    vals, cnt = np.unique(np.round(iv, 3), return_counts=True)
    modal = float(vals[cnt.argmax()])
    top = sorted(zip(cnt, vals), reverse=True)[:5]

    if bpm_override:
        bpm_use, source, refined, score = float(bpm_override), "user", False, 0.0
    else:
        beat0, k = estimate_beat(modal)
        bpm_use, score = refine_bpm(times, 60.0 / beat0)
        source, refined = "heuristic", True
    beat = 60.0 / bpm_use

    out["tempo"] = {
        "drum_hits": int(len(times)),
        "first": round(float(times[0]), 2), "last": round(float(times[-1]), 2),
        "modal_interval": round(modal, 3),
        "top_intervals": [[round(float(v), 3), int(c)] for c, v in top],
        "readings": {"一拍": round(60.0 / modal, 1),
                     "八分": round(60.0 / (modal * 2), 1),
                     "十六分": round(60.0 / (modal * 4), 1)},
        "bpm": round(bpm_use, 1), "source": source,
        "refined": refined, "concentration": round(float(score), 4),
    }

    # ---- 鼓型:量化到 16 分格,按小节折叠
    bar = beat * 4
    steps = 16
    t0 = times[0]
    grid: dict[str, np.ndarray] = collections.defaultdict(lambda: np.zeros(steps))
    for t, pitch in drums:
        name = _DRUM_NAMES.get(pitch, f"其他({pitch})")
        pos = int(round((t - t0) / (bar / steps))) % steps
        grid[name][pos] += 1
    out["drum_pattern"] = {
        "bar_seconds": round(bar, 3),
        "bars": max(1, int((times[-1] - t0) / bar)),
        "grid": {name: [int(v) for v in grid[name]]
                 for name in _DRUM_ORDER if name in grid},
    }

    # ---- 进入时间轴(哪个乐器在什么时候出现)
    buckets = 8
    span = max(max(v) for v in by_inst.values()) or 1
    n_b = max(1, int(span / buckets) + 1)
    out["timeline"] = {"bucket_seconds": buckets, "rows": {}}
    for inst, _ in sorted(by_inst.items(), key=lambda kv: -len(kv[1])):
        occ = np.zeros(n_b)
        for t in by_inst[inst]:
            occ[min(int(t / buckets), n_b - 1)] += 1
        out["timeline"]["rows"][inst] = [int(v) for v in occ]
    return out


def _glyphs(values, peak: float | None = None) -> str:
    top = peak or (max(values) if values else 1) or 1
    return "".join(_GLYPH[min(int(v / top * 4.999), 4)] if v else "·" for v in values)


def report(data: dict) -> None:
    print(f"文件     {data['file']}")
    ev = data["events"]
    print(f"事件总数 {ev['total']}(start {ev['start']} / end {ev['end']})")

    print(f"\n{'乐器':22s} {'音符':>6s} {'音域':>12s} {'首次进入':>10s} {'末次':>9s}")
    for i in data["instruments"]:
        print(f"  {i['name']:20s} {i['notes']:6d} {i['pitch_min']:5d}–{i['pitch_max']:<5d} "
              f"{i['first']:9.1f}s {i['last']:8.1f}s")

    t = data["tempo"]
    if not t:
        print("\n鼓点太少,跳过节奏分析")
        return

    print(f"\n{'─' * 74}\n节奏")
    print(f"  鼓点 {t['drum_hits']} 个,{t['first']:.2f}s – {t['last']:.2f}s")
    print("  最常见间隔: " + ", ".join(f"{v:.3f}s×{c}" for v, c in t["top_intervals"]))
    print(f"  众数间隔 {t['modal_interval']:.3f}s")
    for label, bpm in t["readings"].items():
        print(f"    若众数={label:4s} -> {bpm:6.1f} BPM")
    if t["source"] == "user":
        print(f"  采用:用户指定 --bpm {t['bpm']:g}(拍长 {60 / t['bpm']:.3f}s)")
    else:
        print(f"  采用 + 细化 → {t['bpm']:.1f} BPM(图案集中度 {t['concentration']:.4f})")
        print("        判据是「真实速度多在 70–165 BPM」的启发式;有真值请用 --bpm 覆盖")

    d = data["drum_pattern"]
    print(f"\n  鼓型(折叠周期 = 一小节 {d['bar_seconds']:.2f}s,约 {d['bars']} 小节)")
    print(f"    {'':8s}1 e & a 2 e & a 3 e & a 4 e & a")
    for name, row in d["grid"].items():
        print(f"    {name:8s}{_glyphs(row)}   ({sum(row)} 次)")

    print("\n  进入时间轴(每 8 秒一格,█=有音符)")
    for inst, row in data["timeline"]["rows"].items():
        print(f"    {inst:20s}{_glyphs(row)}")
    n_b = len(next(iter(data["timeline"]["rows"].values())))
    print(f"    {'':20s}" + "".join(f"{int(i * 8):<4d}" for i in range(0, n_b, 2)))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("events_json")
    ap.add_argument("--bpm", type=float, default=None,
                    help="直接指定速度,跳过众数间隔的倍频推断")
    ap.add_argument("--json", help="把结构化结果写到这里(服务端用它,不解析文本)")
    args = ap.parse_args()

    path = Path(args.events_json)
    if not path.is_absolute():
        path = Path.cwd() / path
    if not path.exists():
        print(f"找不到 {path}", file=sys.stderr)
        return 1

    data = summarize(path, args.bpm)
    if args.json:
        out = Path(args.json)
        if not out.is_absolute():
            out = Path.cwd() / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    report(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
