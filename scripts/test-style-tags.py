"""风格标签逻辑的单元测试(不需要界面或引擎)。

验证两件事:
  1. DEFAULT_STYLE 完全落在词表内 —— 否则界面一打开,勾选状态与风格框就不一致
  2. split_style() 能把预设风格串正确还原成各类勾选,未知词进「自定义补充」

用法:runtime\\Scripts\\python.exe scripts\\test-style-tags.py
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

import gui  # noqa: E402  (needs the sys.path insert above)


def main() -> int:
    tags = gui.load_style_tags()
    zh = gui.load_style_zh()
    print("标签类别数量:", {k: len(v) for k, v in tags.items()})
    if not tags:
        print("FAIL: 标签表没加载到 (examples/style_tags.json)")
        return 1

    failures = 0

    # 每个英文标签都该有中文解释(中文标签自身不需要)。界面显示「english · 中文」,
    # 缺解释会退化成只显示英文,不算错误但应尽量避免。
    missing = [(cat, t) for cat, items in tags.items() for t in items
               if t.isascii() and t not in zh]
    print(f"\n中文对照: 英文标签 {sum(1 for i in tags.values() for t in i if t.isascii())} 个,"
          f"缺解释 {len(missing)} 个")
    if missing:
        for cat, tag in missing[:20]:
            print(f"   缺: [{cat}] {tag}")
        print("FAIL: 有英文标签没有中文解释")
        failures += 1
    else:
        print("PASS: 所有英文标签都有中文解释")

    picked = gui.split_style(gui.DEFAULT_STYLE, tags)
    print(f"\nDEFAULT_STYLE = {gui.DEFAULT_STYLE}")
    for key, values in picked.items():
        print(f"   {key:<11} {values}")
    if picked["custom"]:
        print("FAIL: 默认风格里有词不在词表内,界面初始状态会不一致")
        failures += 1
    else:
        print("PASS: 默认风格全部命中词表")

    # 预设回填:直接从运行中的服务取,保证测的是界面真实会收到的内容
    try:
        presets = json.load(urllib.request.urlopen("http://127.0.0.1:1414/api/presets", timeout=10))["styles"]
    except Exception as exc:  # noqa: BLE001
        print(f"\nSKIP 预设回填检查(服务不可用: {exc})")
        presets = {}

    for name, text in presets.items():
        got = gui.split_style(text, tags)
        print(f"\n{name}")
        print(f"   原文: {text}")
        print(f"   勾选: 语言={got['language']} 曲风={got['genre']} 乐器={got['instrument']}")
        print(f"         情绪={got['mood']} 人声={got['gender']} 音色={got['timbre']}")
        print(f"   自定义补充: {got['custom']}")
        if not any(got[c] for c in gui.TAG_CATEGORIES):
            print("   FAIL: 没有任何标签被识别")
            failures += 1

    # 往返:拼回去再解析,应当稳定
    composed = gui.split_style(gui.DEFAULT_STYLE, tags)
    rebuilt = ", ".join(
        t for c in gui.TAG_CATEGORIES for t in composed[c]
    ) + (", " + ", ".join(composed["custom"]) if composed["custom"] else "")
    again = gui.split_style(rebuilt, tags)
    same = all(again[c] == composed[c] for c in gui.TAG_CATEGORIES) and again["custom"] == composed["custom"]
    print(f"\n往返一致性: {'PASS' if same else 'FAIL'}   ({rebuilt})")
    if not same:
        failures += 1

    print("\nRESULT:", "PASS" if failures == 0 else f"FAIL ({failures})")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
