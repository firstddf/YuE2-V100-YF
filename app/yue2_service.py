#!/usr/bin/env python3
"""YuE2 local service: job queue + stage progress on top of audiocpp_cli.

Why this exists instead of audio.cpp's own server: that server is a generic
adapter for 60+ model families and only exposes a synchronous "call and wait"
request. This project needs a job queue, stage-level progress parsed from the
CLI's own timing log, ABC score export, and a Chinese-facing API surface.

Engine: one `audiocpp_cli.exe` process per job. That costs roughly 5-6 s of model
reload per song, which is the accepted trade for statelessness and for getting
real stage progress out of the CLI's `--log` output. If reload ever becomes
annoying, the upgrade path is to keep a warm session inside this process.

Run:  <runtime-python> app/yue2_service.py --port 1414
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / "build" / "bin" / "Release" / "audiocpp_cli.exe"
MODEL_DIR = ROOT / "models" / "Yue2-3B-GGUF"
JOBS_DIR = ROOT / "output" / "gui" / "jobs"
LOGS_DIR = ROOT / "logs"
EXAMPLES = ROOT / "examples"
SCRIPTS = ROOT / "scripts"
# 参考曲分析的两级:librosa(纯 CPU)与 MuScriptor 转谱(占 GPU)
ANALYSIS_DIR = ROOT / "output" / "analysis"
MUSCRIPTOR_MODEL = ROOT / "models" / "MuScriptor-Small-GGUF" / "muscriptor-small-f32.gguf"

# Stage keys as they appear in the CLI's timing log, in the order they are hit.
# The highest index seen so far is the current stage, which is enough for an
# honest progress display: the CLI does not emit token-level progress.
STAGES: list[tuple[str, str]] = [
    ("yue2.plan_ms", "解析请求"),
    ("yue2.semantic.abc_generate_ms", "乐谱规划 (ABC)"),
    ("yue2.semantic.music_generate_ms", "语义生成"),
    ("yue2.nar_ms", "声学合成 (NAR)"),
    ("yue2.vae_decode_ms", "音频解码 (VAE)"),
    ("session.wall_ms", "完成"),
]
# Shown until the first stage key shows up. The log emits plan_ms before
# ar.init_ms, so this is what actually covers engine start + model load.
BOOT_STAGE = "启动引擎 · 加载模型"
STAGE_INDEX = {key: i for i, (key, _) in enumerate(STAGES)}

# Style presets: genre / instrument / mood / gender / timbre, the five components
# the YuE prompt guide recommends, plus the language tag that steers the vocal.
#
# Every token here must exist in examples/style_tags.json: the GUI turns a preset
# into checkbox state, and a token outside the vocabulary would silently land in
# the "自定义补充" box instead of ticking a box. scripts\test-style-tags.py enforces it.
STYLE_PRESETS: dict[str, str] = {
    "华语流行 Mandarin pop":
        "Mandarin, pop, electric piano, synth bass, drum machine, female, warm vocal, sentimental",
    "华语城市流行 Mandarin city pop":
        "Mandarin, city pop, electric piano, Rhodes, groovy bass, live drums, female, warm vocal, nostalgic",
    "粤语流行 Cantonese pop":
        "Cantonese, pop rock, electric guitar, drums, male, powerful vocal, energetic",
    "英语独立流行 English indie pop":
        "English, indie pop, acoustic guitar, drums, male, clear vocal, uplifting",
    "英语爵士放克 English jazz funk":
        "English, funk, electric piano, Rhodes, groovy bass, live drums, male, smooth vocal, groovy",
    "日系动漫日语 Japanese anison":
        "Japanese, anison, synthesizer, synth, drum machine, female, bright vocal, energetic",
    "电影感史诗 Cinematic epic":
        "English, soundtrack, epic, orchestral, strings, piano, brass, powerful vocal, cinematic",
    "赛博金属 Cyber metal":
        "English, heavy metal, electric guitar, drums, aggressive vocal, intense, dark",
    "纯器乐风格标签(还需把歌词留空,见界面「🎹 纯器乐模式」)":
        "English, instrumental, ambient, piano, strings, calm, relaxing",
}


# 采样参数的范围,与引擎的 validate_sampling 一致。
# 引擎报错只说 "abc sampling options are invalid" —— 不说哪个参数、不说哪个值,
# 而失败要等到加载完模型才发生。在这里先拦一次,把话说清楚。
_SAMPLING_RULES: tuple[tuple[str, float | None, float | None, bool], ...] = (
    # (名字, 下界, 上界, 下界是否可取)
    ("temperature", 0.0, 5.0, True),
    ("top_p", 0.0, 1.0, False),
    ("top_k", 1, None, True),
    ("repetition_penalty", 0.0, None, False),
    ("penalty_window", 1, None, True),
    ("min_tokens", 0, None, True),
)
_SAMPLING_DEFAULTS = {"abc": {"min_tokens": 32.0, "max_tokens": 4096.0},
                      "semantic": {"min_tokens": 200.0, "max_tokens": 9000.0}}


def validate_sampling(request: dict[str, Any]) -> None:
    """校验 abc_* / semantic_* 采样参数,越界时抛出可直接照做的说明。"""
    for prefix in ("abc", "semantic"):
        for name, lo, hi, lower_ok in _SAMPLING_RULES:
            key = f"{prefix}_{name}"
            raw = request.get(key)
            if raw in (None, ""):
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                raise ValueError(f"{key} 必须是数字,收到 {raw!r}") from None
            if lo is not None and (value < lo if lower_ok else value <= lo):
                op = "≥" if lower_ok else ">"
                raise ValueError(f"{key}={value:g} 越界,要求 {op} {lo:g}")
            if hi is not None and value > hi:
                raise ValueError(f"{key}={value:g} 越界,要求 ≤ {hi:g}")
        defaults = _SAMPLING_DEFAULTS[prefix]
        low = request.get(f"{prefix}_min_tokens")
        high = request.get(f"{prefix}_max_tokens")
        low = defaults["min_tokens"] if low in (None, "") else float(low)
        high = defaults["max_tokens"] if high in (None, "") else float(high)
        if high < low:
            raise ValueError(
                f"{prefix}_max_tokens({high:g})必须 ≥ "
                f"{prefix}_min_tokens({low:g})"
                + ("(后者未指定,用的是引擎默认值)" if request.get(f"{prefix}_min_tokens") in (None, "") else ""))


# ---------------------------------------------------------------- job model
@dataclass
class Job:
    id: str
    created: float
    status: str = "queued"          # queued | running | done | failed | cancelled
    stage: str = "排队中"
    stage_index: int = -1
    progress: float = 0.0
    elapsed_s: float = 0.0
    request: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    abc: str = ""
    audio: str = ""
    error: str = ""
    log_path: str = ""
    _proc: Optional[subprocess.Popen] = field(default=None, repr=False)
    _cancel: bool = field(default=False, repr=False)

    def public(self) -> dict[str, Any]:
        # Built by hand on purpose: dataclasses.asdict() deep-copies every field,
        # and the private ones hold a live Popen object, which cannot be copied.
        return {
            "id": self.id,
            "created": self.created,
            "created_str": time.strftime("%Y-%m-%d %H:%M", time.localtime(self.created)),
            "status": self.status,
            "stage": self.stage,
            "stage_index": self.stage_index,
            "progress": self.progress,
            "elapsed_s": self.elapsed_s,
            "request": self.request,
            "metrics": self.metrics,
            "abc": self.abc,
            "audio": self.audio,
            "error": self.error,
            "has_audio": bool(self.audio) and Path(self.audio).exists(),
            "has_abc": bool(self.abc),
        }


class Yue2Service:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}
        self.order: list[str] = []
        self.lock = threading.Lock()
        # One worker: a single GPU, and serial execution keeps timings meaningful.
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="yue2")
        JOBS_DIR.mkdir(parents=True, exist_ok=True)
        self.restored = self._restore_history()

    # ------------------------------------------------------------ persistence
    def _restore_history(self) -> int:
        """Rebuild the job list from disk.

        Jobs live in memory only, so without this a service restart empties the
        history tab even though every artifact is still sitting in
        output/gui/jobs/<id>/ (audio.wav, score.abc, run.log). Metrics and the
        score are re-read from those files; the request is read back from
        request.json, which older job directories will not have.
        """
        if not JOBS_DIR.exists():
            return 0
        count = 0
        try:
            entries = sorted((d for d in JOBS_DIR.iterdir() if d.is_dir()),
                             key=lambda d: d.stat().st_mtime)
        except OSError:
            return 0
        for directory in entries:
            job_id = directory.name
            if job_id in self.jobs:
                continue
            try:
                job = Job(id=job_id, created=directory.stat().st_mtime)
            except OSError:
                continue
            job.log_path = str(directory / "run.log")
            request_file = directory / "request.json"
            if request_file.exists():
                try:
                    job.request = json.loads(request_file.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    job.request = {}
            audio_file = directory / "audio.wav"
            if audio_file.exists():
                job.audio = str(audio_file)
                try:
                    # 用**产物本身**的时间做 created,而不是目录 mtime ——
                    # 目录 mtime 会被之后写进该目录的任何文件刷新(例如 score.jianpu.txt),
                    # 于是历史里的时间会漂。
                    job.created = audio_file.stat().st_mtime
                except OSError:
                    pass
            score_file = directory / "score.abc"
            if score_file.exists():
                job.abc = score_file.read_text(encoding="utf-8", errors="replace")
            job.metrics = self._read_metrics(job.log_path)
            job.elapsed_s = job.metrics.get("wall_s") or 0.0
            if job.audio:
                job.status, job.stage, job.progress = "done", "完成", 1.0
            else:
                job.status, job.stage = "failed", "失败"
                job.error = self._tail_error(job.log_path) or "(历史记录,无音频产物)"
            self.jobs[job_id] = job
            self.order.append(job_id)
            count += 1
        return count

    # ------------------------------------------------------------ analysis
    def analyze(self, request: dict[str, Any]) -> dict[str, Any]:
        """分析参考音频:一级 librosa 特征,二级加 MuScriptor 音符级转谱。

        MuScriptor 要占 GPU,而生成任务用的是同一块卡,所以 level=full 时
        只要队列里有任务在跑就直接拒绝 —— 与其让两边抢显存、让用户以为在排队,
        不如明确说清楚。
        """
        raw = str(request.get("audio") or "").strip()
        if not raw:
            raise ValueError("audio 不能为空")
        audio = Path(raw)
        if not audio.is_absolute():
            audio = ROOT / audio
        if not audio.exists():
            raise ValueError(f"找不到音频:{audio}")

        level = request.get("level") or "basic"
        if level not in ("basic", "full"):
            raise ValueError("level 必须是 basic 或 full")
        bpm_raw = request.get("bpm")
        bpm: Optional[float] = None
        if bpm_raw not in (None, ""):
            try:
                bpm = float(bpm_raw)
            except (TypeError, ValueError):
                raise ValueError("bpm 必须是数字") from None

        if level == "full":
            busy = [j.id for j in self.jobs.values() if j.status in ("queued", "running")]
            if busy:
                raise RuntimeError(
                    f"有 {len(busy)} 个生成任务在跑(如 {busy[0]})。"
                    "MuScriptor 会和生成抢显存,请等它跑完再分析")

        out_dir = ANALYSIS_DIR / audio.stem
        out_dir.mkdir(parents=True, exist_ok=True)

        # 一级:librosa —— 纯 CPU,秒级
        basic_json = out_dir / "basic.json"
        self._run_script("analyze-track.py", [str(audio), "--json", str(basic_json)])
        rows = json.loads(basic_json.read_text(encoding="utf-8"))
        result: dict[str, Any] = {"audio": str(audio), "level": level, "basic": rows[0]}

        # 二级:MuScriptor 转谱 + 节奏分析
        if level == "full":
            notes = out_dir / "notes.json"
            self._run_muscriptor(audio, notes)
            summary_json = out_dir / "summary.json"
            args = [str(notes), "--json", str(summary_json)]
            if bpm is not None:
                args += ["--bpm", str(bpm)]
            self._run_script("muscriptor-summary.py", args)
            result["notes"] = json.loads(summary_json.read_text(encoding="utf-8"))
            result["notes_file"] = str(notes)
        return result

    @staticmethod
    def _run_script(script: str, args: list[str]) -> None:
        """调用 scripts\\ 下的分析脚本。用服务自己的解释器,避免 PATH 里是另一个 Python。"""
        proc = subprocess.run([sys.executable, str(SCRIPTS / script), *args],
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=900)
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip()[-400:]
            raise RuntimeError(f"{script} 失败:{tail}")

    @staticmethod
    def _run_muscriptor(audio: Path, out_json: Path) -> None:
        if not MUSCRIPTOR_MODEL.exists():
            raise RuntimeError(f"缺少 MuScriptor 权重:{MUSCRIPTOR_MODEL}")
        argv = [str(ENGINE), "--task", "midi", "--family", "muscriptor",
                "--model", str(MUSCRIPTOR_MODEL), "--backend", "cuda",
                "--audio", str(audio),
                "--request-option", "output_format=json",
                "--out", str(out_json), "--log", "--metrics"]
        env = dict(os.environ)
        cuda_bin = r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.8\bin"
        if cuda_bin not in env.get("PATH", ""):
            env["PATH"] = cuda_bin + os.pathsep + env.get("PATH", "")
        proc = subprocess.run(argv, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", env=env, timeout=1800)
        if proc.returncode != 0 or not out_json.exists():
            tail = (proc.stderr or proc.stdout or "").strip()[-400:]
            raise RuntimeError(f"MuScriptor 转谱失败:{tail}")

    # ------------------------------------------------------------ validation
    def preflight(self) -> dict[str, Any]:
        return {
            "engine": str(ENGINE),
            "engine_exists": ENGINE.exists(),
            "model_dir": str(MODEL_DIR),
            "model_exists": MODEL_DIR.exists(),
            "jobs_dir": str(JOBS_DIR),
            "python": sys.version.split()[0],
        }

    # ------------------------------------------------------------ submission
    def submit(self, request: dict[str, Any]) -> Job:
        if not ENGINE.exists():
            raise RuntimeError(f"engine not found: {ENGINE}")
        if not MODEL_DIR.exists():
            raise RuntimeError(f"model dir not found: {MODEL_DIR}")
        style = (request.get("style") or "").strip()
        lyrics = (request.get("lyrics") or "").strip()
        if not style:
            raise ValueError("style 不能为空")
        if not lyrics:
            raise ValueError("lyrics 不能为空")
        cot = request.get("cot") or "off"
        if cot not in ("off", "melody", "full"):
            raise ValueError("cot 必须是 off / melody / full")
        if request.get("abc") and cot == "off":
            raise ValueError("提供 ABC 乐谱时,cot 必须为 melody 或 full")
        validate_sampling(request)

        job = Job(id=uuid.uuid4().hex[:12], created=time.time(), request=request)
        jobdir = JOBS_DIR / job.id
        jobdir.mkdir(parents=True, exist_ok=True)
        # Persist the request so a restart can rebuild the history entry, not just
        # the artifacts.
        (jobdir / "request.json").write_text(
            json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")
        job.log_path = str(jobdir / "run.log")
        with self.lock:
            self.jobs[job.id] = job
            self.order.append(job.id)
        self.pool.submit(self._run, job)
        return job

    def cancel(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if not job:
            return False
        job._cancel = True
        proc = job._proc
        if proc and proc.poll() is None:
            proc.terminate()
        return True

    # ------------------------------------------------------------ execution
    def _build_argv(self, job: Job, out_wav: Path, out_abc: Path) -> list[str]:
        r = job.request
        argv = [
            str(ENGINE), "--task", "gen", "--family", "yue2",
            "--model", str(MODEL_DIR), "--backend", "cuda",
            "--threads", str(int(r.get("threads", 8))),
            "--request-option", f"style={r['style']}",
            "--request-option", f"lyrics={r['lyrics']}",
            "--request-option", f"cot={r.get('cot', 'off')}",
            "--out", str(out_wav),
            "--log", "--metrics",
        ]

        # ABC conditioning: inline text wins over a file path.
        abc_text = (r.get("abc") or "").strip()
        abc_file = (r.get("abc_file") or "").strip()
        if abc_text:
            argv += ["--request-option", f"abc={abc_text}"]
        elif abc_file:
            argv += ["--request-option", f"abc_file={abc_file}"]

        for key in ("seed", "cfg_scale", "num_inference_steps",
                    "abc_temperature", "abc_top_p", "abc_top_k",
                    "abc_repetition_penalty", "abc_penalty_window",
                    "abc_min_tokens", "abc_max_tokens",
                    "semantic_temperature", "semantic_top_p", "semantic_top_k",
                    "semantic_repetition_penalty", "semantic_penalty_window",
                    "semantic_min_tokens", "semantic_max_tokens"):
            value = r.get(key)
            if value is None or value == "":
                continue
            argv += ["--request-option", f"{key}={value}"]

        # --text-out only makes sense when the model produces notation. With
        # cot=off the CLI aborts with "task result has no text output", so it is
        # deliberately omitted there.
        if r.get("cot", "off") in ("melody", "full"):
            argv += ["--text-out", str(out_abc)]
        return argv

    def _run(self, job: Job) -> None:
        jobdir = Path(job.log_path).parent
        out_wav = jobdir / "audio.wav"
        out_abc = jobdir / "score.abc"
        argv = self._build_argv(job, out_wav, out_abc)

        job.status = "running"
        job.stage = BOOT_STAGE
        started = time.time()
        env = dict(os.environ)
        cuda_bin = r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.8\bin"
        if cuda_bin not in env.get("PATH", ""):
            env["PATH"] = cuda_bin + os.pathsep + env.get("PATH", "")

        try:
            with open(job.log_path, "w", encoding="utf-8", errors="replace") as logf:
                proc = subprocess.Popen(argv, stdout=logf, stderr=subprocess.STDOUT, env=env)
                job._proc = proc
                skip = 0
                while proc.poll() is None:
                    if job._cancel:
                        proc.terminate()
                        break
                    self._scan_log(job, started)
                    time.sleep(0.4)
                code = proc.wait()
            self._scan_log(job, started)

            if job._cancel:
                job.status, job.stage = "cancelled", "已取消"
                return
            if code != 0:
                job.status = "failed"
                job.stage = "失败"
                job.error = self._tail_error(job.log_path) or f"引擎退出码 {code}"
                return

            job.metrics = self._read_metrics(job.log_path)
            if out_wav.exists():
                job.audio = str(out_wav)
            if out_abc.exists():
                job.abc = out_abc.read_text(encoding="utf-8", errors="replace")
                self._write_jianpu(out_abc)
            job.status = "done"
            job.stage = "完成"
            job.progress = 1.0
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI verbatim
            job.status, job.stage, job.error = "failed", "失败", f"{type(exc).__name__}: {exc}"
        finally:
            job.elapsed_s = round(time.time() - started, 1)
            job._proc = None

    @staticmethod
    def _write_jianpu(abc_path: Path) -> None:
        """顺带导出一份简谱文本(score.jianpu.txt),方便直接抄或改。

        这是附加产物:模型乐谱能导出就导出,导不出来也绝不能影响出歌本身,
        所以解析异常在这里被吞掉(界面里仍会渲染简谱,只是没有 .txt)。
        """
        try:
            from abc_jianpu import has_notation, to_text
            text = abc_path.read_text(encoding="utf-8", errors="replace")
            if has_notation(text):
                abc_path.with_suffix(".jianpu.txt").write_text(
                    to_text(text, title=abc_path.parent.name), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    def _scan_log(self, job: Job, started: float) -> None:
        """Derive the current stage from the timing log. Cheap enough to re-read."""
        try:
            text = Path(job.log_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        seen = -1
        for key, _label in STAGES:
            if re.search(re.escape(key) + r"[\s=]", text):
                seen = max(seen, STAGE_INDEX[key])
        if seen > job.stage_index:
            job.stage_index = seen
            job.stage = STAGES[seen][1]
            job.progress = round((seen + 1) / len(STAGES), 3)
        job.elapsed_s = round(time.time() - started, 1)

    @staticmethod
    def _metric(text: str, key: str) -> Optional[float]:
        m = re.search(re.escape(key) + r"[=]\s*([0-9.]+)", text)
        return float(m.group(1)) if m else None

    def _read_metrics(self, log_path: str) -> dict[str, Any]:
        try:
            text = Path(log_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return {}
        out: dict[str, Any] = {}
        wall = self._metric(text, "metrics.wall_ms")
        audio = self._metric(text, "metrics.audio_duration_ms")
        rtf = self._metric(text, "metrics.rtf")
        if wall is not None:
            out["wall_s"] = round(wall / 1000, 2)
        if audio is not None:
            out["audio_s"] = round(audio / 1000, 2)
        if rtf is not None:
            out["rtf"] = round(rtf, 3)
            out["x_realtime"] = round(1 / rtf, 2) if rtf > 0 else None
        for key, name in (("yue2.semantic.tokens", "semantic_tokens"),
                          ("yue2.semantic.abc_generated_tokens", "abc_tokens"),
                          ("yue2.semantic.abc_text_bytes", "abc_bytes"),
                          ("yue2.plan.abc_tokens", "input_abc_tokens")):
            m = re.search(re.escape(key) + r"[\s=]+([0-9]+)", text)
            if m:
                out[name] = int(m.group(1))
        return out

    @staticmethod
    def _tail_error(log_path: str) -> str:
        try:
            lines = [ln.strip() for ln in Path(log_path).read_text(
                encoding="utf-8", errors="replace").splitlines() if ln.strip()]
        except OSError:
            return ""
        for ln in reversed(lines):
            if "failed" in ln.lower() or "error" in ln.lower() or "requires" in ln.lower():
                return ln[:400]
        return lines[-1][:400] if lines else ""

    # ------------------------------------------------------------ queries
    def get(self, job_id: str) -> Optional[Job]:
        return self.jobs.get(job_id)

    def list_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        """按**创建时间**倒序返回(新的在前)。

        不能按 self.order 倒序:重启后从磁盘恢复的任务是按目录扫描顺序追加进去的,
        那个顺序不是时间顺序 —— 结果"历史"看起来是乱的。created 才是权威。
        """
        jobs = sorted(self.jobs.values(), key=lambda j: j.created, reverse=True)
        return [j.public() for j in jobs[:limit]]

    def delete(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if not job:
            return False
        if job.status == "running":
            return False
        with self.lock:
            self.jobs.pop(job_id, None)
            if job_id in self.order:
                self.order.remove(job_id)
        shutil.rmtree(Path(job.log_path).parent, ignore_errors=True)
        return True


# ---------------------------------------------------------------- HTTP layer
def build_app(service: Yue2Service):
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import FileResponse

    app = FastAPI(title="YuE2 local service", version="1.0")

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        info = service.preflight()
        info["ok"] = info["engine_exists"] and info["model_exists"]
        info["jobs"] = len(service.order)
        info["restored_from_disk"] = service.restored
        return info

    @app.get("/api/presets")
    def presets() -> dict[str, Any]:
        return {"styles": STYLE_PRESETS, "stages": [label for _k, label in STAGES]}

    @app.post("/api/analyze")
    def analyze(payload: dict[str, Any]) -> dict[str, Any]:
        """分析参考音频。level=basic 只跑 librosa;level=full 加 MuScriptor 转谱。"""
        try:
            return service.analyze(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            # 409:GPU 被生成任务占着,不是请求本身有问题
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/jobs")
    def create_job(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            job = service.submit(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return job.public()

    @app.get("/api/jobs")
    def list_jobs(limit: int = 50) -> dict[str, Any]:
        return {"jobs": service.list_jobs(limit)}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        job = service.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="job not found")
        return job.public()

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str) -> dict[str, Any]:
        if not service.cancel(job_id):
            raise HTTPException(status_code=404, detail="job not found")
        return {"cancelled": job_id}

    @app.delete("/api/jobs/{job_id}")
    def delete_job(job_id: str) -> dict[str, Any]:
        if not service.delete(job_id):
            raise HTTPException(status_code=409, detail="job not found or still running")
        return {"deleted": job_id}

    @app.get("/api/jobs/{job_id}/audio")
    def job_audio(job_id: str):
        job = service.get(job_id)
        if not job or not job.audio or not Path(job.audio).exists():
            raise HTTPException(status_code=404, detail="audio not ready")
        return FileResponse(job.audio, media_type="audio/wav",
                            filename=f"yue2-{job_id}.wav")

    @app.get("/api/jobs/{job_id}/abc")
    def job_abc(job_id: str) -> dict[str, Any]:
        job = service.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="job not found")
        return {"abc": job.abc}

    @app.get("/api/jobs/{job_id}/log")
    def job_log(job_id: str, tail: int = 400) -> dict[str, Any]:
        """任务日志。tail<=0 表示返回全部行。"""
        job = service.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="job not found")
        try:
            lines = Path(job.log_path).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            lines = []
        total = len(lines)
        shown = lines if tail <= 0 else lines[-max(1, tail):]
        return {"lines": shown, "total_lines": total, "path": job.log_path}

    @app.get("/api/logs")
    def list_logs() -> dict[str, Any]:
        """列出 logs\\ 下的日志文件,供界面挑选查看。"""
        if not LOGS_DIR.is_dir():
            return {"logs": [], "dir": str(LOGS_DIR)}
        rows = []
        for pattern in ("*.log", "*.txt"):
            for path in LOGS_DIR.glob(pattern):
                try:
                    st = path.stat()
                except OSError:
                    continue
                rows.append({
                    "name": path.name,
                    "bytes": st.st_size,
                    "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)),
                })
        rows.sort(key=lambda r: r["mtime"], reverse=True)
        return {"logs": rows, "dir": str(LOGS_DIR)}

    @app.get("/api/logs/{name}")
    def read_log(name: str, tail: int = 400) -> dict[str, Any]:
        # safe_log_path 是模块级函数,不依赖 fastapi,所以它抛普通异常,这里转成 HTTP。
        try:
            path = safe_log_path(name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        total = len(lines)
        return {"name": path.name, "lines": lines if tail <= 0 else lines[-max(1, tail):],
                "total_lines": total, "path": str(path)}

    return app


def safe_log_path(name: str) -> Path:
    """把日志名解析成 logs\\ 下的真实文件,拒绝路径穿越。

    只接受**裸文件名** —— 界面上能提交任意字符串,不能让它读到 logs\\ 之外。
    抛 ValueError(非法名)/ FileNotFoundError(不存在),由调用方转成 HTTP 状态码 ——
    这样本函数不必依赖 fastapi 的异常类型。
    """
    if not name or name.strip() != name or any(c in name for c in "/\\") or name in (".", ".."):
        raise ValueError(f"非法的日志名:{name!r}")
    path = (LOGS_DIR / name).resolve()
    if path.parent != LOGS_DIR.resolve() or not path.is_file():
        raise FileNotFoundError(f"日志不存在:{name}")
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=1414)
    args = ap.parse_args()

    service = Yue2Service()
    info = service.preflight()
    print("YuE2 local service")
    for key in ("engine", "engine_exists", "model_dir", "model_exists", "python"):
        print(f"  {key:<14} {info[key]}")
    print(f"  history        {service.restored} 个任务从磁盘恢复,共 {len(service.order)} 个")
    if not (info["engine_exists"] and info["model_exists"]):
        print("\nFATAL: engine or model missing - run scripts\\configure.ps1 + build first")
        return 2

    import uvicorn
    print(f"\nlistening on http://{args.host}:{args.port}  (docs: /docs)")
    uvicorn.run(build_app(service), host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
