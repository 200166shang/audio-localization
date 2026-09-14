# video-extract-skill

An independent Codex skill and reviewable Python pipeline for localizing authorized
English audio into a timeline-aligned Mandarin audio track with Tencent Cloud.

The cloud boundary is deliberately small: Tencent Cloud ASR, TMT, and TTS provide
all model inference. Local work is limited to parsing, caching, and FFmpeg media
assembly; there is no local ML fallback.

## Install

Requirements: Python 3.11+, FFmpeg/FFprobe, and enabled Tencent Cloud ASR, TMT,
and TTS services.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[tencent]'
export TENCENTCLOUD_SECRET_ID='...'
export TENCENTCLOUD_SECRET_KEY='...'
```

Never place credentials in command-line flags, repository files, or generated
reports. Rotate any key that has been pasted into chat or committed elsewhere.

## Run

Use an existing English SRT to skip ASR and minimize elapsed time and API cost:

```bash
video-localize \
  --audio /absolute/path/source.m4a \
  --subtitle /absolute/path/transcript.srt \
  --output /absolute/path/artifacts/run-001
```

Omit `--subtitle` to use Tencent Cloud ASR. The default first pass uses male voice
`501005`, translation concurrency 5, TTS concurrency 3, and rejects segments that
would require more than 1.35x time compression.

Outputs:

- `audio/podcast.zh-CN.m4a`: final pure-Mandarin track
- `localization/script.zh-CN.json`: traceable localized script
- `localization/report.json`: timing, usage, cache, and provenance report
- `.work/tencent/`: content-addressed resumable cache

## Develop

```bash
pip install -e '.[test,tencent]'
pytest
```

Architecture and implementation decisions are tracked in
[Issue #1](https://github.com/200166shang/video-extract-skill/issues/1).

The acceptance sample (33:52 with an existing SRT) completed in 128.55 seconds on
2026-09-14, producing 177 timeline-aligned segments. Actual time varies with Tencent
Cloud latency, account quotas, and the host's FFmpeg performance.
