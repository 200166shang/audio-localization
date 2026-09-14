"""FFmpeg boundary for probing, ASR preparation, and timeline rendering."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from pathlib import Path

from .errors import ConfigurationError


class FFmpegMediaRunner:
    def __init__(self, ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe") -> None:
        self.ffmpeg, self.ffprobe = ffmpeg, ffprobe

    def duration(self, media: Path) -> float:
        try:
            result = subprocess.run(
                [
                    self.ffprobe,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "json",
                    str(media),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            raise ConfigurationError(f"cannot probe media: {media}") from exc
        value = float(json.loads(result.stdout)["format"]["duration"])
        if value <= 0:
            raise ConfigurationError(f"media has no positive duration: {media}")
        return value

    def split_for_asr(
        self, audio: Path, output: Path, *, seconds: int, bitrate: str
    ) -> list[Path]:
        output.mkdir(parents=True, exist_ok=True)
        pattern = output / "chunk-%04d.mp3"
        self._run(
            [
                self.ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(audio),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-b:a",
                bitrate,
                "-f",
                "segment",
                "-segment_time",
                str(seconds),
                "-reset_timestamps",
                "1",
                str(pattern),
            ]
        )
        chunks = sorted(output.glob("chunk-*.mp3"))
        if not chunks:
            raise ConfigurationError("ffmpeg produced no ASR chunks")
        if any(path.stat().st_size > 5 * 1024 * 1024 for path in chunks):
            raise ConfigurationError(
                "ASR chunk exceeds 5MB; reduce chunk duration or bitrate"
            )
        return chunks

    def render_segment(
        self,
        audio: Path,
        output: Path,
        *,
        leading_silence: float,
        slot_duration: float,
        speed_ratio: float,
    ) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        filters = []
        if speed_ratio > 1.0001:
            filters.append(f"atempo={speed_ratio:.6f}")
        if leading_silence > 0.0005:
            delay = round(leading_silence * 1000)
            filters.append(f"adelay={delay}:all=1")
        filters.extend(
            ["apad", f"atrim=duration={leading_silence + slot_duration:.6f}"]
        )
        self._run(
            [
                self.ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(audio),
                "-af",
                ",".join(filters),
                "-ar",
                "24000",
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                str(output),
            ]
        )

    def concatenate(
        self, inputs: Sequence[Path], output: Path, *, duration: float
    ) -> None:
        if not inputs:
            raise ConfigurationError("timeline contains no audio segments")
        output.parent.mkdir(parents=True, exist_ok=True)
        list_file = output.with_suffix(".concat.txt")
        partial = output.with_name(f".{output.name}.partial.m4a")
        try:
            lines = [
                "file '" + str(path.resolve()).replace("'", "'\\''") + "'"
                for path in inputs
            ]
            list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
            self._run(
                [
                    self.ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(list_file),
                    "-af",
                    f"apad,atrim=duration={duration:.6f},loudnorm=I=-16:TP=-1.5:LRA=11",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "192k",
                    str(partial),
                ]
            )
            partial.replace(output)
        finally:
            list_file.unlink(missing_ok=True)
            partial.unlink(missing_ok=True)

    @staticmethod
    def _run(command: list[str]) -> None:
        try:
            subprocess.run(command, check=True, capture_output=True)
        except FileNotFoundError as exc:
            raise ConfigurationError("ffmpeg is required") from exc
        except subprocess.CalledProcessError as exc:
            message = exc.stderr.decode("utf-8", "replace")[-500:]
            raise ConfigurationError(f"ffmpeg failed: {message}") from exc
