#!/usr/bin/env python3
"""Download the YuE2 GGUF files that the audio.cpp `yue2` loader actually reads.

Why this exists instead of calling the `hf` CLI: the `hf` entry point imports
`huggingface_hub.cli`, which builds its Typer app eagerly. In the shared runtime
at D:\\mt-tool\\runtime that combination (hub 1.13.0 + typer 0.16.0) raises
    TypeError: Typer.__init__() got an unexpected keyword argument 'suggest_commands'
before any network call happens. The download API itself is fine, so this script
uses `hf_hub_download` directly and never imports the CLI module.

Only the files listed in audio.cpp's model_specs/yue2.json are fetched. The HF
repositories also carry demo audio for the model card - one file alone is 43 MB -
and none of it is needed to generate.

Size expectations are recorded from the Hugging Face tree API on 2026-09-13. A
truncated weight file would otherwise only surface much later, inside the loader,
as an unrelated-looking error.
"""
from __future__ import annotations

import argparse
import os
import sys

EXPECTED = {
    "yue2-3b-q8_0.gguf": 4264186432,
    "yue2-3b-q4_0.gguf": 2665632320,
    "yue2-3b-bf16.gguf": 7261475392,
    "yue2-vae-f16.gguf": 265218656,
    "yue2-vae-f32.gguf": 530537760,
    "sidecars/yue2-qwen.tiktoken": 2561218,
}

SIDECARS = [
    "sidecars/yue2-model-config.json",
    "sidecars/yue2-generation-config.json",
    "sidecars/yue2-qwen.tiktoken",
    "sidecars/yue2-vae-config.json",
]


def human(n: int) -> str:
    return f"{n:,} bytes ({n / 2**30:.2f} GiB)"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default="audio-cpp/Yue2-3B-GGUF")
    ap.add_argument("--local-dir", required=True)
    ap.add_argument("--endpoint", default="https://hf-mirror.com")
    ap.add_argument("--precision", default="q8_0", choices=["q8_0", "q4_0", "bf16"])
    ap.add_argument("--vae", default="f16", choices=["f16", "f32"])
    ap.add_argument("--only", nargs="*", default=None,
                    help="fetch just these repo paths (used for link testing)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    # HF_ENDPOINT is read into a module constant at import time, so it has to be
    # set before huggingface_hub is imported.
    if args.endpoint:
        os.environ["HF_ENDPOINT"] = args.endpoint

    files = args.only or ([f"yue2-3b-{args.precision}.gguf", f"yue2-vae-{args.vae}.gguf"] + SIDECARS)

    planned = sum(EXPECTED.get(f, 0) for f in files)
    print(f"repo      : {args.repo}")
    print(f"endpoint  : {args.endpoint}")
    print(f"target    : {args.local_dir}")
    print("files     :")
    for f in files:
        sz = EXPECTED.get(f)
        print(f"  {f:<45} {human(sz) if sz else '(small)'}")
    print(f"total     : {planned / 2**30:.2f} GiB")

    if args.dry_run:
        print("\nDRY RUN - nothing downloaded.")
        return 0

    try:
        from huggingface_hub import hf_hub_download
        import huggingface_hub
    except Exception as e:  # pragma: no cover - environment problem
        print(f"FATAL: cannot import huggingface_hub: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    print(f"hub version: {huggingface_hub.__version__}")

    os.makedirs(args.local_dir, exist_ok=True)
    failures = []
    for f in files:
        print(f"\n--- {f}", flush=True)
        try:
            path = hf_hub_download(repo_id=args.repo, filename=f, local_dir=args.local_dir)
            print(f"    -> {path}")
        except Exception as e:
            print(f"    FAILED: {type(e).__name__}: {e}", file=sys.stderr)
            failures.append(f)

    print("\n=== size verification ===")
    bad = 0
    for f in files:
        p = os.path.join(args.local_dir, f.replace("/", os.sep))
        if not os.path.exists(p):
            print(f"MISSING  {f}")
            bad += 1
            continue
        n = os.path.getsize(p)
        exp = EXPECTED.get(f)
        if exp is None:
            print(f"OK       {f:<45} {n:,}")
        elif n == exp:
            print(f"OK       {f:<45} {n:,}")
        else:
            print(f"MISMATCH {f:<45} got {n:,}, expected {exp:,}")
            bad += 1

    if failures or bad:
        print(f"\n{failures and len(failures) or 0} download failure(s), {bad} verification failure(s)")
        return 1
    print("\nAll weights verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
