"""用 whisper-small 转写生成结果,对比"有人声 / 无人声"。

演唱音频的 ASR 准确率天然很差,所以这里看的不是"转对多少",而是:
  * 对照曲(歌词已知)应当转出若干可辨认的词
  * 纯器乐方案应当几乎转不出词

对照组很关键 —— 只有两组一比,"转不出词"才能说明问题。

用法:
    python scripts\\asr-check.py output\\yue2-01.wav output\\instr-D-official.wav
    python scripts\\asr-check.py --model openai/whisper-small <files...>
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 本地已有 whisper-small 缓存(快照完整),优先离线加载
os.environ.setdefault("HF_HUB_CACHE", r"D:\mt-tool\models_cache")
os.environ.setdefault("HF_HOME", r"D:\mt-tool\models_cache")


def load_asr(model_id: str, use_gpu: bool):
    import torch
    from transformers import (
        WhisperForConditionalGeneration,
        WhisperProcessor,
        pipeline,
    )

    if use_gpu and torch.cuda.is_available():
        device = 0
    else:
        device = -1
    print(f"加载 {model_id}  device={'cuda' if device == 0 else 'cpu'} ...")
    try:
        model = WhisperForConditionalGeneration.from_pretrained(model_id, local_files_only=True)
        proc = WhisperProcessor.from_pretrained(model_id, local_files_only=True)
    except Exception as exc:  # noqa: BLE001
        print(f"  本地加载失败({exc}),改为联网获取")
        model = WhisperForConditionalGeneration.from_pretrained(model_id)
        proc = WhisperProcessor.from_pretrained(model_id)
    return pipeline("automatic-speech-recognition", model=model,
                    tokenizer=proc.tokenizer, feature_extractor=proc.feature_extractor,
                    device=device)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+")
    ap.add_argument("--model", default="openai/whisper-small")
    ap.add_argument("--gpu", action="store_true", help="用 GPU(默认 CPU,避免和引擎抢显存)")
    args = ap.parse_args()

    import soundfile as sf

    asr = load_asr(args.model, args.gpu)

    print(f"\n{'文件':<34} {'语言':<6} {'词数':>5}  转写")
    print("-" * 100)
    summary = []
    for name in args.files:
        path = Path(name)
        if not path.exists():
            print(f"{name:<34} 缺失")
            continue
        audio, sr = sf.read(str(path), always_2d=True)
        mono = audio.mean(axis=1)
        result = asr({"array": mono, "sampling_rate": sr},
                     generate_kwargs={"language": None, "task": "transcribe"},
                     # >30 秒的音频会走 whisper 的长格式生成,必须带时间戳
                     return_timestamps=True)
        text = (result.get("text") or "").strip()
        words = [w for w in text.replace(".", " ").replace(",", " ").split() if w]
        summary.append((path.name, len(words), text))
        print(f"{path.name:<34} {'?':<6} {len(words):>5}  {text[:60]}")

    print("\n对比(词数越多说明越可能有人在唱):")
    for name, count, _ in summary:
        bar = "#" * min(40, count)
        print(f"  {name:<34} {count:>4}  {bar}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
