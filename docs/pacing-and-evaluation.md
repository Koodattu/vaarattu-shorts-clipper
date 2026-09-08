# Pacing, GPU encoding and selection comparisons

Updated 8 September 2026: selection version `conversation-v10`, timestamp pacing version 2. Nothing starts automatically: the user starts/restarts the application, processing, comparisons and renders.

## New runs and existing clips

New runs default to **NVIDIA GPU** encoding and **Shorten long pauses between words**. The three-second minimum applies to selection, manual edits and rendering; five seconds is a preference, so complete three- and four-second clips remain possible. New selections apply quality gates and a ranked queue of at most ten clips; see [selection design](selection-design.md).

Saved runs retain their saved settings. Older runs without these fields retain CPU encoding and original pacing, including when recovering exclusions. To update an existing clip, select **NVIDIA GPU** and **Shorten pauses** in the review queue, then **Re-render**. Layout selection is optional; leaving **Current layout** retains the clip's composition. The editor has equivalent controls, plus source timing and caption edits. Disable pause trimming to render the contiguous version again.

Every change queues a new revision and resets that revision's human review. Existing exports remain on disk. **Previous revision** opens the prior preview; the editor's original recording always has uninterrupted source pacing. Revision-specific artifact URLs now actually serve the requested saved revision rather than silently substituting the latest file.

## Timestamp-based pacing

When **Shorten pauses** is enabled, gaps of at least 1.5 seconds between transcript word spans are shortened to 0.6 seconds: 300 ms after the preceding word and 300 ms before the following word. Occupied spans are merged first, including nested/overlapping words. Leading/trailing intervals, short gaps and gaps intersecting known transcript timing conflicts remain intact. Edits that would reduce output below three seconds are abandoned.

This explicitly replaces the old -50 dB acoustic silence requirement. Background music or game audio no longer prevents a gap cut. No audio detector, extra transcription, new model or dependency is involved. Every supplied word is preserved and caption timing is mapped to the shortened output. Speech missed entirely by ASR, inaccurate word timestamps, laughter or a deliberate dramatic pause can still require manual inspection; turn trimming off to retain original pacing. Five-millisecond fades occur only at the cut seams.

The four creator-named examples were replayed as offline edit plans with every word retained: neighbour 51.77→22.92s, CPU 64.91→47.00s, Stormreaver 67.37→61.68s, anime/manga 54.87→31.98s. These are planned durations, not newly rendered VOD clips. The anime clip's saved settings had trimming disabled. Enable trimming when rerendering it.

Canonical source word times and clip start/end stay unchanged in clip metadata. `pacing.json` records retained source intervals, their output offsets, removed intervals and output duration. Video timestamps are compressed in one pass; audio intervals use the same cuts. Avoiding repeated A/V concat padding prevents one frame of audio padding accumulating at each seam. Captions are generated from retimed copies. `captions.words.json` now uses output-relative timing, with `captions.source.words.json` retaining the original source words. API `words` still uses canonical source timing. Queue/gallery duration displays use the edited duration.

Rendered output still goes through decode, dimensions, duration, A/V start and duration checks. The scene scan covers the whole original interval. A technical failure holds the clip; it does not silently remove speech or switch encoders. Filter documentation: [FFmpeg](https://ffmpeg.org/ffmpeg-filters.html#concat).

## NVIDIA encoding and comparison

Final composition supports `h264_nvenc` (`p5`, high-quality tuning, VBR/CQ20) and the previous `libx264` (`medium`, CRF20). Both deliver H.264/AAC, 1080×1920, 30 fps, yuv420p. Section acquisition/alignment is unchanged. CPU filters remain CPU work, and CQ20 is not a measured quality equivalent of CRF20. Choose CPU if NVIDIA encoding is unavailable. [NVIDIA's FFmpeg guide](https://docs.nvidia.com/video-technologies/video-codec-sdk/13.1/ffmpeg-with-nvidia-gpu/index.html).

Run this yourself for a clip whose aligned source section is still saved:

```powershell
.venv/Scripts/python.exe -m vaarattu_shorts benchmark-render CLIP_ID
```

This makes separate CPU/GPU comparison files under `workdir/evaluations/encoders-*`, without queueing clips, changing reviews or overwriting exports. Both use the same source, layout, words and pacing settings. `comparison.json` records encoder time, output bytes, duration, warnings and explicit failures; each output passes the normal render validation. Inspect gameplay detail, captions and speech at cuts alongside the numbers. No real benchmark was run during implementation.

## Lean discovery and reasoning comparison

Discovery output no longer requests titles and summaries that verification generates again. It retains source anchors, category, short Finnish reason, scores, outcome and flags, plus concise section feedback. Saved historical full candidates remain recoverable. If verification cannot return valid boundaries, a valid original proposal is preserved for inspection; its short Finnish reason is used as a fallback title/summary. New review gates defer its unresolved verification rather than automatically rendering it.

OpenAI/Codex runs have separate **Discovery reasoning** and **Cut review reasoning** choices: low or medium. Both stay low by default until a comparison demonstrates useful improvement. Other providers keep their existing provider-specific behavior. Effort settings are snapshotted per run and included in request cache identity. New request artifacts include the actual request body—input, instructions, schema and settings—without authentication headers or endpoint URLs. They live alongside the existing response/usage artifacts and include source transcript content, so keep these project artifacts private.

For a controlled comparison, choose zero-based six-minute sections from a saved run. Include empty sections as well as sections with useful clips. Example for the audited age-guessing anecdote in section 0:

```powershell
.venv/Scripts/python.exe -m vaarattu_shorts compare-reasoning 3d29cbceb71146c98128cb797dc76a4c --section 0 --stage discovery
```

Repeat `--section N` to include more sections. Both efforts receive the same transcript, section cores and surrounding context. Discovery comparison varies discovery effort and holds verification at low. For `--stage verification`, the tool reuses the same saved proposals in the specified sections and varies only verification effort; it cannot discover new clips from an empty section.

These commands make model requests using the saved run's provider/model. They never render videos, queue production runs or alter reviews. API runs share one combined spending limit across both efforts (the saved limit, or `--budget N`); Codex subscription usage has no dollar cap. Outputs go to a separate evaluation directory/database. `manifest.json` records source/run/section identity, `comparison.json` records counts/time/usage, and each effort gets a `review.csv` with separate **would_post**, **cut_defect**, **needs_context** and **notes** columns. Not publishing a candidate is not automatically classified as a discovery mistake.

No live low/medium comparison was run during implementation; no quality winner has been chosen. [Luna model documentation](https://developers.openai.com/api/docs/models/gpt-5.6-luna).

## Verification

The 8 September full offline Python suite reported **236 passed and one pre-existing failure**: `test_codex_admission_needs_no_platform_key_and_snapshots_no_secrets` supplies an empty layout and fails the existing required-name check. Both that test and storage module exactly match the pre-change backup; neither was changed or skipped. Six Node UI behavior tests, Ruff lint and diff whitespace checks passed. Tests cover gate admission, comparative deferrals, exact ID validation, score fallback, overlap suppression, the ten-clip cap, cached comparisons, legacy exports, recovery capacity, best-first queue order, timestamp margins and caption retiming.

An actual FFmpeg test on synthetic media shortened 12 seconds to 7.2 seconds, producing 216 frames at 30 fps with audio and video both 7.200 seconds. Test artifacts live under `.cache/ranked-pacing-media/`; no production VODs were processed. The four named saved-word edit plans preserve all canonical word IDs. No app startup, live model request, VOD rerender or encoder benchmark was performed. Better ranking precision and audible cut quality on real VODs remain to be evaluated by the creator.
