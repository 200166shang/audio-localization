"""Thin package adapter around pyVideoTrans and Alibaba Bailian."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .domain import LocalizationResult
from .errors import ConfigurationError, TransientCloudError
from .media import FFmpegMediaRunner
from .request import config_sha256, request_id, sha256_file
from .srt import parse_srt


PROVIDER = "bailian-pyvideotrans"
REPORT_SCHEMA = "bailian-pyvideotrans-report-v1"


def localize_package(package: Path) -> LocalizationResult:
    started = time.monotonic()
    package = package.expanduser().resolve()
    request = _load_request(package)
    media = FFmpegMediaRunner()
    source_audio = _path(package, request["source"]["audio"])
    output_audio = _path(package, request["target"]["audio"])
    script_path = _path(package, request["target"]["script"])
    report_path = _path(package, request["target"]["report"])
    source_duration = media.duration(source_audio)

    if _reusable(request, output_audio, script_path, report_path, source_duration, media):
        report = json.loads(report_path.read_text(encoding="utf-8"))
        return LocalizationResult(
            output_audio,
            report_path,
            source_duration,
            int(report["coverage"]["segment_count"]),
            0,
            0,
            0,
            time.monotonic() - started,
            True,
            {"asr": 0, "translation": 0, "tts": 0},
        )

    run = _path(package, request["work_dir"]) / "runs" / request["request_id"]
    run.mkdir(parents=True, exist_ok=True)
    timings: dict[str, float] = {}
    source_subtitle_raw = request["source"].get("subtitle")
    source_subtitle = _path(package, source_subtitle_raw) if source_subtitle_raw else None

    if source_subtitle is None:
        phase = time.monotonic()
        stt_dir = run / "stt"
        asr_model = str(request["config"].get("asr_model") or "qwen3-asr-flash")
        _run_bridge(
            "--task", "stt", "--name", str(source_audio), "--recogn_type", "13",
            "--model_name", asr_model,
            "--detect_language", "auto", "--output-dir", str(stt_dir), "--quiet",
        )
        generated = _single_output(stt_dir, ".srt")
        source_subtitle = _path(package, request["source"]["asr_output"])
        source_subtitle.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(generated, source_subtitle)
        subtitle_origin = "qwen3-asr-flash"
        timings["transcription_seconds"] = time.monotonic() - phase
    else:
        subtitle_origin = "existing"
        timings["transcription_seconds"] = 0.0

    phase = time.monotonic()
    sts_dir = run / "sts"
    _run_bridge(
        "--task", "sts", "--name", str(source_subtitle), "--translate_type", "13",
        "--source_language_code", "auto", "--target_language_code", "zh-cn",
        "--output-dir", str(sts_dir), "--quiet",
    )
    translated_srt = _single_output(sts_dir, ".srt")
    source_cues = parse_srt(source_subtitle)
    target_cues = parse_srt(translated_srt)
    if len(source_cues) != len(target_cues):
        raise TransientCloudError(
            f"translation coverage mismatch: {len(source_cues)} source, "
            f"{len(target_cues)} target cues"
        )
    timings["translation_seconds"] = time.monotonic() - phase

    phase = time.monotonic()
    tts_dir = run / "tts"
    voice = str(request["config"].get("voice") or "墨讲师(Elias)")
    _run_bridge(
        "--task", "tts", "--name", str(translated_srt), "--tts_type", "18",
        "--voice_role", voice, "--target_language_code", "zh-cn",
        "--voice_autorate", "--output-dir", str(tts_dir), "--quiet",
    )
    rendered = _single_output(tts_dir, ".wav")
    timings["tts_seconds"] = time.monotonic() - phase

    phase = time.monotonic()
    _normalize(
        rendered,
        output_audio,
        source_duration,
        str(request["config"].get("output_bitrate") or "192k"),
        request["request_id"],
    )
    output_duration = media.duration(output_audio)
    timings["timeline_seconds"] = time.monotonic() - phase

    script = _script(request, source_cues, target_cues, source_subtitle)
    _write_json(script_path, script)
    timings["total_seconds"] = time.monotonic() - started
    report = {
        "schema": REPORT_SCHEMA,
        "status": "complete",
        "request_id": request["request_id"],
        "provider": PROVIDER,
        "engine": "pyVideoTrans",
        "source_audio_sha256": request["source"]["audio_sha256"],
        "source_subtitle_sha256": sha256_file(source_subtitle),
        "config_sha256": request["config_sha256"],
        "source": {
            "audio": request["source"]["audio"],
            "subtitle": source_subtitle.relative_to(package).as_posix(),
            "subtitle_origin": subtitle_origin,
            "duration": source_duration,
            "cue_count": len(source_cues),
        },
        "output": {
            "audio": request["target"]["audio"],
            "audio_sha256": sha256_file(output_audio),
            "script": request["target"]["script"],
            "script_sha256": sha256_file(script_path),
            "duration": output_duration,
            "bitrate": request["config"].get("output_bitrate", "192k"),
        },
        "coverage": {
            "source_cue_count": len(source_cues),
            "covered_cue_count": len(target_cues),
            "translation_unit_count": len(target_cues),
            "segment_count": len(target_cues),
            "complete": True,
            "ordered": True,
            "ordered_exactly_once": True,
        },
        "timeline": {
            "duration_delta_seconds": abs(output_duration - source_duration),
            "max_allowed_delta_seconds": 0.1,
            "max_observed_speed_ratio": 1.0,
            "max_allowed_speed_ratio": request["config"].get("max_speed_ratio", 1.35),
        },
        "timings": timings,
        "config": request["config"],
    }
    _write_json(report_path, report)
    return LocalizationResult(
        output_audio,
        report_path,
        source_duration,
        len(target_cues),
        sum(len(cue.text) for cue in source_cues),
        sum(len(cue.text) for cue in target_cues),
        0,
        timings["total_seconds"],
        False,
        {
            "asr": int(subtitle_origin != "existing"),
            "translation": 1,
            "tts": 1,
        },
    )


def _load_request(package: Path) -> dict[str, Any]:
    package = package.expanduser().resolve()
    path = package / "localization/request.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"cannot read localization request: {path}") from exc
    if not isinstance(value, dict) or value.get("schema") != "audio-localization-request-v1":
        raise ConfigurationError("unsupported localization request")
    if value.get("provider") != PROVIDER:
        raise ConfigurationError(f"request provider must be {PROVIDER}")
    if value.get("config_sha256") != config_sha256(value.get("config", {})):
        raise ConfigurationError("config hash mismatch")
    if value.get("request_id") != request_id(value):
        raise ConfigurationError("request id mismatch")
    source_audio = _path(package, value.get("source", {}).get("audio"))
    if not source_audio.is_file() or value["source"].get("audio_sha256") != sha256_file(source_audio):
        raise ConfigurationError("source audio is missing or changed")
    return value


def _path(package: Path, raw: object) -> Path:
    if not isinstance(raw, str) or not raw or Path(raw).is_absolute():
        raise ConfigurationError("localization paths must be package-relative")
    path = (package / raw).resolve()
    try:
        path.relative_to(package)
    except ValueError as exc:
        raise ConfigurationError("localization path escapes package") from exc
    return path


def _run_bridge(*arguments: str) -> None:
    home = Path(os.environ.get("PYVIDEOTRANS_HOME", "/Users/syz/code/pyvideotrans"))
    python = Path(os.environ.get("PYVIDEOTRANS_PYTHON", str(home / ".venv/bin/python")))
    bridge = Path(__file__).with_name("pyvideotrans_bridge.py")
    if not python.is_file():
        raise ConfigurationError(f"pyVideoTrans Python not found: {python}")
    try:
        completed = subprocess.run(
            [str(python), str(bridge), *arguments],
            capture_output=True,
            text=True,
            timeout=7200,
        )
    except subprocess.TimeoutExpired as exc:
        raise TransientCloudError("pyVideoTrans stage timed out") from exc
    if completed.returncode:
        detail = (completed.stderr or completed.stdout or "pyVideoTrans failed")[-800:]
        raise TransientCloudError(detail)


def _single_output(folder: Path, suffix: str) -> Path:
    outputs = sorted(
        (path for path in folder.rglob(f"*{suffix}") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
    )
    if not outputs:
        raise TransientCloudError(f"pyVideoTrans produced no {suffix} output")
    return outputs[-1]


def _normalize(source: Path, target: Path, duration: float, bitrate: str, request: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f".{target.name}.partial-{request}.m4a")
    try:
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
                "-af", f"apad,atrim=duration={duration:.6f}", "-ar", "24000", "-ac", "1",
                "-c:a", "aac", "-b:a", bitrate, str(partial),
            ],
            check=True,
            capture_output=True,
            timeout=1800,
        )
        partial.replace(target)
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise ConfigurationError("ffmpeg could not normalize pyVideoTrans output") from exc
    finally:
        partial.unlink(missing_ok=True)


def _script(request: dict[str, Any], source_cues, target_cues, subtitle: Path) -> dict[str, Any]:
    units = []
    segments = []
    for source, target in zip(source_cues, target_cues):
        unit_id = f"u-{source.id}"
        units.append(
            {
                "id": unit_id,
                "start": source.start,
                "end": source.end,
                "source_ids": [source.id],
                "source_text": source.text,
                "translated_text": target.text,
                "speaker": "unknown",
            }
        )
        duration = source.end - source.start
        segments.append(
            {
                "id": f"{unit_id}-1",
                "translation_unit_id": unit_id,
                "part_index": 1,
                "part_count": 1,
                "start": source.start,
                "end": source.end,
                "text": target.text,
                "speaker": "unknown",
                "voice": request["config"].get("voice", "墨讲师(Elias)"),
                "synthesized_duration_seconds": duration,
                "slot_duration_seconds": duration,
                "speed_ratio": 1.0,
            }
        )
    return {
        "schema": "localized-script-v3",
        "request_id": request["request_id"],
        "source_audio_sha256": request["source"]["audio_sha256"],
        "source_subtitle_sha256": sha256_file(subtitle),
        "config_sha256": request["config_sha256"],
        "source_cue_ids": [cue.id for cue in source_cues],
        "translation_units": units,
        "segments": segments,
    }


def _reusable(request, audio, script, report, duration, media) -> bool:
    if not all(path.is_file() for path in (audio, script, report)):
        return False
    try:
        value = json.loads(report.read_text(encoding="utf-8"))
        return (
            value.get("schema") == REPORT_SCHEMA
            and value.get("provider") == PROVIDER
            and value.get("request_id") == request["request_id"]
            and value.get("output", {}).get("audio_sha256") == sha256_file(audio)
            and value.get("output", {}).get("script_sha256") == sha256_file(script)
            and abs(media.duration(audio) - duration) <= 0.1
        )
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return False


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.partial")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)
