#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 ABC 乐谱转成简谱(.txt 导出 / 批量检查)。

用法:
    python scripts\\abc-to-jianpu.py output\\score-full.abc     # 转一个,写到同目录 .jianpu.txt
    python scripts\\abc-to-jianpu.py --all                      # 把 output\\ 下所有 .abc 都转一遍
    python scripts\\abc-to-jianpu.py --check                    # 只解析不写文件,检查有没有解析失败
    python scripts\\abc-to-jianpu.py output\\score-full.abc --html out.html   # 另存一份 HTML 简谱
"""
from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import abc_jianpu as J  # noqa: E402


def convert_one(abc_path: Path, want_html: bool = False) -> Path | None:
    text = abc_path.read_text(encoding="utf-8", errors="replace")
    if not J.has_notation(text):
        print(f"  跳过(没有音符):{abc_path.name}")
        return None
    out = abc_path.with_suffix(".jianpu.txt")
    out.write_text(J.to_text(text, title=abc_path.stem), encoding="utf-8")
    extra = ""
    if want_html:
        page = abc_path.with_suffix(".jianpu.html")
        page.write_text(
            "<!doctype html><meta charset='utf-8'>"
            f"<title>简谱 · {abc_path.name}</title>"
            "<body style='padding:20px;background:#fff'>"
            f"{J.to_html(text)}</body>", encoding="utf-8")
        extra = f" + {page.name}"
    print(f"  {abc_path.name} -> {out.name}{extra}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("abc", nargs="?", help="单个 .abc 文件")
    ap.add_argument("--all", action="store_true", help="转换 output/ 下全部 .abc")
    ap.add_argument("--check", action="store_true", help="只检查解析,不写文件")
    ap.add_argument("--html", action="store_true", help="同时输出同名 .jianpu.html")
    args = ap.parse_args()

    if args.all or args.check:
        files = sorted(p for p in (ROOT / "output").rglob("*.abc"))
        if not files:
            print("output/ 下没有 .abc", file=sys.stderr)
            return 1
        bad = 0
        for p in files:
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
                score = J.parse_abc(text)
                html = J.to_html(text)
                assert html.count("<table") == html.count("</table>")
                assert html.count("<tr") == html.count("</tr>")
                note = ""
                if not J.has_notation(text):
                    note = "  (无音符)"
                elif args.check:
                    note = f"  小节={score.bar_count} 声部={len(score.voices)}"
                    if score.warnings:
                        note += "  ⚠" + ";".join(dict.fromkeys(score.warnings))
                print(f"  OK  {p.name:30s}{note}")
                if not args.check:
                    convert_one(p, args.html)
            except Exception:                                     # noqa: BLE001
                bad += 1
                print(f"  !!  {p.name}")
                traceback.print_exc()
        print(f"\n共 {len(files)} 个,失败 {bad} 个")
        return 1 if bad else 0

    if not args.abc:
        ap.error("需要 .abc 路径,或用 --all / --check")
    path = Path(args.abc)
    if not path.is_absolute():
        path = Path.cwd() / path
    if not path.exists():
        print(f"找不到 {path}", file=sys.stderr)
        return 1
    if args.check:
        print(J.to_text(path.read_text(encoding="utf-8", errors="replace"), title=path.stem))
        return 0
    convert_one(path, args.html)
    return 0


if __name__ == "__main__":
    sys.exit(main())
