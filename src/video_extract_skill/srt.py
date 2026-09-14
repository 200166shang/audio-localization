"""Small, strict SRT parser used by the cloud pipeline."""

from __future__ import annotations

import re
from pathlib import Path

from .domain import Cue

_TIMING = re.compile(
    r"(?P<start>\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*"
    r"(?P<end>\d{2}:\d{2}:\d{2}[,.]\d{3})"
)


def _seconds(value: str) -> float:
    hours, minutes, rest = value.replace(",", ".").split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(rest)


def parse_srt(path: Path) -> list[Cue]:
    blocks = re.split(r"\r?\n\s*\r?\n", path.read_text(encoding="utf-8-sig").strip())
    cues: list[Cue] = []
    for position, block in enumerate(blocks, 1):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        timing_index = next(
            (i for i, line in enumerate(lines) if _TIMING.fullmatch(line)), -1
        )
        if timing_index < 0 or timing_index + 1 >= len(lines):
            raise ValueError(f"invalid SRT block {position}")
        match = _TIMING.fullmatch(lines[timing_index])
        assert match
        cue_id = lines[0] if timing_index else str(position)
        text = " ".join(lines[timing_index + 1 :]).strip()
        cues.append(Cue(cue_id, _seconds(match["start"]), _seconds(match["end"]), text))
    if not cues:
        raise ValueError("SRT contains no cues")
    return cues
