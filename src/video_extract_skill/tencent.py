"""Tencent Cloud API 3.0 adapter; SDK details stop at this module boundary."""

from __future__ import annotations

import base64
import json
import os
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .domain import Cue, LocalizationConfig
from .errors import ConfigurationError, PermanentCloudError, TransientCloudError

_PERMANENT_CODES = (
    "AuthFailure",
    "InvalidParameter",
    "MissingParameter",
    "UnsupportedOperation",
    "FailedOperation.UserNotRegistered",
)


@dataclass(frozen=True)
class Credentials:
    secret_id: str = field(repr=False)
    secret_key: str = field(repr=False)

    @classmethod
    def from_environment(cls) -> Credentials:
        secret_id = os.environ.get("TENCENTCLOUD_SECRET_ID", "").strip()
        secret_key = os.environ.get("TENCENTCLOUD_SECRET_KEY", "").strip()
        if not secret_id or not secret_key:
            raise ConfigurationError(
                "set TENCENTCLOUD_SECRET_ID and TENCENTCLOUD_SECRET_KEY"
            )
        return cls(secret_id, secret_key)


class TencentProvider:
    name = "tencent-cloud"

    def __init__(
        self,
        config: LocalizationConfig,
        credentials: Credentials | None = None,
        *,
        sdk: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self.credentials = credentials or Credentials.from_environment()
        self._sdk = sdk or _SdkFacade(self.credentials, config.region)
        self._sleep = sleep
        self._translation_lock = threading.Lock()
        self._next_translation_at = 0.0

    def translate(self, text: str, *, source: str, target: str) -> tuple[str, int]:
        self._throttle_translation()
        response = self._retry(lambda: self._sdk.translate(text, source, target))
        translated = str(response.get("text") or "").strip()
        if not translated:
            raise PermanentCloudError("Tencent translation returned empty text")
        return translated, int(response.get("used_amount") or len(text))

    def _throttle_translation(self) -> None:
        # TMT's entry-level quota is sensitive to bursts. Serialize only the
        # dispatch instant; response waits can still overlap across workers.
        with self._translation_lock:
            now = time.monotonic()
            wait = self._next_translation_at - now
            if wait > 0:
                self._sleep(wait)
            self._next_translation_at = time.monotonic() + 0.3

    def synthesize(
        self, text: str, *, voice: int, sample_rate: int, speed: float
    ) -> bytes:
        response = self._retry(
            lambda: self._sdk.synthesize(text, voice, sample_rate, speed)
        )
        try:
            audio = base64.b64decode(response, validate=True)
        except Exception as exc:
            raise PermanentCloudError("Tencent TTS returned invalid audio") from exc
        if len(audio) < 100:
            raise PermanentCloudError("Tencent TTS returned empty audio")
        return audio

    def transcribe(self, audio: Path, *, offset: float, diarize: bool) -> Sequence[Cue]:
        raw = audio.read_bytes()
        if len(raw) > 5 * 1024 * 1024:
            raise PermanentCloudError("ASR chunk exceeds Tencent's 5MB request limit")
        task_id = self._retry(
            lambda: self._sdk.create_asr(
                base64.b64encode(raw).decode(), len(raw), diarize
            )
        )
        delay = 0.5
        for _ in range(120):
            result = self._retry(lambda: self._sdk.describe_asr(task_id))
            status = str(result.get("status") or "")
            if status == "success":
                return tuple(_asr_cues(result.get("sentences") or [], offset))
            if status == "failed":
                raise PermanentCloudError(
                    "Tencent ASR task failed: "
                    + str(result.get("message") or "unknown")
                )
            self._sleep(delay)
            delay = min(delay * 1.5, 5.0)
        raise TransientCloudError("Tencent ASR polling timed out")

    def _retry(self, operation: Callable[[], Any]) -> Any:
        delay = 0.5
        for attempt in range(1, self.config.max_attempts + 1):
            try:
                return operation()
            except (PermanentCloudError, ConfigurationError):
                raise
            except Exception as exc:
                code = _error_code(exc)
                if code.startswith(_PERMANENT_CODES):
                    raise PermanentCloudError(
                        f"Tencent API rejected request: {code}"
                    ) from exc
                if attempt == self.config.max_attempts:
                    raise TransientCloudError(
                        f"Tencent API failed after {attempt} attempts: {code}"
                    ) from exc
                retry_delay = (
                    max(delay, 1.0)
                    if code.startswith("RequestLimitExceeded")
                    else delay
                )
                self._sleep(retry_delay)
                delay = min(delay * 2, 4.0)
        raise AssertionError("unreachable")


def _error_code(exc: Exception) -> str:
    getter = getattr(exc, "get_code", None)
    return str(getter() if callable(getter) else type(exc).__name__)


def _asr_cues(sentences: Sequence[dict[str, Any]], offset: float) -> list[Cue]:
    cues: list[Cue] = []
    for index, sentence in enumerate(sentences, 1):
        text = str(sentence.get("text") or "").strip()
        if not text:
            continue
        start = offset + float(sentence.get("start_ms") or 0) / 1000
        end = offset + float(sentence.get("end_ms") or 0) / 1000
        if end <= start:
            continue
        speaker = sentence.get("speaker_id")
        cues.append(
            Cue(
                f"asr-{offset:g}-{index}",
                start,
                end,
                text,
                f"speaker_{speaker}" if speaker is not None else "unknown",
            )
        )
    if not cues:
        raise PermanentCloudError("Tencent ASR returned no timed sentences")
    return cues


class _SdkFacade:
    def __init__(self, credentials: Credentials, region: str) -> None:
        try:
            from tencentcloud.asr.v20190614 import asr_client
            from tencentcloud.asr.v20190614 import models as asr_models
            from tencentcloud.common import credential
            from tencentcloud.common.common_client import CommonClient
            from tencentcloud.tts.v20190823 import models as tts_models
            from tencentcloud.tts.v20190823 import tts_client
        except ImportError as exc:
            raise ConfigurationError(
                "install the Tencent SDK with: pip install 'video-extract-skill[tencent]'"
            ) from exc
        cred = credential.Credential(credentials.secret_id, credentials.secret_key)
        self.asr = asr_client.AsrClient(cred, region)
        # Tencent removed TextTranslateRequest from recent generated TMT models.
        # CommonClient is still an official SDK client and keeps this adapter on
        # supported SDK signing/retry code while the live API remains available.
        self.tmt = CommonClient("tmt", "2018-03-21", cred, region)
        self.tts = tts_client.TtsClient(cred, region)
        self.asr_models, self.tts_models = asr_models, tts_models

    def translate(self, text: str, source: str, target: str) -> dict[str, Any]:
        response = self.tmt.call_json(
            "TextTranslate",
            {"SourceText": text, "Source": source, "Target": target, "ProjectId": 0},
        )["Response"]
        return {
            "text": response.get("TargetText"),
            "used_amount": response.get("UsedAmount"),
        }

    def synthesize(self, text: str, voice: int, sample_rate: int, speed: float) -> str:
        request = self.tts_models.TextToVoiceRequest()
        request.from_json_string(
            json.dumps(
                {
                    "Text": text,
                    "SessionId": f"video-localize-{time.time_ns()}",
                    "ModelType": 1,
                    "VoiceType": voice,
                    "Codec": "mp3",
                    "SampleRate": sample_rate,
                    "Speed": speed,
                    "Volume": 0,
                }
            )
        )
        return self.tts.TextToVoice(request).Audio

    def create_asr(self, data: str, data_len: int, diarize: bool) -> int:
        request = self.asr_models.CreateRecTaskRequest()
        request.from_json_string(
            json.dumps(
                {
                    "EngineModelType": "16k_zh_en_meeting" if diarize else "16k_en",
                    "ChannelNum": 1,
                    "ResTextFormat": 3,
                    "SourceType": 1,
                    "Data": data,
                    "DataLen": data_len,
                    "SpeakerDiarization": 1 if diarize else 0,
                }
            )
        )
        return int(self.asr.CreateRecTask(request).Data.TaskId)

    def describe_asr(self, task_id: int) -> dict[str, Any]:
        request = self.asr_models.DescribeTaskStatusRequest()
        request.from_json_string(json.dumps({"TaskId": task_id}))
        data = self.asr.DescribeTaskStatus(request).Data
        statuses = {0: "waiting", 1: "doing", 2: "success", 3: "failed"}
        sentences = []
        for item in getattr(data, "ResultDetail", None) or []:
            sentences.append(
                {
                    "text": getattr(item, "FinalSentence", ""),
                    "start_ms": getattr(item, "StartMs", 0),
                    "end_ms": getattr(item, "EndMs", 0),
                    "speaker_id": getattr(item, "SpeakerId", None),
                }
            )
        return {
            "status": statuses.get(getattr(data, "Status", -1), "unknown"),
            "message": getattr(data, "ErrorMsg", ""),
            "sentences": sentences,
        }
