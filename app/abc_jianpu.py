#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ABC 记谱法 -> 简谱。

模型(cot=melody / full)导出的是 ABC:给机器读的,`V: Vocal` / `A2A2B2d2` / `"G"z8`。
这个模块把它翻成人能读的简谱:调号换算(1=D)、八度点、减时线、附点、休止 0、
小节线、和弦标记、段落名,并把各声部按小节对齐。

两种输出:
    to_html(abc)  -> 真正的简谱外观(八度点/减时线用 CSS 画),给界面用
    to_text(abc)  -> 纯文本版,给 .txt 导出用

纯文本的记号约定(HTML 版不需要,它画的是真记号):
    '  高八度(可多个)      ,  低八度(可多个)
    _  八分音符(1 条减时线) __ 十六分音符(2 条)
    .  附点                -  延长一拍       0  休止符
    |  小节线              [G] 和弦标记      #4/b7 变化音
"""
from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass, field
from typing import Optional

_LETTER_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_LETTERS = "CDEFGAB"
_MAJOR = (0, 2, 4, 5, 7, 9, 11)
_MINOR = (0, 2, 3, 5, 7, 8, 10)

# K: 不只是写个调名 —— ABC 里它自带调号。K:D 表示 F 与 C 全升,不处理就会满屏 b3/b7。
# 大调主音音级 -> 升号数(正)/降号数(负);小调换算成关系大调后再查。
_MAJOR_SIG = {0: 0, 7: 1, 2: 2, 9: 3, 4: 4, 11: 5, 6: 6,
              5: -1, 10: -2, 3: -3, 8: -4, 1: -5}
_SHARP_ORDER = "FCGDAEB"
_FLAT_ORDER = "BEADGCF"

# 段落名翻成中文(模型只会写这几个)
_SECTION_ZH = {
    "intro": "前奏", "verse": "主歌", "chorus": "副歌", "bridge": "桥段",
    "outro": "尾奏", "inst": "间奏", "instrumental": "间奏", "solo": "独奏",
    "pre-chorus": "预副歌", "prechorus": "预副歌", "hook": "记忆点",
}

# 时值 -> (减时线条数, 是否有附点)
_DUR_TABLE = {0.25: (2, False), 0.375: (2, True), 0.5: (1, False),
              0.75: (1, True), 1.0: (0, False), 1.5: (0, True)}


# ------------------------------------------------------------------ 数据模型
@dataclass
class JNote:
    """一个简谱音。degree=0 表示休止符。"""
    degree: int = 0
    accidental: int = 0      # -1 降 / 0 / +1 升
    octave: int = 0          # 相对主音:正数=高八度点数,负数=低八度点数
    beats: float = 1.0       # 以四分音符为 1 拍


@dataclass
class Event:
    notes: list[JNote] = field(default_factory=list)
    symbol: str = ""         # 和弦标记,如 "G"


@dataclass
class Bar:
    events: list[Event] = field(default_factory=list)


@dataclass
class Voice:
    name: str
    bars: list[Bar] = field(default_factory=list)


@dataclass
class Score:
    title: str = ""
    meter: tuple[int, int] = (4, 4)
    tempo: str = ""
    key_display: str = "1=C"
    is_minor: bool = False
    voices: list[Voice] = field(default_factory=list)
    sections: dict[int, str] = field(default_factory=dict)   # 小节序号(0 基) -> 段落名
    warnings: list[str] = field(default_factory=list)

    @property
    def bar_count(self) -> int:
        return max((len(v.bars) for v in self.voices), default=0)


@dataclass
class _Ctx:
    tonic_pc: int
    tonic_index: int
    tonic_octave: int
    scale: tuple
    unit_beats: float
    beats_per_bar: float
    key_acc: dict = field(default_factory=dict)   # 调号:音名 -> +/-1


# ------------------------------------------------------------------ 解析
def _multiplier(spec: str) -> float:
    if not spec:
        return 1.0
    if set(spec) == {"/"}:
        return 0.5 ** len(spec)
    if "/" in spec:
        a, _, b = spec.partition("/")
        return (float(a) if a else 1.0) / (float(b) if b else 2.0)
    return float(spec)


def _read_length(line: str, i: int) -> tuple[str, int]:
    n, start = len(line), i
    while i < n and (line[i].isdigit() or line[i] == "/"):
        i += 1
    return line[start:i], i


def _make_note(letter: str, acc: int, octave: int, ctx: _Ctx) -> JNote:
    li = _LETTERS.index(letter)
    step = (li - ctx.tonic_index) % 7
    pc = (_LETTER_PC[letter] + acc) % 12
    expected = (ctx.tonic_pc + ctx.scale[step]) % 12
    diff = (pc - expected + 6) % 12 - 6
    dots = octave - ctx.tonic_octave - (1 if li < ctx.tonic_index else 0)
    return JNote(degree=step + 1, accidental=diff, octave=dots)


def _parse_note(line: str, i: int, ctx: _Ctx) -> tuple[JNote, int]:
    n = len(line)
    acc: Optional[int] = None                         # None = 没写,用调号
    if line[i] in "^_=":
        acc = {"^": 1, "_": -1, "=": 0}[line[i]]
        i += 1
        if i < n and line[i] in "^_":                 # ^^ 双升 / __ 双降
            acc += {"^": 1, "_": -1}[line[i]]
            i += 1
    letter = line[i]
    i += 1
    octave = 4 if letter.isupper() else 5
    while i < n and line[i] in "',":
        octave += 1 if line[i] == "'" else -1
        i += 1
    spec, i = _read_length(line, i)
    if acc is None:                                   # 调号里的升降号
        acc = ctx.key_acc.get(letter.upper(), 0)
    note = _make_note(letter.upper(), acc, octave, ctx)
    note.beats = _multiplier(spec) * ctx.unit_beats
    return note, i


def _parse_key(value: str) -> tuple[int, int, tuple, bool, str, dict]:
    m = re.match(r"^\s*([A-Ga-g])([#b]?)\s*(.*)$", value)
    if not m:
        return 0, 0, _MAJOR, False, "1=C", {}
    letter = m.group(1).upper()
    acc = 1 if "#" in m.group(2) else (-1 if "b" in m.group(2) else 0)
    mode = m.group(3).strip().lower()
    is_minor = mode.startswith("min") or (mode.startswith("m") and not mode.startswith("maj"))
    pc = (_LETTER_PC[letter] + acc) % 12
    accidental_text = {1: "#", -1: "b", 0: ""}[acc]
    display = f"1={letter}{accidental_text}" + ("(小调)" if is_minor else "")

    major_pc = (pc + 3) % 12 if is_minor else pc      # 小调 -> 关系大调
    count = _MAJOR_SIG.get(major_pc, 0)
    order = _SHARP_ORDER if count > 0 else _FLAT_ORDER
    key_acc = {name: (1 if count > 0 else -1) for name in order[:abs(count)]}
    return pc, _LETTERS.index(letter), (_MINOR if is_minor else _MAJOR), is_minor, display, key_acc


def parse_abc(text: str) -> Score:
    """单遍扫描:头信息、声部切换、段落标记、音符流一次读完。"""
    score = Score()
    ctx = _Ctx(tonic_pc=0, tonic_index=0, tonic_octave=4, scale=_MAJOR,
               unit_beats=1.0, beats_per_bar=4.0)
    voices: dict[str, Voice] = {}
    current: Voice | None = None
    pending: list[Event] = []
    key_seen = False

    def flush() -> None:
        nonlocal pending
        if pending and current is not None:
            current.bars.append(Bar(pending))
        pending = []

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue

        if line.startswith("%"):
            label = line[1:].strip()
            if label:
                index = max((len(v.bars) for v in voices.values()), default=0)
                score.sections.setdefault(index, label)
            continue

        m = re.match(r"^([A-Za-z]):\s*(.*)$", line)
        if m and not line[0].isdigit():
            key, value = m.group(1).upper(), m.group(2).strip()
            if key == "T":
                score.title = value
            elif key == "M":
                mm = re.match(r"^(\d+)\s*/\s*(\d+)$", value)
                if mm:
                    score.meter = (int(mm.group(1)), int(mm.group(2)))
                    ctx.beats_per_bar = score.meter[0] * 4.0 / score.meter[1]
            elif key == "L":
                mm = re.match(r"^(\d+)\s*/\s*(\d+)$", value)
                if mm:
                    ctx.unit_beats = 4.0 * int(mm.group(1)) / int(mm.group(2))
            elif key == "Q":
                score.tempo = value
            elif key == "K":
                if key_seen:
                    score.warnings.append(f"忽略中途转调:{value}")
                else:
                    pc, idx, scale, minor, display, key_acc = _parse_key(value)
                    ctx.tonic_pc, ctx.tonic_index, ctx.scale = pc, idx, scale
                    ctx.key_acc = key_acc
                    score.key_display, score.is_minor = display, minor
                    key_seen = True
            elif key == "V":
                flush()
                name = value.split()[0] if value.split() else "旋律"
                current = voices.setdefault(name, Voice(name=name))
            continue

        # 正文行:先切掉行内注释
        body = line.split("%", 1)[0]
        if current is None:                                # 没有 V: 行的单声部谱
            current = voices.setdefault("旋律", Voice(name="旋律"))

        i, n, symbol = 0, len(body), ""
        while i < n:
            c = body[i]
            if c.isspace():
                i += 1
                continue
            if c == '"':                                   # "G" 和弦标记
                j = body.find('"', i + 1)
                if j < 0:
                    break
                symbol = body[i + 1:j]
                i = j + 1
                continue
            if c == "|":                                   # 小节线
                if pending:
                    current.bars.append(Bar(pending))
                    pending = []
                i += 1
                continue
            if c in "Zz":                                  # 休止
                i += 1
                spec, i = _read_length(body, i)
                if c == "Z":                               # 整小节休止,数字=小节数
                    flush()
                    count = int(float(spec)) if spec else 1
                    for _ in range(count):
                        current.bars.append(Bar([Event(
                            notes=[JNote(degree=0, beats=ctx.beats_per_bar)])]))
                else:
                    beats = _multiplier(spec) * ctx.unit_beats
                    pending.append(Event(notes=[JNote(degree=0, beats=beats)],
                                         symbol=symbol))
                    symbol = ""
                continue
            if c == "[":                                   # 和弦(同时发声)
                j = body.find("]", i)
                if j < 0:
                    break
                inner, i = body[i + 1:j], j + 1
                notes: list[JNote] = []
                k = 0
                while k < len(inner):
                    if inner[k].isspace() or inner[k] in ".,":
                        k += 1
                        continue
                    if inner[k] in "ABCDEFGabcdefg" or (
                            inner[k] in "^_=" and k + 1 < len(inner)
                            and inner[k + 1] in "ABCDEFGabcdefg"):
                        note, k = _parse_note(inner, k, ctx)
                        notes.append(note)
                    else:
                        k += 1
                if notes:
                    base = notes[0].beats
                    if any(abs(x.beats - base) > 1e-6 for x in notes):
                        score.warnings.append(
                            f"和弦内时值不一致:{inner}(按第一个音 {base:g} 拍处理)")
                    pending.append(Event(notes=notes, symbol=symbol))
                    symbol = ""
                continue
            if c in "ABCDEFGabcdefg" or (
                    c in "^_=" and i + 1 < n and body[i + 1] in "ABCDEFGabcdefg"):
                note, i = _parse_note(body, i, ctx)
                pending.append(Event(notes=[note], symbol=symbol))
                symbol = ""
                continue
            i += 1                                         # 连音线/装饰音/分句等:简谱不表达

    flush()
    score.voices = list(voices.values())

    counts = {v.name: len(v.bars) for v in score.voices}
    if len(set(counts.values())) > 1:
        score.warnings.append("各声部小节数不一致:" +
                              ",".join(f"{k}={v}" for k, v in counts.items()))
        for v in score.voices:                             # 补齐,便于并排对齐
            while len(v.bars) < score.bar_count:
                v.bars.append(Bar())
    return score


# ------------------------------------------------------------------ 时值
def _dur_shape(beats: float) -> tuple[int, bool, int]:
    """-> (减时线条数, 是否附点, 延长线数量)"""
    b = round(beats, 4)
    if b in _DUR_TABLE:
        ul, dot = _DUR_TABLE[b]
        return ul, dot, 0
    if b >= 2:
        return 0, bool(b % 1), max(int(b // 1) - 1, 0)
    for candidate in (0.75, 0.5, 0.375, 0.25):
        if b > candidate:
            ul, dot = _DUR_TABLE[candidate]
            return ul, dot, 0
    return 2, False, 0


def _plain_note(note: JNote) -> str:
    ul, dot, dashes = _dur_shape(note.beats)
    if note.degree == 0:
        head = "0"
    else:
        acc = "#" if note.accidental > 0 else ("b" if note.accidental < 0 else "")
        head = acc + str(note.degree)
        head += "'" * max(note.octave, 0) + "," * max(-note.octave, 0)
    head = "_" * ul + head + ("." if dot else "")
    return " ".join([head] + ["-"] * dashes)


def _plain_event(ev: Event) -> str:
    body = ("[" + " ".join(_plain_note(x) for x in ev.notes) + "]"
            if len(ev.notes) > 1 else _plain_note(ev.notes[0]))
    return f"[{ev.symbol}]{body}" if ev.symbol else body


# ------------------------------------------------------------------ HTML
_CSS = """
.jpscore { border-collapse: collapse; margin: 4px 0 10px; font-family: "Microsoft YaHei", system-ui, sans-serif; }
.jpscore caption { text-align: left; font-size: 13px; color: #2b5aa8; background: #eef4ff;
                   padding: 4px 8px; border-radius: 4px; margin-bottom: 4px; }
.jpscore th { font-size: 12px; font-weight: 600; color: #555; background: #fafafa;
              padding: 3px 8px; text-align: right; border-right: 1px solid #e0e0e0; white-space: nowrap; }
.jpscore td { padding: 8px 10px; border-left: 1px solid #e6e6e6; vertical-align: middle; white-space: nowrap; }
.jpscore tr:nth-child(even) td { background: #fcfcfc; }
.jpscore .n { position: relative; display: inline-block; padding: 10px 5px 9px;
              font-size: 17px; line-height: 1; color: inherit; }
.jpscore .n .dg { display: inline-block; padding: 0 1px; }
.jpscore .n .ul { display: inline-block; border-bottom: 1.4px solid currentColor; padding-bottom: 1px; }
.jpscore .n .od { position: absolute; left: 0; right: 0; text-align: center;
                  font-size: 15px; line-height: 0.5; font-style: normal; }
.jpscore .n .od.u { top: 2px; }
.jpscore .n .od.d { bottom: 0; }
.jpscore .n .dt { position: absolute; right: -3px; top: 50%; margin-top: -4px; font-style: normal; }
.jpscore .dash { display: inline-block; padding: 0 4px; }
.jpscore .cs { font-size: 11px; color: #2b5aa8; margin-right: 1px; }
.jpscore .chord { display: inline-block; vertical-align: middle; text-align: center; }
.jpmeta { font-family: "Microsoft YaHei", system-ui, sans-serif; font-size: 13px; color: #555; margin: 2px 0 6px; }
.jpmeta code { background: #f2f2f2; padding: 1px 5px; border-radius: 4px; }
.jpwrap { overflow-x: auto; }
"""


def _note_html(note: JNote) -> str:
    ul, dot, dashes = _dur_shape(note.beats)
    if note.degree == 0:
        core, up, down = "0", "", ""
    else:
        acc = "#" if note.accidental > 0 else ("b" if note.accidental < 0 else "")
        core = f'<span class="dg">{acc}{note.degree}</span>'
        up = '<i class="od u">·</i>' * max(note.octave, 0)
        down = '<i class="od d">·</i>' * max(-note.octave, 0)
    for _ in range(ul):
        core = f'<span class="ul">{core}</span>'
    tail = '<span class="dash">–</span>' * dashes
    dot_html = '<i class="dt">·</i>' if dot else ''
    return f'<span class="n">{up}{core}{dot_html}{down}</span>{tail}'


def _event_html(ev: Event) -> str:
    sym = f'<sup class="cs">{_html.escape(ev.symbol)}</sup>' if ev.symbol else ""
    if len(ev.notes) > 1:
        stacked = "<br>".join(_note_html(x) for x in ev.notes)
        return f'{sym}<span class="chord">{stacked}</span>'
    return sym + _note_html(ev.notes[0])


def _segments(score: Score) -> list[tuple[int, int, str]]:
    """按段落把小节切成 [(起, 止, 段落名)]。"""
    total = score.bar_count
    bounds = sorted(b for b in score.sections if 0 <= b < total)
    if not bounds or bounds[0] != 0:
        bounds = [0] + bounds
    out = []
    for k, start in enumerate(bounds):
        end = bounds[k + 1] if k + 1 < len(bounds) else total
        out.append((start, end, score.sections.get(start, "")))
    return out


def _zh_section(label: str) -> str:
    if not label:
        return "全曲"
    return _SECTION_ZH.get(label.lower(), label)


def to_html(text: str, *, limit_bars: int = 0) -> str:
    """ABC -> 简谱 HTML。各声部按小节并排对齐,段落分隔。"""
    score = parse_abc(text or "")
    if not score.voices or score.bar_count == 0:
        return ""

    meter = f"{score.meter[0]}/{score.meter[1]}"
    parts = [f"<style>{_CSS}</style>",
             f'<div class="jpwrap"><div class="jpmeta">'
             f'简谱:<code>{_html.escape(score.key_display)}</code> '
             f'<code>{meter}</code>'
             + (f' <code>{_html.escape(score.tempo)}</code>' if score.tempo else "")
             + f' · 共 {score.bar_count} 小节 · '
             + " / ".join(_html.escape(v.name) for v in score.voices)
             + "</div>"]

    for start, end, label in _segments(score):
        if limit_bars and start >= limit_bars:
            break
        stop = min(end, limit_bars) if limit_bars else end
        caption = f"{_zh_section(label)} · 第 {start + 1}–{stop} 小节"
        head = "".join(f"<th>{b + 1}</th>" for b in range(start, stop))
        rows = []
        for voice in score.voices:
            cells = "".join(
                "<td>" + "".join(_event_html(e) for e in voice.bars[b].events) + "</td>"
                if b < len(voice.bars) else "<td></td>"
                for b in range(start, stop))
            rows.append(f'<tr><th>{_html.escape(voice.name)}</th>{cells}</tr>')
        parts.append(f'<table class="jpscore"><caption>{_html.escape(caption)}</caption>'
                     f'<tr><th>小节</th>{head}</tr>{"".join(rows)}</table>')

    if score.warnings:
        parts.append('<div class="jpmeta">⚠ '
                     + _html.escape(";".join(dict.fromkeys(score.warnings)))
                     + "</div>")
    parts.append("</div>")
    return "".join(parts)


def to_text(text: str, *, title: str = "") -> str:
    """ABC -> 纯文本简谱(记号约定见模块开头)。"""
    score = parse_abc(text or "")
    if not score.voices or score.bar_count == 0:
        return ""

    out = []
    if title or score.title:
        out.append(f"# {title or score.title}")
    meter = f"{score.meter[0]}/{score.meter[1]}"
    out.append(f"# 调号 {score.key_display}  拍号 {meter}"
               + (f"  速度 {score.tempo}" if score.tempo else "")
               + f"  共 {score.bar_count} 小节")
    out.append("# 记号: ' 高八度, 低八度 | _ 八分(1条减时线) __ 十六分(2条) | . 附点 | "
               "- 延长一拍 | 0 休止 | | 小节线 | [G] 和弦标记 | #4/b7 变化音")
    out.append("")
    for start, end, label in _segments(score):
        out.append(f"【{_zh_section(label)}】第 {start + 1}–{end} 小节")
        for voice in score.voices:
            bars = []
            for b in range(start, min(end, len(voice.bars))):
                bars.append(" ".join(_plain_event(e) for e in voice.bars[b].events)
                            or " ")
            out.append(f"  {voice.name}: " + " | ".join(bars) + " |")
        out.append("")
    if score.warnings:
        out.append("⚠ " + ";".join(dict.fromkeys(score.warnings)))
    return "\n".join(out)


def has_notation(text: str) -> bool:
    """这段 ABC 里到底有没有音符(空谱不要显示)。"""
    return bool(text and text.strip()) and any(
        re.search(r"[A-Ga-gzZ]", ln) for ln in text.splitlines()
        if not re.match(r"^\s*[A-Za-z]:", ln) and not ln.strip().startswith("%"))


if __name__ == "__main__":       # 手工快速看一眼
    import sys
    abc = open(sys.argv[1], encoding="utf-8").read()
    print(to_text(abc, title=sys.argv[1]))
