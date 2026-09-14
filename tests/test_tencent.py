import pytest

from video_extract_skill.domain import LocalizationConfig
from video_extract_skill.errors import PermanentCloudError
from video_extract_skill.tencent import Credentials, TencentProvider


class StubSdk:
    def __init__(self) -> None:
        self.translate_attempts = 0

    def translate(self, text: str, source: str, target: str):
        self.translate_attempts += 1
        if self.translate_attempts == 1:
            raise RuntimeError("temporary")
        return {"text": "你好", "used_amount": 5}

    def synthesize(self, text: str, voice: int, sample_rate: int, speed: float):
        return "not-base64"


def test_credentials_repr_never_contains_secret() -> None:
    value = Credentials("id-visible-only-to-sdk", "super-secret")
    assert "super-secret" not in repr(value)
    assert "id-visible-only-to-sdk" not in repr(value)


def test_provider_retries_transient_failures() -> None:
    sdk = StubSdk()
    provider = TencentProvider(
        LocalizationConfig(), Credentials("id", "key"), sdk=sdk, sleep=lambda _: None
    )
    provider._next_translation_at = 0

    assert provider.translate("hello", source="en", target="zh") == ("你好", 5)
    assert sdk.translate_attempts == 2


def test_provider_rejects_invalid_tts_payload() -> None:
    provider = TencentProvider(
        LocalizationConfig(),
        Credentials("id", "key"),
        sdk=StubSdk(),
        sleep=lambda _: None,
    )

    with pytest.raises(PermanentCloudError, match="invalid audio"):
        provider.synthesize("hello", voice=501005, sample_rate=24000, speed=0)
