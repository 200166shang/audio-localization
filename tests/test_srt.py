from pathlib import Path

import pytest

from video_extract_skill.srt import parse_srt


def test_parse_srt_accepts_bom_multiline_and_dot_timestamp(tmp_path: Path) -> None:
    source = tmp_path / "source.srt"
    source.write_text(
        "\ufeff1\n00:00:00,250 --> 00:00:01,500\nHello\nworld\n\n"
        "2\n00:00:02.000 --> 00:00:03.125\nAgain\n",
        encoding="utf-8",
    )

    cues = parse_srt(source)

    assert [(c.id, c.start, c.end, c.text) for c in cues] == [
        ("1", 0.25, 1.5, "Hello world"),
        ("2", 2.0, 3.125, "Again"),
    ]


def test_parse_srt_rejects_block_without_text(tmp_path: Path) -> None:
    source = tmp_path / "bad.srt"
    source.write_text("1\n00:00:00,000 --> 00:00:01,000\n", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid SRT block"):
        parse_srt(source)
