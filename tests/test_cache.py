from pathlib import Path

from video_extract_skill.cache import Cache, key


def test_cache_key_is_stable_and_writes_round_trip(tmp_path: Path) -> None:
    first = key("translation", {"text": "hello", "target": "zh"})
    second = key("translation", {"target": "zh", "text": "hello"})
    cache = Cache(tmp_path)

    cache.write_json("translation", first, {"text": "你好"})
    audio = cache.write_audio(first, b"a" * 101)

    assert first == second
    assert cache.read_json("translation", first) == {"text": "你好"}
    assert audio.read_bytes() == b"a" * 101
