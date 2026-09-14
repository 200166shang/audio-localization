---
name: tencent-video-localization
description: Localize authorized English video or audio into a timeline-aligned pure-Mandarin audio track using Tencent Cloud ASR, translation, and speech synthesis. Use this skill whenever the user asks to translate, dub, Chinese-localize, or generate a Mandarin podcast/audio track from media, especially when they mention Tencent Cloud, Chinese APIs, avoiding overseas APIs, existing SRT subtitles, speaker voices, or a five-minute processing target.
compatibility: Requires Python 3.11+, FFmpeg/FFprobe, Tencent Cloud ASR/TMT/TTS, and TENCENTCLOUD_SECRET_ID plus TENCENTCLOUD_SECRET_KEY in the environment.
---

# Tencent video localization

Use the deterministic Python pipeline in this repository. Do not substitute local
ML models: Tencent Cloud is the only inference backend; local execution only parses,
caches, and assembles media.

## Workflow

1. Confirm that the user owns or is authorized to process the input media.
2. Resolve absolute input and output paths. Prefer an existing English SRT because
   it skips ASR and is the fastest, least expensive route.
3. Check `ffmpeg`, `ffprobe`, Python 3.11+, and the two Tencent credential environment
   variables. Never print, persist, or pass credentials on the command line.
4. Install once with `pip install -e '.[tencent]'` from this repository.
5. Run:

   ```bash
   video-localize --audio /absolute/source.m4a \
     --subtitle /absolute/transcript.srt \
     --output /absolute/output-directory
   ```

   Omit `--subtitle` only when transcription is required. The defaults use male
   voice `501005`, TTS concurrency 3, translation concurrency 5, and speaker
   diarization when ASR runs.
6. Read `localization/report.json`. Completion requires a final audio duration within
   100 ms of the source, complete source-cue coverage, and no segment exceeding the
   1.35x compression cap.
7. Present the final audio and report as clickable absolute paths. State elapsed time,
   cache hits, translated/TTS character counts, and whether ASR was skipped.

If execution stops, rerun the same command and output directory; the content-addressed
cache resumes completed translation and TTS work. Do not delete `.work` unless the
user explicitly asks to discard the cache.

Read [references/tencent-cloud.md](references/tencent-cloud.md) when diagnosing
service enablement, quotas, permissions, or adapter behavior.
