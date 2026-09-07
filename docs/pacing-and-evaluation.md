# Pacing, GPU encoding and selection comparisons

Implemented 7 September 2026, selection version `conversation-v8`. Nothing starts automatically: the user starts/restarts the application, processing, comparisons and renders.

## New runs and existing clips

New runs default to **NVIDIA GPU** encoding and **Shorten long quiet pauses**. The three-second minimum applies to selection, manual edits and rendering; five seconds is a preference, so complete three- and four-second clips remain possible. Editorial verdicts, scores and flags remain advisory.

Saved runs retain their saved settings. Older runs without these fields retain CPU encoding and original pacing, including when recovering exclusions. To update an existing clip, select **NVIDIA GPU** and **Shorten pauses** in the review queue, then **Re-render**. Layout selection is optional; leaving **Current layout** retains the clip's composition. The editor has equivalent controls, plus source timing and caption edits. Disable pause trimming to render the contiguous version again.

Every change queues a new revision and resets that revision's human review. Existing exports remain on disk. **Previous revision** opens the prior preview; the editor's original recording always has uninterrupted source pacing. Revision-specific artifact URLs now actually serve the requested saved revision rather than silently substituting the latest file.

## Conservative pause cuts

An internal gap must satisfy both conditions:

- No recognized word occupies it. Nested and overlapping word intervals are merged before finding gaps; disputed transcription timing regions are protected.
- FFmpeg detects at least two continuous seconds below **−50 dB**, retaining all audio channels rather than downmixing. A transcript gap alone never authorizes a cut.

Half a second is retained at each end of the quiet interval. Short gaps, sounds that interrupt a quiet interval, leading/trailing silence and uncertain regions remain intact. Edits that would reduce output below three seconds are abandoned. Five-millisecond audio fades occur only at the quiet cut seams.

This is deliberately conservative silence detection, not a semantic speech detector. Background music/game audio can prevent an otherwise useful pause cut; missed very quiet speech and dramatic/comedic pauses still need listening review. It does not remove spoken tangents or try to repair ASR text. No automatic quality claim is made without listening to actual outputs.

Canonical source word times and clip start/end stay unchanged in clip metadata. `pacing.json` records retained source intervals, their output offsets, removed intervals and output duration. Video timestamps are compressed in one pass; audio intervals use the same cuts. Avoiding repeated A/V concat padding prevents one frame of audio padding accumulating at each seam. Captions are generated from retimed copies. `captions.words.json` now uses output-relative timing, with `captions.source.words.json` retaining the original source words. API `words` still uses canonical source timing. Queue/gallery duration displays use the edited duration.

Rendered output still goes through decode, dimensions, duration, A/V start and duration checks. The scene scan covers the whole original interval. A technical failure holds the clip; it does not silently remove speech or switch encoders. Filter documentation: [FFmpeg](https://ffmpeg.org/ffmpeg-filters.html#silencedetect).

## NVIDIA encoding and comparison

Final composition supports `h264_nvenc` (`p5`, high-quality tuning, VBR/CQ20) and the previous `libx264` (`medium`, CRF20). Both deliver H.264/AAC, 1080×1920, 30 fps, yuv420p. Section acquisition/alignment is unchanged. CPU filters remain CPU work, and CQ20 is not a measured quality equivalent of CRF20. Choose CPU if NVIDIA encoding is unavailable. [NVIDIA's FFmpeg guide](https://docs.nvidia.com/video-technologies/video-codec-sdk/13.1/ffmpeg-with-nvidia-gpu/index.html).

Run this yourself for a clip whose aligned source section is still saved:

```powershell
.venv/Scripts/python.exe -m vaarattu_shorts benchmark-render CLIP_ID
```

This makes separate CPU/GPU comparison files under `workdir/evaluations/encoders-*`, without queueing clips, changing reviews or overwriting exports. Both use the same source, layout, words and pacing settings. `comparison.json` records encoder time, output bytes, duration, warnings and explicit failures; each output passes the normal render validation. Inspect gameplay detail, captions and speech at cuts alongside the numbers. No real benchmark was run during implementation.

## Lean discovery and reasoning comparison

Discovery output no longer requests titles and summaries that verification generates again. It retains source anchors, category, short Finnish reason, scores, outcome and flags, plus concise section feedback. Saved historical full candidates remain recoverable. If verification cannot return valid boundaries, a valid original proposal remains reviewable; its short Finnish reason is used as a fallback title/summary.

OpenAI/Codex runs have separate **Discovery reasoning** and **Cut review reasoning** choices: low or medium. Both stay low by default until a comparison demonstrates useful improvement. Other providers keep their existing provider-specific behavior. Effort settings are snapshotted per run and included in request cache identity. New request artifacts include the actual request body—input, instructions, schema and settings—without authentication headers or endpoint URLs. They live alongside the existing response/usage artifacts and include source transcript content, so keep these project artifacts private.

For a controlled comparison, choose zero-based six-minute sections from a saved run. Include empty sections as well as sections with useful clips. Example for the audited age-guessing anecdote in section 0:

```powershell
.venv/Scripts/python.exe -m vaarattu_shorts compare-reasoning 3d29cbceb71146c98128cb797dc76a4c --section 0 --stage discovery
```

Repeat `--section N` to include more sections. Both efforts receive the same transcript, section cores and surrounding context. Discovery comparison varies discovery effort and holds verification at low. For `--stage verification`, the tool reuses the same saved proposals in the specified sections and varies only verification effort; it cannot discover new clips from an empty section.

These commands make model requests using the saved run's provider/model. They never render videos, queue production runs or alter reviews. API runs share one combined spending limit across both efforts (the saved limit, or `--budget N`); Codex subscription usage has no dollar cap. Outputs go to a separate evaluation directory/database. `manifest.json` records source/run/section identity, `comparison.json` records counts/time/usage, and each effort gets a `review.csv` with separate **would_post**, **cut_defect**, **needs_context** and **notes** columns. Not publishing a candidate is not automatically classified as a discovery mistake.

No live low/medium comparison was run during implementation; no quality winner has been chosen. [Luna model documentation](https://developers.openai.com/api/docs/models/gpt-5.6-luna).

## Verification

The full offline Python suite reported **225 passed and one pre-existing failure**: `test_codex_admission_needs_no_platform_key_and_snapshots_no_secrets` supplies an empty layout and fails the existing required-name check. The test and storage module exactly match the pre-change backup; this unrelated failure was not changed or skipped. Five Node UI behavior tests and Ruff lint passed. The seven focused pacing tests passed again after final cleanup. CLI help for both comparison commands was verified. No app startup, live model request, real media render, benchmark, or browser visual inspection was performed.
