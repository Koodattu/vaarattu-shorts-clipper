# Video rendering, captions and delivery

Status: proposed first implementation. No VOD sections or finished shorts were rendered in this planning pass.

## Range acquisition and exact source timing

Download a candidate's interval plus ten seconds of handles on each side, clamped to the VOD bounds. Merge overlapping requested ranges from the same VOD when doing so reduces repeated transfer. Limit initial source quality to the best available at or below 1080p; do not download 4K just to create a cropped phone video without a measured benefit. Preserve the original full audio only as the discovery/alignment reference; use the downloaded section's matching audio for the finished video.

yt-dlp/FFmpeg range requests may retrieve surrounding keyframes or whole transport fragments. Some representations or servers may transfer more than requested. Track downloaded bytes and stop an unexpectedly unbounded fetch; **do not silently fall back to downloading the full VOD**. Refresh expired format URLs and retry within the job limits. Partial download feasibility is a milestone-0 experiment, not an assumption proven by metadata access.

Keep requested start/end, actual probed stream timestamps, selected format IDs, source dimensions/frame rate, acquisition tool versions and a verified mapping from the local section to canonical VOD time. Use FFprobe for streams/duration/PTS and compare distinctive audio from the section against the retained full audio at two positions. A bounded waveform correlation or equivalent audio match can estimate a fixed shift; silence/repetition with ambiguous matches must fail verification. If start/end matches disagree beyond tolerance, do not hide drift by moving captions.

```text
source_time_us = section_local_time_us + verified_section_origin_us
local_trim_start_us = clip_source_start_us - verified_section_origin_us
output_time_us = source_time_us - clip_source_start_us
```

These maps include container start-time normalization and codec delay handling. A requested `--download-sections` start value is not itself a measured section origin. If the request already produces correctly trimmed/normalized media, verify that fact and use it; do not subtract a second guessed offset.

Decode and re-encode the final exact cut. [FFmpeg's seeking documentation](https://ffmpeg.org/ffmpeg.html) explains why input seeks can begin at an earlier seek point and how transcoding with accurate seek discards that lead-in; stream copy can preserve it. Validate the chosen invocation with beginning/middle/end fixtures and actual VOD audio rather than relying on an apparently correct duration alone.

## Two-panel vertical layout

Default canvas: 1080×1920, square pixels, 30 fps constant output, H.264/yuv420p and AAC 48 kHz audio. Initial panel split: 1080×640 camera above 1080×1280 gameplay. The split is a starting visual choice, adjustable during preset calibration.

```text
┌──────────────────────────┐
│                          │
│       CAMERA PANEL       │  640 px
│                          │
├──────────────────────────┤
│                          │
│                          │
│     GAMEPLAY PANEL       │  1280 px
│                          │
│   readable captions      │
│                          │
└──────────────────────────┘
         1080 px
```

For each layout preset, save a normalized source camera rectangle and gameplay rectangle, plus per-panel fill/fit behavior and a caption-safe region. Resolve to even integer pixel dimensions after probing orientation/display size; reject out-of-bounds or too-small rectangles. Camera is fitted with preserved aspect ratio and padding when needed, so filling a wide top panel does not silently cut off the face. Gameplay normally fills its panel using an explicitly previewed center crop. Do not stretch either panel.

The source camera can be embedded in a corner of the game picture. Define the gameplay crop so it does not show a distracting second copy of the face. If this cannot be achieved without losing the useful scene, use a saved alternative layout. Face-off/BRB/fullscreen-browser scenes are not automatically valid camera presets.

Calibration workflow: extract representative frames from an acquired short section → drag camera/gameplay rectangles → see the vertical preview → save the preset for that VOD or a confirmed era of recordings. Recheck start/middle/end frames of each selected clip for scene changes. Geometric checks and scene-change heuristics cannot prove the rectangle contains Juha; bootstrap presets need actual visual review, and suspicious changes route to attention. A known preset can thereafter render automatically without per-clip approval.

Compile one deterministic filter graph: exact trim/reset timestamps → split the same decoded source → crop/scale/pad each panel → stack → render captions → encode. Trim/reset the corresponding audio against the same verified source interval. The [FFmpeg filter reference](https://ffmpeg.org/ffmpeg-filters.html) covers the necessary crop, scale, pad, stack, trim, timestamp and subtitle primitives. FFmpeg is sufficient; MoviePy, Remotion, a face tracker and a scene-generation engine are unnecessary for this template.

## Finnish captions

Burn readable Finnish captions into the primary `short.mp4` and also emit editable SRT plus a word-timing JSON artifact. One canonical caption track drives both. An optional clean MP4 can be added later if needed; do not double storage by default.

Start from the chosen words in the canonical transcript. Refine shortlisted audio with the selected accuracy profile when necessary, then verify correspondence against the original words/audio. Do not automatically accept every difference from a second ASR pass: changed negation, names, numbers or apparent speaker identity need attention. Keep raw transcript, proposed corrections and accepted caption text distinct. Manual edits create a new caption revision.

Refinement must respect the [GPU lifecycle](models-and-providers.md): unload the discovery/verifier LLM, load ASR once for all selected ranges requiring refinement, then unload ASR. Any subsequent text reverification reloads the LLM only after ASR exit. Routine captions may use the original word track without this extra phase. Never retain Whisper in memory during Gemma inference for the convenience of later caption work.

Rules for the first caption style:

- Preserve Finnish characters, spoken register and meaningful repetitions. Sentence casing/punctuation may improve readability, but do not rewrite the opinion or translate English game terms automatically.
- Group short phrases, typically 3–6 words, at most two lines. Finnish compound words make word count insufficient: use measured font widths inside the caption region and a provisional reading-speed ceiling around 20 characters/second.
- Prefer cues around 0.8–3 seconds where speech allows. Do not stretch a cue through a long silence to hit a minimum. Flag bursts that cannot remain readable without omitting speech.
- Use a bundled/licensed font with Finnish glyphs, high contrast, outline/shadow and a calibrated safe region away from the face, bottom controls and right-side controls. Platform overlays vary; inspect at phone size rather than claiming one universal safe margin.
- Phrase captions are the first baseline. Word highlighting via ASS is an optional style only after word timing proves good enough.
- Escape ASS control syntax in literal spoken text. Keep filter scripts/relative paths in a controlled working directory so Windows drive-colon and quoting issues do not corrupt subtitle arguments.

Convert timing only once: `caption_output_us = source_word_us - clip_source_start_us`. Clip cues to the final output interval, preserve order, and never include context words outside the cut. If a later editing feature removes pauses, it will require a new piecewise time map; this version does not remove them.

Use the small caption/QC techniques in video-generator as references, not its narrated-script pipeline. This project has recorded speech as the authority; there is no original TTS script to force ASR words to match.

## Audio treatment and editorial exceptions

Keep original delivery and the section's original mixed track. Apply measured two-pass loudness normalization conservatively (initial target −16 LUFS integrated, true peak ≤−1.5 dBTP) only after checking that gain does not make game noise distracting. Those targets are our starting delivery choices, not YouTube requirements. No voice synthesis, background music addition or automatic source separation in v1.

Music, a voice call or a played video can remain in the mixed recording. Speech confidence does not prove that the voice is Juha or that background music is clear for reuse. Flag apparent attribution/music problems for inspection; do not claim to solve them with volume normalization.

YouTube currently categorizes eligible square/vertical uploads up to three minutes as Shorts. Its help page also states that Shorts over one minute with an active Content ID claim are blocked globally. The 20–90-second default is therefore an editorial range, not a claim that 60 seconds is the maximum; longer music-bearing clips particularly need inspection before Juha publishes. See [YouTube Shorts guidance](https://support.google.com/youtube/answer/15424877?hl=en).

## QC and automatic ready-folder promotion

Before promoting a delivery:

1. Fully decode it and probe expected streams, dimensions, pixel format, frame rate, duration and seekability; reject truncated/empty/corrupt files and missing audio.
2. Verify requested source words are retained, start/end audio mapping is consistent, and A/V duration agreement is within 100 ms (initial tolerance). For known timing fixtures use tighter frame-based checks.
3. Check caption bounds, glyph rendering, line width, clipping, cue order and word coverage. Compare audible word onset to caption timing on the benchmark; an SRT parser passing alone does not establish sync.
4. Inspect sampled frames for invalid/black panels or likely scene changes. Emit a contact sheet for layout diagnosis. An automated pass does not certify face framing or editorial appeal.
5. Recheck editorial/context flags and current recipe revision. An obsolete render cannot overwrite the active clip.

Automatic promotion requires: the verifier's editorial gate passes; source timing and delivery QC pass; the assigned layout preset was calibrated for this source/era; and there is no detected scene/layout uncertainty. Preset mismatch, suspected camera-off/BRB/fullscreen/browser content or an uncalibrated camera/gameplay region becomes `needs_layout`, not ready. Speaker status must be `primary_verified`, or `primary_assumed` with the source-profile basis and absence of contrary evidence defined in [discovery.md](discovery.md); other/mixed/unknown outcomes cannot promote automatically. These are explicit technical/layout checks and bounded editorial judgments, not a guarantee that automation understands every frame or voice. Routine successful output requires no approval dialog. Material uncertainty produces a specific held state and fix action. Zero deliverable clips is preferable to padding the ready folder with broken ones.

Proposed delivery package:

```text
ready/<clip_id>/<revision>/
  short.mp4
  captions.srt
  captions.words.json
  metadata.json       # source ID/link/date, exact interval, title and revisions
  title.txt           # proposed Finnish title, faithful to the clip
```

Stage the whole package in a temporary directory and atomically rename it on the same volume after validation, then register it in SQLite. Recovery verifies/reconciles a package if the process died between rename and DB commit. Repeating an identical render is idempotent. Preserve previous exports; when an edit becomes current, remove the superseded revision from the active ready listing by moving/registering it under clip history, without deleting it. Already manually published records remain attached to their exact revision.

The single-video Run action advances through all these stages automatically and writes `ready/runs/<run_id>.json` as its output index. Include the selected VOD, completed/partial coverage, delivered package paths and held/rejected counts. A zero-clips result still gets a completed outcome/index; do not create empty fake MP4 packages. The configured output directory replaces the `ready/` root, and destination-side staging preserves atomic promotion across volumes. The UI exposes that run's results and Open output folder; it does not require candidate approval or start the next VOD.

The ready folder is an inventory, not an automatic publishing queue. The UI may suggest an order with topic variety and let Juha mark files published. A future one-per-day publication scheduler would be a separate feature requiring explicit publishing authorization and channel decisions.
