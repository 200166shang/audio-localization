from pathlib import Path

import pytest

from audio_localization import pyvideotrans_pipeline


def test_stt_passes_requested_asr_model_to_pyvideotrans(tmp_path: Path, monkeypatch):
    audio = tmp_path / "media" / "audio.source.m4a"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"fixture")
    request = {
        "request_id": "alr-fixture",
        "work_dir": ".work/audio-localization",
        "source": {
            "audio": "media/audio.source.m4a",
            "subtitle": None,
            "asr_output": "subtitles/source.srt",
        },
        "target": {
            "audio": "media/audio.zh-CN.m4a",
            "script": "localization/script.zh-CN.json",
            "report": "localization/report.json",
        },
        "config": {"asr_model": "qwen3-asr-flash"},
    }
    monkeypatch.setattr(pyvideotrans_pipeline, "_load_request", lambda package: request)
    monkeypatch.setattr(
        pyvideotrans_pipeline.FFmpegMediaRunner, "duration", lambda self, path: 1.0
    )
    captured = []

    def stop_after_stt(*arguments):
        captured.extend(arguments)
        raise RuntimeError("stop after command capture")

    monkeypatch.setattr(pyvideotrans_pipeline, "_run_bridge", stop_after_stt)

    with pytest.raises(RuntimeError, match="command capture"):
        pyvideotrans_pipeline.localize_package(tmp_path)

    model_index = captured.index("--model_name")
    assert captured[model_index + 1] == "qwen3-asr-flash"
