"""点击级冒烟测试:直接调用 Gradio 界面的处理函数。

不经过浏览器,但走的是和点按钮完全相同的那条路径 —— 包括界面把"
最长时长(秒)"换算成 semantic_max_tokens 的逻辑。于是这一次调用同时验证:

  1. 界面输入项的接线是否正确(参数顺序/数量)
  2. 时长上限是否真的生效(设 20 秒,输出应明显短于默认的 40 秒上下)
  3. 历史试听能否取到音频(下拉与表格行点击共用同一个 /load 端点)

用法:runtime\\Scripts\\python.exe scripts\\gui-smoke-test.py
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

from gradio_client import Client

GUI = "http://127.0.0.1:7860"
SERVICE = "http://127.0.0.1:1414"
LYRICS = "[Verse]\n夜色落在窗台上\n霓虹把影子拉长\n[Chorus]\n如果风还记得那年夏天"
STYLE = "Mandarin, city pop, warm female vocal, nostalgic"
MAX_SECONDS = 20


def main() -> int:
    client = Client(GUI, verbose=False)

    endpoints = None
    try:
        api = client.view_api(return_format="dict")
        endpoints = sorted(api.get("named_endpoints", {}).keys())
        print(f"named endpoints: {endpoints}")
    except Exception as exc:  # noqa: BLE001
        print(f"view_api failed ({exc}); trying /on_generate anyway")

    name = "/on_generate"
    if endpoints and name not in endpoints:
        # Fall back to whatever generate-ish endpoint exists.
        cands = [e for e in endpoints if "generate" in e]
        if not cands:
            print(f"FAIL: no generate endpoint among {endpoints}")
            return 2
        name = cands[0]
    print(f"calling {name}  (最长时长={MAX_SECONDS}s, 规划路线=off)")

    result = client.predict(
        STYLE,            # style
        LYRICS,           # lyrics
        "off",            # cot
        831001,           # seed
        8,                # NAR steps
        1.01,             # cfg_scale
        8,                # 最短时长(秒)
        MAX_SECONDS,      # 最长时长(秒)  <- 关键
        "",               # abc 文本
        None,             # abc 文件
        None, None, None, None,          # temperature / top_p / top_k / rep
        None, None, None, None,          # abc_max / abc_temp / sem_temp / sem_rep
        api_name=name,
    )

    print(f"返回值类型: {type(result).__name__}")
    parts = result if isinstance(result, (list, tuple)) else [result]
    stage = str(parts[0]) if parts else ""
    metrics = str(parts[2]) if len(parts) > 2 else ""
    audio = parts[3] if len(parts) > 3 else None
    jianpu = str(parts[4]) if len(parts) > 4 else ""
    abc = str(parts[5]) if len(parts) > 5 else ""

    print(f"  阶段 : {stage}")
    print(f"  指标 : {metrics}")
    print(f"  音频 : {audio}")
    print(f"  乐谱 : {'有, ' + str(len(abc)) + ' 字符' if abc.strip() else '无(cot=off 预期如此)'}")
    print(f"  简谱 : {'有, ' + str(len(jianpu)) + ' 字节' if jianpu.strip() else '无(cot=off 预期如此)'}")

    ok = True
    seconds = None
    # The metrics line reads "音频时长 **20.0 s**" - note the space before the unit.
    m = re.search(r"音频时长 \*\*([0-9.]+)", metrics)
    if m:
        seconds = float(m.group(1))
    if seconds is None:
        print("FAIL: 指标里没有音频时长")
        ok = False
    elif seconds > MAX_SECONDS + 1.0:
        print(f"FAIL: 音频 {seconds}s 超过上限 {MAX_SECONDS}s —— 时长控制没生效")
        ok = False
    else:
        print(f"PASS: 音频 {seconds}s ≤ 上限 {MAX_SECONDS}s —— 时长控制生效")

    if abc.strip():
        print("NOTE: cot=off 却返回了乐谱,不符合预期")
    if jianpu.strip():
        print("NOTE: cot=off 却渲染了简谱,不符合预期")

    # ---- 简谱换算:挑一条有乐谱的历史任务,确认界面真把 ABC 变成了简谱
    try:
        jobs = json.load(urllib.request.urlopen(f"{SERVICE}/api/jobs?limit=100", timeout=15))["jobs"]
        with_abc = [j for j in jobs if j.get("has_abc")]
        print(f"\n有乐谱的历史任务 {len(with_abc)} 条")
        if with_abc:
            target = with_abc[-1]
            try:
                client.predict(api_name="/refresh")
            except Exception:  # noqa: BLE001
                pass
            try:
                _a, jp, raw = client.predict(target["id"], api_name="/load")
                via = "/load(下拉)"
            except Exception:  # noqa: BLE001
                _a, jp, raw = client.predict(target["id"], api_name="/load_1")
                via = "/load_1(自由输入)"
            jp = str(jp or "")
            raw = str(raw or "")
            has_table = 'class="jpscore"' in jp
            has_dots = 'class="od' in jp or "1'" in jp
            print(f"  {via} {target['id']}: ABC {len(raw)} 字符 -> 简谱 {len(jp)} 字节")
            print(f"  简谱表格: {has_table}  八度/减时线记号: {has_dots}")
            if not (has_table and has_dots):
                print("FAIL: 简谱没有渲染出来")
                ok = False
            else:
                print("PASS: ABC 已换算成简谱")
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: 简谱检查出错: {type(exc).__name__}: {exc}")
        ok = False

    # ---- 历史试听:界面下拉与表格行点击都调用 /load
    try:
        jobs = json.load(urllib.request.urlopen(f"{SERVICE}/api/jobs?limit=100", timeout=15))["jobs"]
        playable = [j for j in jobs if j.get("has_audio")]
        print(f"\n历史任务 {len(jobs)} 条,其中有音频 {len(playable)} 条")
        if not playable:
            print("FAIL: 历史里没有可试听的音频")
            ok = False
        else:
            target = playable[-1]
            # Gradio exposes one endpoint per distinct input component:
            #   /load   -> bound to the history Dropdown (validates against its choices)
            #   /load_1 -> bound to the free-text job id box
            # Both call the same load(), which is also what the table row click uses.
            # Populate the session's dropdown choices first, then prefer /load.
            try:
                client.predict(api_name="/refresh")
            except Exception:  # noqa: BLE001
                pass
            try:
                hist_audio, hist_jp, hist_abc = client.predict(target["id"], api_name="/load")
                via = "/load(下拉)"
            except Exception:  # noqa: BLE001
                hist_audio, hist_jp, hist_abc = client.predict(target["id"], api_name="/load_1")
                via = "/load_1(自由输入)"
            exists = bool(hist_audio) and Path(hist_audio).exists()
            print(f"  {via} {target['id']} -> {hist_audio}")
            print(f"  文件存在: {exists}  乐谱: {'有' if (hist_abc or '').strip() else '无'}"
                  f"  简谱: {'有' if str(hist_jp or '').strip() else '无'}")
            if not exists:
                print("FAIL: 历史试听取不到音频文件")
                ok = False
            else:
                print("PASS: 历史音频可试听")
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: 历史试听检查出错: {type(exc).__name__}: {exc}")
        ok = False

    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
