# VOD supercut: investigation and proposed design

19 September 2026. Planning only; no feature code or production state changed.

## Product scope and defaults

A separate Highlights workflow turns one complete recording into one 5-20 minute,
16:9, 1920x1080, 30 fps video without subtitles. The original full-frame picture,
facecam and game audio remain. Default editorial style: chronological stream
highlights with complete stories, jokes, reactions and speech-supported gameplay
sequences. No new narration, invented dialogue, music or decorative transitions.

Aim for roughly 10-12 minutes when the material supports it, with a 20-minute
ceiling. Five minutes is a quality-dependent target, not a reason to pad weak
footage. If a coherent result cannot reach five minutes, report insufficient
material and retain the shorter draft for optional review. Do not silently call
that a completed in-range video.

Normal interaction: select recording -> automatic analysis and editing -> review
720p draft -> approve and render final. Optional written feedback revises the draft
without rescanning the entire VOD. Intermediate plans remain inspectable but need
no routine user approval. Final export is not automatic social publication.

## Findings in this repository

- `youtube.py` already invokes yt-dlp, but metadata validation, canonical URLs,
  channel checks, IDs and acquisition are explicitly YouTube-specific. Its
  audio-only path also rejects a downloaded file containing a video stream;
  Twitch may require extracting audio from multiplexed media.
- `discover.py` scans six-minute cores with 90 seconds of surrounding context,
  splits oversized requests on passage boundaries and saves coverage. Its prompt,
  per-clip contracts, 20-candidate verification limit and ten-render limit are
  specific to shorts. Do not apply those editorial rules to highlights.
- `tighten.py` uses compact word references and deterministic validation to remove
  speech, protect neighboring words and retain edit history. Reuse the boundary
  principles, not its internal-only/six-cut clip contract.
- `pacing.py` shortens transcript gaps >=1.5 seconds to 0.6 seconds. That default
  would damage gameplay/tension in this feature. Share timeline arithmetic, not
  the automatic gap policy.
- `render.py` has useful source alignment, NVENC and technical checks, but its
  composition always crops into vertical panels and burns subtitles. Add a
  separate landscape renderer rather than modifying clip output behavior.
- Existing transcription, local GPU lifecycle, model request artifacts, bounded
  service retries, request caching and revision protection are useful shared
  components. Old architecture documents are partly historical; current worker
  code supports concurrent jobs and independent interactive review work.
- Earlier human review favored actual developed payoff and interesting substance;
  isolated remarks, routine coordination and truncated setups often failed.
  Those lessons transfer. A rejected standalone short can still supply essential
  context inside a longer sequence, so historical rejection is not a hard filter.

## Source identity and multipart recordings

Read-only inspection of the cached catalogue found 100 uploads, including four
multipart uploads forming two pairs:

| Recording | Part 1 | Part 2 |
|---|---|---|
| 28.2.2026 - peli lapi, oliko tassa enaa mitaan? | I96U85g0HCE, 42,584 seconds | lYWDZDfXFE8, 1,160 seconds |
| 27.2.2026 - testissa uusin wow killer (tama tappaa sen oikeasti) | PqwdySA_nNA, 42,901 seconds | uK4qijFPhH8, 3,971 seconds |

Titles actually end in `(Part 1/2)` and `(Part 2/2)`. Titles in the table are
ASCII-normalized for readability. This is cached catalogue evidence, not a full
channel audit or a new media download. Direct YouTube page retrieval failed during
research; current availability remains to be verified during implementation.

Introduce a recording manifest containing ordered source assets, each with
provider, provider ID, URL, owner, title, probed duration and part metadata. Keep
source IDs distinct from recording IDs. A Twitch VOD and its YouTube archive are
alternative representations; do not concatenate both merely because dates match.

For YouTube, strip only a recognized part suffix. Match channel, normalized base
title, recording date in the title and declared part count. Fetch missing siblings
from the channel catalogue when necessary; the current 100 cached items are not
assumed complete. Sort by part index, not upload order. Support other observed
formats with fixtures; do not guess whether an unseen 0/N convention means N or
N+1 pieces. Missing, duplicate or ambiguous parts need a visible resolution with
manual add/remove/reorder, not an incomplete video presented as the full stream.

Maintain per-asset local time plus a logical recording timeline. Check boundaries
for repeated audio/transcript or omitted footage. Deduplicate only confidently
matched overlap; an unknown gap stays a discontinuity. Never use summed advertised
durations as proof of alignment, and never cut seamlessly across an unknown seam.

Twitch MVP accepts a completed `twitch.tv/videos/<id>` URL using yt-dlp's existing
Twitch VOD extractor. Reject ongoing streams for this workflow. A later/optional
channel picker can use Twitch Get Videos with an app or user access token; that
endpoint provides metadata, not a full-VOD download URL. Muted/unavailable sections
must be represented as source limitations, not presumed speech-free filler.

References: [yt-dlp Twitch extractor](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/twitch.py),
[Twitch Get Videos](https://dev.twitch.tv/docs/api/reference/#get-videos),
[YouTube video metadata](https://developers.google.com/youtube/v3/docs/videos).

## Automatic editorial pipeline

1. Resolve and validate the full recording manifest. Reuse compatible cached
   transcripts only when source identity, version and timing are verified.
2. Acquire audio for every part, transcribe with the existing fast model, unload
   it, and preserve word timing/uncertainty. Keep original transcript immutable.
3. Scan all speech with token-bounded chunks and overlapping context. Start from
   the existing six-minute/90-second approach. Produce compact anchored beat
   records, not fully verified clip proposals: setup, development, payoff,
   interesting detail, candidate context dependencies, inferred event confidence
   and continuation into another chunk. Empty windows are legitimate.
4. Merge overlap duplicates and build a compact whole-recording map. Link setup
   and payoff across chunk boundaries and distant callbacks. Keep references to
   verbatim evidence: summaries guide retrieval but never authorize final cuts.
5. Select connected sequences for a provisional duration budget. Compare actual
   excerpt speech globally alongside dependencies; avoid ranking from persuasive
   discovery reasons or incomparable per-chunk scores. Reward complete payoff,
   personality, distinct content and momentum. Do not force coverage of every
   topic/hour, fill a candidate quota or assemble the ten existing shorts.
6. Retrieve detailed transcript only for selected sequences and needed context.
   Create a structured edit decision list (EDL): ordered source ranges, removed
   speech passages, explicit gap decisions and short reasons. The program owns
   times and duration calculation. The model references supplied IDs.
7. Independently review the assembled transcript in output order, including gap
   lengths, source jumps, neighboring removed passages and unresolved dependencies.
   Return specific anchored defects and patch operations, or pass.
8. Apply validated patches and review again, with at most two revision rounds
   after the first assembly. Stop on pass, no useful change, repeated plan hash,
   budget exhaustion or iteration limit. Retain the last valid plan and unresolved
   notes; do not label unresolved problems as model-approved.
9. Download/cache selected original-quality video windows with modest context
   handles, verify timing, and render a 720p draft from the validated EDL.
10. Run technical checks, present final human review, and on approval render 1080p
    from original-quality source windows using the identical EDL revision.

Scanning cost grows with source length. Detailed editing cost should grow with
selected footage, not every speculative moment. A starting application budget is
roughly 30-40 minutes of detailed candidate footage for a 10-12 minute draft,
including bounded reserve/context retrieval. This is a tuning hypothesis, not a
measured optimum. Record excluded candidates so recall can be audited. Group
related passages in requests instead of issuing a context request per tiny clip.
Request IDs, ranges, concise evidence and decisions; do not request rewritten
transcripts or long prose reasoning. No embeddings/vector database are needed
initially. Use the existing model provider, then compare settings on fixed VODs
before adopting a more expensive configuration across the pipeline.

## Edit contract, pauses and fidelity

Store an ordered list of retained source intervals with sequence IDs, source asset
IDs, anchor references, verified integer source times and deterministic output
offsets. Derive removed spans from that list so two conflicting timelines cannot
exist. Map every output frame/time back to its source. Speech boundaries resolve
from words/passages with protective margins; no invented model timestamps.

Also supply explicit gap IDs with duration and adjacent words. Gap decisions are
keep or shorten, optionally selecting from program-generated safe timing choices.
This lets the model retain non-speech footage without pretending it has word IDs.
Preserve gaps by default. Shorten only when surrounding evidence supports dead
time and the join preserves the sequence. Mark gameplay inferred from speech as
inferred, not visually observed. Do not introduce a volume-based silence detector.

A transcript gap means no recognized words, not proven silence. It can contain a
boss fight, laughter, an explosion, music or missed speech. Preserve uncertain
intervals, setup-to-reaction gaps and dramatic timing. Transcript-only discovery
will miss events without spoken clues; this is the central quality limitation.
Targeted visual/audio inspection is a future improvement if evaluation shows the
limitation matters, not an implicit capability in v1.

Validate IDs, source bounds, chronological order, duplicates, overlap, retained
word integrity, protected gaps, duration and reference freshness before rendering.
Validate the combined edit, not only each removal alone. A reviewer must be able
to restore context/remove a sequence/revise by instruction and undo to an earlier
EDL revision. Never reconstruct missing source content or rewrite spoken meaning.

Rendering during every model iteration is unnecessary: a text-only model learns
nothing new from an MP4 it cannot inspect. The editorial loop operates on the
exact assembled transcript/timeline; render once it stabilizes. Technical media
checks are separate and do not establish that an edit is entertaining or natural.

## Rendering, storage and operational behavior

Use existing yt-dlp/FFmpeg tools. Prefer coalesced selected source windows with
handles to downloading many tiny fragments or the full multi-hour 1080p VOD.
Persist actual downloaded-media-to-source mappings; requested timestamps are not
proof. Twitch audio-only/range support, HLS discontinuities and alignment need
live sample tests. If selective acquisition is unreliable, offer an explicit
full-source mode with disk estimate/limit; never silently download a huge VOD.

Draft: 1280x720, 30 fps, H.264/AAC, approximately 3-4 Mbps. Final: 1920x1080,
30 fps, h264_nvenc, H.264 high profile/yuv420p, variable bitrate starting around
10 Mbps (roughly 16 Mbps peak), AAC stereo 48 kHz 192 kbps, MP4 faststart.
Preserve aspect ratio with padding if required; no shorts crop or subtitles.
These bitrate choices are initial engineering defaults to inspect on gameplay.
YouTube's reference recommendation is 8 Mbps for SDR 1080p at 24/25/30 fps;
10 Mbps leaves some extra room for busy gameplay. A 20-minute output at 10 Mbps
plus 192 kbps audio is roughly 1.53 GB, excluding small container overhead.
[YouTube upload settings](https://support.google.com/youtube/answer/1722171?hl=en),
[NVIDIA FFmpeg guide](https://docs.nvidia.com/video-technologies/video-codec-sdk/13.1/ffmpeg-with-nvidia-gpu/index.html).

Cut both picture and audio using the same timeline. Normalize time bases, frame
rate, dimensions and audio layout across source parts. Use short audio edge fades
where needed; avoid automatic visual dissolves. Validate decode, dimensions,
frame rate, A/V timing and final duration, including high-cut-count fixtures.
Final rendering uses original sources, never an upscale of the draft. A changed
edit invalidates approval. Cache unchanged render segments if repeat-render cost
justifies it; avoid making this a prerequisite for the first version.

Separate Highlights jobs, revisions, review state, outputs and cleanup eligibility
from shorts. Reuse the scheduler/request lifecycle, but preserve interactive clip
review independence and shared GPU resource limits. Save every completed stage
and model response; retry transient failures with the existing backoff/timer.
Never repeat successful full transcription because one later request failed.
Retain referenced source windows until final approval/export so a Twitch source
expiring does not break final rendering. Shared cache cleanup must honor references
from both features. No dependency or orchestration service is proposed.

## Research and why this design

[Video Use](https://github.com/browser-use/video-use) demonstrates compact word-timed
text, structured EDLs and a bounded self-evaluation loop, with optional visual
composites at decision points. It is a useful implementation pattern, not evidence
that Finnish gaming VOD editing is solved.

[Prompt-Driven Agentic Video Editing](https://arxiv.org/abs/2509.16811) uses
hierarchical indexing and global narrative context for multi-hour material. Its
multimodal, often narrated recaps differ from this transcript-only supercut.
Borrow the anchored global/local representation, not the entire architecture or
its quality claims for our use case.

[LAVE](https://arxiv.org/abs/2402.10294) explores language-guided planning and direct
user refinement. It supports exposing reversible editorial decisions, but is not
a benchmark proving unattended gameplay highlight quality.

## Build order and acceptance evidence

1. Source/manifest layer: preserve current YouTube behavior; test known multipart
   pairs, missing/ambiguous parts and actual Twitch audio/video acquisition.
2. Offline planner on saved transcripts: produce beat map, selection, EDL and
   bounded critic loop without rendering or touching shorts reviews.
3. Landscape renderer and separate Highlights UI: resumable jobs, automatic draft,
   feedback/undo, final approval and 1080p export.
4. Evaluate several complete VODs: a conversation-heavy recording, gameplay-heavy
   recording, split recording and Twitch input. Reuse source material where useful,
   but assess full-video quality independently from historical short approvals.

Measure time/tokens per source hour, total calls/retries, candidate minutes vs final
minutes, first-draft approval, boring/missing-context passages, important omitted
moments, unnatural joins and useful gameplay gaps accidentally removed. Include
an assembly-without-revision baseline to establish whether the critic loop helps.
Regression checks must preserve shorts discovery/rendering and independent review.
No extra runtime dependency, live download, model replay or render was performed
for this planning investigation. Implementation and those acceptance checks remain
future work.
