# Research and reference-project audit

Research date: 2026-09-06. Sources below are primary project documentation/model cards, inspected local source, and direct public metadata. Recommendations are design judgments; no Vaarattu ASR/LLM benchmark was run in this pass. Model names, prices and downloader requirements must be rechecked when implementation starts.

## Evidence gathered on the actual environment

- Target PC: Intel i7-13700K, 16 cores/24 logical processors; 63.8 GiB RAM; NVIDIA RTX 4090 with 24,564 MiB reported VRAM, driver 610.88. Read-only system queries were used, not a model benchmark.
- C: free space was approximately 356.5 GiB; E: approximately 3,868.2 GiB. `uv`, `ffmpeg` and `ffprobe` were discoverable; their versions/capabilities were not certified. yt-dlp was not found on PATH; an existing binary in niilo22-dev was used for metadata only.
- The first five entries returned from [the channel](https://www.youtube.com/@VaarattuVODs/videos) were July 9, 8, 6, 5 and 4 VODs, with roughly 5.1–6.2-hour durations. This is an observed sample, not a complete archive census or proof of total hours.
- A full metadata request for [OJ-bDXbfEos](https://www.youtube.com/watch?v=OJ-bDXbfEos) confirmed channel ID `UCUCV40VqBZqt83afjbbICvw`, upload date 2026-07-16, title date 2026-07-09, duration 22,440 seconds and `not_live` status. Metadata may change; actual processing must revalidate it.
- Direct HTTP requests to [the stream list](https://dev.vaarattu.tv/api/streams?page=1&limit=100) returned 199 records across two pages. The July 9 VOD has a likely identity match to stream 254. The live activity route tested for stream 277 returned `Route not found`; local source contains the route. Details and limitations are in [stream-data.md](stream-data.md).
- No full audio, video section, model weights or private chat data was downloaded. No credentials or private configuration values were read. No API inference was purchased.

## Reference audit: reuse small techniques, not old product assumptions

All paths in this table are relative to the named reference root. Documents in those projects were treated as descriptions of their own state, not instructions to deploy, migrate, import or inherit their architectures.

| Reference root | Inspected evidence | Useful technique | Limitation / avoid carrying forward |
|---|---|---|---|
| `C:/Users/Juha/Desktop/Projektit/quick-transcribe` | `transcribe.py`, `requirements.txt` | Minimal faster-whisper Turbo CUDA/FP16 invocation; VAD; local model cache | Writes plain text only, losing timestamps/confidence. Automatic language detection is not the chosen Finnish default |
| `E:/Projektit/niilo22-dev` | `download.py`, `transcribe.py` | YouTube inventory concept; `BatchedInferencePipeline` with `language=fi`, word timestamps, VAD and batch size 16 | Download order is oldest-first, contrary to this project. Completion is a filename/log convention; YouTube ID extraction via `split('_')[2]` breaks for valid IDs containing underscores. MP3 encode/client fallback/cache deletion policies should not be copied |
| `C:/Users/Juha/Desktop/Projektit/loonclipper` | `README.md`, `ClipperRunner.cs` | yt-dlp time-range arguments, FFprobe validation, FFmpeg waveform/normalization, safe argument lists and temporary outputs | Actual supported product is public Twitch audio-to-MP3, despite the initial user description mentioning YouTube. It is not proof of YouTube video-range behavior. Requested start alone does not verify exact source origin |
| `C:/Users/Juha/Desktop/Projektit/video-generator` | `src/video_generator/media.py`, caption tests and ADRs 0008/0011 | `write_srt`, `write_ass`, cue grouping, word timing/QC, single caption track, temporary local model process pattern | Its script/TTS-led narration timeline is different: recorded VOD speech is our authority. Do not import multi-backend story/image/Remotion orchestration |
| `C:/Users/Juha/Desktop/Projektit/highlight-clipper` | `analysis/retrieval.py`, `analysis/ranking.py`, `workflows/analyze.py`, `evaluator_schema.py`, `timebase.py`, `docs/implementation-status.md` | Stable evidence/word anchors, explicit timebase, bounded original context, overlap suppression, process cleanup and resumable artifacts | Broad retrieval generators, embeddings, generalized creator profiles and full-media import are not necessary here. Runtime/schema smoke checks do not prove that its selected highlights are useful |
| `C:/Users/Juha/Desktop/Projektit/vaarattu.tv` | Stream routes/service/activity utility, Prisma schema, collector message timestamps, stream-recording docs | Existing stream inventory, per-minute distinct authors/messages, audience sample distinction | No YouTube identity field; live activity rollout missing at the tested URL; coarse charts and collector receipt time limit precision |

### What the highlight-clipper evidence actually says

Its own implementation-status document explicitly leaves corpus-based quality promotion pending. It records one real pre-v7 evaluator run with zero proposals and a later 87.7-second smoke with 20,953 input tokens, one repair and two proposals, reusing ASR. The document correctly says these are integration results, not cold full-pipeline throughput, Finnish accuracy, highlight recall or a model winner.

This does not prove a single root cause for the user's poor results. It identifies a missing quality-validation step and several hypotheses worth avoiding/testing: overly restrictive retrieval before semantic reading, topic words mistaken for interestingness, fragmented boundaries, out-of-context remarks, and extensive infrastructure preceding creator-quality evidence. The new design uses full speech coverage and simpler bounded selection first, then measures misses and false positives.

Do not copy old code wholesale or import those repositories at runtime. Adapt a narrow proven technique only after reading its implementation/test, checking licensing and fitting the new contract. Reference outputs and user edits stay untouched.

## Revised model research

The user's model/workflow revision is consolidated in [models-and-providers.md](models-and-providers.md), which is the canonical source for the local model shortlist, exact reference revisions, GPU lifecycle, ASR alternatives, provider capabilities and dated pricing. This replaces the initial small-Qwen/Gemini Flash-Lite recommendation; those were untested proposals, not measured winners.

Additional local inspection covered highlight-clipper's `assets/model-catalog.json` and `docs/local-runtime.md`, and video-generator's `setup.py`, `docs/model-matrix.md`, historical model notes and ADR 0011. Both describe Gemma 4 profiles. Their Gemma 26B repository revisions differ, so the new project must pin one complete verified asset set. Catalogue support and historical smoke results do not prove Gemma 31B fit or Finnish clip-selection quality on this PC.

The revised local direction is Gemma 4 31B Q4 first, with 26B-A4B Q4 as a required comparison. Keep only one local model family resident: finish ASR, confirm its owned process exits, then load the LLM. The preference for a large fully GPU-resident LLM supersedes reserving memory for simultaneous Whisper residency.

For ASR, retain faster-whisper Turbo as the familiar baseline; compare large-v3, Finnish-NLP Whisper and NVIDIA Parakeet TDT 0.6B v3. NVIDIA's native NeMo-Speech.cpp Windows/CUDA path makes Parakeet a concrete alternative worth testing. whisper.cpp is also a runtime option, with timestamp quality requiring separate validation. Published Finnish recognition scores are not Vaarattu stream-speech scores.

## Cost and throughput planning

### Provider comparison, not measured billing

The [current API comparison](models-and-providers.md) covers Gemini 3.8 Flash, GPT-5.6 Luna, GLM-5.3-Flash, DeepSeek V4 Flash and the unresolved Meta Muse Spark 1.3 pricing/API contract. It includes primary sources, launch-discount expiry, time-of-day rates and an identical illustrative workload. GLM has the lowest verified standard uncached rates among the requested models; no Finnish quality or cost-per-useful-clip winner has been established.

Budget by actual tokenized Finnish text including timestamps, prompts, overlap, verification and repairs:

```text
cost_usd = input_tokens / 1,000,000 × applicable_input_rate
         + billable_output_tokens / 1,000,000 × applicable_output_rate
```

Use separate categories when a provider charges cache, long-context, reasoning or other usage differently. Estimate before Run from the selected provider's dated rate snapshot; reserve requests and checkpoint at the configured cap. No fixed dollar cap has been chosen and no paid inference has run. A cap suitable for one provider may be insufficient for another. Do not silently omit transcript windows when the budget runs out.

Local inference avoids API fees but consumes electricity and time on the streaming PC. Measure wall-plug or system power over actual runtime if comparing expense: `energy_kWh = average_power_kW × runtime_hours`. Do not compare a GPU's rated maximum power directly with measured API token costs. API mode can reduce local LLM GPU occupation while gaming; report latency and useful clips as well as dollars.

### Storage example, not measured download sizes

| Representation / assumption | Per hour | Six hours | 1,000 hours |
|---|---:|---:|---:|
| Compressed audio at 128 kbit/s | 57.6 MB | 345.6 MB | 57.6 GB |
| 16 kHz mono signed 16-bit PCM | 115.2 MB | 691.2 MB | 115.2 GB |
| Video at combined 6 Mbit/s | 2.7 GB | 16.2 GB | 2.7 TB |

Decimal MB/GB are used in this table. Actual formats vary and decoded float arrays can use more RAM than PCM files. Three 60-second candidates plus 20 seconds of total handles each represent 240 seconds of video, approximately **180 MB at 6 Mbit/s**, before transport/seek overhead. This is why audio-first and range-only acquisition are valuable, but the real downloader experiment must establish the savings.

Measure ASR real-time factor (`wall_seconds / audio_seconds`), model prompt/output throughput and stage startup separately. End-to-end runtime includes download, decode, context verification and render. Reference smoke times must not be extrapolated into a claim about processing thousands of VOD hours.

## Research limits and next evidence

Not yet established: archive size/total hours, audio-only and video-range transfer behavior on actual media, ASR accuracy, local GGUF build/fit, best Finnish selector, real token consumption, source offset for stream 254, camera crop coordinates, background-music/speaker reliability, or creator acceptance rate.

These gaps are deliberate experiment inputs in [milestone 0 and the benchmark](implementation-plan.md), not hidden implementation TODOs. The architecture can be implemented now, but model/default promotion requires recorded evidence. No conclusion here depends on another project's deployment instructions having been executed.
