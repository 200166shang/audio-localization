# Tencent Cloud adapter notes

The pipeline uses official Python SDK packages for three services:

- ASR (`asr.tencentcloudapi.com`): creates offline recognition tasks. Audio is
  converted locally to mono 16 kHz, 32 kbps MP3 chunks below the 5 MB request limit.
- TMT (`tmt.tencentcloudapi.com`): translates English source text to Simplified Chinese.
  Recent generated TMT model packages omit `TextTranslateRequest`, so the adapter uses
  the official SDK's `CommonClient` for signing and dispatch while keeping the same API.
- TTS (`tts.tencentcloudapi.com`): returns MP3 speech. The default voice is `501005`;
  `101001` is mapped to a second speaker when diarization is available.

Credential lookup is environment-only. Required variables:

```text
TENCENTCLOUD_SECRET_ID
TENCENTCLOUD_SECRET_KEY
```

When authentication, registration, parameter, or unsupported-operation errors occur,
stop instead of retrying. Transient SDK/network errors use bounded exponential retry.
API-specific code remains isolated in `src/video_extract_skill/tencent.py` so an API
version or product change does not leak into orchestration.

The report intentionally excludes credential material and raw provider responses.
For least privilege, use a dedicated Tencent Cloud sub-user restricted to the ASR,
TMT, and TTS calls required by this workflow.
