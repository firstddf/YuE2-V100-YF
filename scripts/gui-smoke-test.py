"""点击级冒烟测试:直接调用 Gradio 界面的处理函数。

不经过浏览器,但走的是和点按钮完全相同的那条路径 —— 包括界面把"
最长时长(秒)"换算成 semantic_max_tokens 的逻辑。于是这一次调用同时验证:

  1. 界面输入项的接线是否正确(参数顺序/数量)
  2. 时长上限是否真的生效(设 20 秒,输出应明显短于默认的 40 秒上下)
  3. 历史试听能否取到音频(下拉与表格行点击共用同一个 /load 端点)
  4. 历史参数面板能否取到种子/步数
  5. 批量导出是否真的写出"音频 + info.json"并打包出 zip(跑完会删掉测试批次)

用法:runtime\\Scripts\\python.exe scripts\\gui-smoke-test.py
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import time
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
                _a, jp, raw = client.predict(target["id"], api_name="/load")[:3]
                via = "/load(下拉)"
            except Exception:  # noqa: BLE001
                _a, jp, raw = client.predict(target["id"], api_name="/load_1")[:3]
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
            # 优先挑一个存过 request.json 的 —— 否则参数面板永远是空的,
            # 这个检查就白做了(早期任务不写 request.json)。
            with_req = [j for j in playable if j.get("request")]
            target = (with_req or playable)[-1]
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
                (hist_audio, hist_jp, hist_abc,
                 hist_params, hist_style, hist_lyrics) = client.predict(target["id"], api_name="/load")
                via = "/load(下拉)"
            except Exception:  # noqa: BLE001
                (hist_audio, hist_jp, hist_abc,
                 hist_params, hist_style, hist_lyrics) = client.predict(target["id"], api_name="/load_1")
                via = "/load_1(自由输入)"
            exists = bool(hist_audio) and Path(hist_audio).exists()
            print(f"  {via} {target['id']} -> {hist_audio}")
            print(f"  文件存在: {exists}  乐谱: {'有' if (hist_abc or '').strip() else '无'}"
                  f"  简谱: {'有' if str(hist_jp or '').strip() else '无'}")
            # 参数面板:有 request.json 的老任务才有内容
            has_params = "| `" in str(hist_params or "")
            print(f"  参数面板: {'有' if has_params else '无(该任务没存 request.json)'}"
                  f"  风格: {len(str(hist_style or ''))} 字符"
                  f"  歌词: {len(str(hist_lyrics or ''))} 字符")
            if not exists:
                print("FAIL: 历史试听取不到音频文件")
                ok = False
            else:
                print("PASS: 历史音频可试听")
            # 参数面板:种子/步数是这次新增的能力,存过 request.json 就必须渲染出来
            if target.get("request"):
                seed = target["request"].get("seed")
                steps = target["request"].get("num_inference_steps")
                if has_params and f"`seed`" in str(hist_params):
                    print(f"PASS: 参数面板可见(seed={seed} steps={steps})")
                else:
                    print("FAIL: 该任务有 request.json,但参数面板没渲染出来")
                    ok = False
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: 历史试听检查出错: {type(exc).__name__}: {exc}")
        ok = False

    # ---- 批量导出:一首一个文件夹 + 批次 zip。跑完把产物删掉,不留垃圾。
    try:
        jobs = json.load(urllib.request.urlopen(f"{SERVICE}/api/jobs?limit=100", timeout=15))["jobs"]
        target = next((j for j in jobs if j.get("has_audio")), None)
        print("\n导出检查")
        if not target:
            print("FAIL: 没有可导出的任务")
            ok = False
        else:
            batch = f"_smoke_{int(time.time())}"
            body = json.dumps({"ids": [target["id"]], "name": batch}).encode()
            req = urllib.request.Request(f"{SERVICE}/api/export", data=body,
                                         headers={"Content-Type": "application/json"})
            r = json.load(urllib.request.urlopen(req, timeout=300))
            folder = Path(r["dir"])
            zip_path = Path(r["zip"])
            song_dirs = [d for d in folder.iterdir() if d.is_dir()]
            print(f"  批次 {r['name']}:{r['count']} 首,zip {r['bytes'] / 1024 / 1024:.2f} MB")
            if song_dirs:
                got = sorted(p.name for p in song_dirs[0].iterdir())
                print(f"  单曲文件夹 {song_dirs[0].name}:{', '.join(got)}")
                # info.json 是这次新增的关键产物 —— 指标本来只活在内存解析里
                need = {"audio.wav", "info.json"}
                if need <= set(got) and zip_path.is_file():
                    print("PASS: 导出含音频 + info.json,且 zip 已生成")
                else:
                    print(f"FAIL: 导出内容不全,缺 {need - set(got)}")
                    ok = False
            else:
                print("FAIL: 批次里没有单曲文件夹")
                ok = False
            shutil.rmtree(folder, ignore_errors=True)
            zip_path.unlink(missing_ok=True)
            print("  (已清理测试批次)")
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: 导出检查出错: {type(exc).__name__}: {exc}")
        ok = False

    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
