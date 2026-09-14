"""Content-addressed, atomically written localization cache."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


def key(kind: str, payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        {"kind": kind, **payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


class Cache:
    def __init__(self, root: Path) -> None:
        self.root = root

    def json_path(self, namespace: str, cache_key: str) -> Path:
        return self.root / namespace / f"{cache_key}.json"

    def audio_path(self, cache_key: str) -> Path:
        return self.root / "tts" / f"{cache_key}.mp3"

    def read_json(self, namespace: str, cache_key: str) -> dict[str, Any] | None:
        path = self.json_path(namespace, cache_key)
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else None
        except (OSError, json.JSONDecodeError):
            return None

    def write_json(self, namespace: str, cache_key: str, value: dict[str, Any]) -> Path:
        path = self.json_path(namespace, cache_key)
        return _atomic_write(
            path, json.dumps(value, ensure_ascii=False, indent=2).encode()
        )

    def write_audio(self, cache_key: str, value: bytes) -> Path:
        if len(value) < 100:
            raise ValueError("synthesized audio is empty")
        return _atomic_write(self.audio_path(cache_key), value)


def _atomic_write(path: Path, value: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f".{path.name}.{os.getpid()}.partial")
    try:
        partial.write_bytes(value)
        partial.replace(path)
    finally:
        partial.unlink(missing_ok=True)
    return path
