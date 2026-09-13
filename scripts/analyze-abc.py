"""统计 ABC 乐谱各声部的音符 / 休止符比例。

用途:客观判断一次生成到底有没有人声。
YuE2 的符号规划(planning)会在乐谱里同时写出 Vocal 与 Ins 两个声部;
如果 Vocal 声部几乎全是休止符(z / Z),说明模型规划的就是纯器乐。
这比"听一下"更可复现,也比跑 ASR 便宜得多。

用法:
    python scripts\\analyze-abc.py <file.abc> [more.abc ...]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# ABC 头部字段(不是音乐内容)
HEADER_KEYS = ("X:", "T:", "M:", "L:", "Q:", "K:", "C:", "O:", "R:", "P:", "W:", "Z:", "N:", "S:")
NOTE_RE = re.compile(r"[\^_=]?[A-Ga-g][,']*\d*")
REST_RE = re.compile(r"[zZxX]\d*")
CHORD_RE = re.compile(r'"[^"]*"')
VOICE_RE = re.compile(r"^V:\s*([^\s]+)\s*(.*)$")


def analyze(path: Path) -> tuple[dict[str, dict[str, int]], str]:
    voices: dict[str, dict[str, int]] = {}
    current: str | None = None
    keys: list[str] = []

    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("%"):
            continue
        if any(line.startswith(k) for k in HEADER_KEYS):
            if line.startswith("K:"):
                keys.append(line[2:].strip())
            continue

        match = VOICE_RE.match(line)
        if match:
            current = match.group(1)
            voices.setdefault(current, {"notes": 0, "rests": 0, "bars": 0})
            tail = match.group(2).strip()
            # A voice declaration carries header params (clef=, name=, snm=);
            # anything else on the line is music.
            if not tail or "=" in tail:
                continue
            line = tail

        if current is None:
            continue

        body = CHORD_RE.sub("", line)
        voices[current]["bars"] += body.count("|")
        voices[current]["rests"] += len(REST_RE.findall(body))
        voices[current]["notes"] += len(NOTE_RE.findall(body))

    return voices, ",".join(keys)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2

    worst = 0
    for name in argv[1:]:
        path = Path(name)
        if not path.exists():
            print(f"MISSING {path}")
            worst = 1
            continue
        voices, key = analyze(path)
        print(f"\n{path.name}   (K:{key or '?'})")
        if not voices:
            print("  解析不到声部 —— 可能不是 ABC 格式")
            worst = 1
            continue
        for voice, stat in voices.items():
            total = stat["notes"] + stat["rests"]
            sung = stat["notes"] / total * 100 if total else 0.0
            verdict = ""
            if voice.lower().startswith("voc"):
                verdict = "  <== 人声声部" + ("(几乎全休止 → 纯器乐)" if sung < 5 else "(有实际音符)")
                if sung < 5:
                    pass
            print(f"  {voice:<8} 音符 {stat['notes']:>5}  休止 {stat['rests']:>5}  "
                  f"小节 {stat['bars']:>4}  音符占比 {sung:5.1f}%{verdict}")

    return worst


if __name__ == "__main__":
    sys.exit(main(sys.argv))
