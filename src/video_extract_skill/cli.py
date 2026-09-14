"""Command-line interface kept deliberately thin for Skill invocation."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, replace
from pathlib import Path

from .domain import LocalizationConfig
from .errors import LocalizationError
from .pipeline import localize_audio


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(
        prog="video-localize",
        description="Localize authorized English audio to Mandarin with Tencent Cloud",
    )
    command.add_argument("--audio", required=True, type=Path)
    command.add_argument(
        "--subtitle", type=Path, help="existing English SRT; skips ASR"
    )
    command.add_argument("--output", required=True, type=Path)
    command.add_argument("--voice", type=int, default=501005)
    command.add_argument("--tts-concurrency", type=int, default=3)
    command.add_argument("--translation-concurrency", type=int, default=5)
    command.add_argument("--no-diarize", action="store_true")
    return command


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if not 1 <= args.tts_concurrency <= 20:
        parser().error("--tts-concurrency must be between 1 and 20")
    if not 1 <= args.translation_concurrency <= 20:
        parser().error("--translation-concurrency must be between 1 and 20")
    config = replace(
        LocalizationConfig(),
        default_voice=args.voice,
        tts_concurrency=args.tts_concurrency,
        translation_concurrency=args.translation_concurrency,
        diarize=not args.no_diarize,
    )
    try:
        result = localize_audio(
            args.audio, args.output, subtitle=args.subtitle, config=config
        )
    except (LocalizationError, FileNotFoundError, ValueError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 1
    payload = asdict(result)
    payload.update(
        {
            "status": "complete",
            "output": str(result.output),
            "report": str(result.report),
        }
    )
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
