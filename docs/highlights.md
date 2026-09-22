# Highlights workflow

Highlights is a separate landscape-video workflow. Open **Highlights** in the
navigation, or **Create highlight video** on a recording in Video library.
Restart the app and its normal worker together after installing this change.
No separate service or dependency is required.

## Starting a recording

Paste completed YouTube or Twitch VOD URLs, one per line, then choose **Load
recordings**. You can combine independent recordings from either platform in one
project. Give the project a name, such as the game you are collecting highlights
from. Each recording has an embedded preview, start/end timestamps, range sliders,
and buttons to preview either boundary. Enter times as H:MM:SS, MM:SS or seconds.
Choose one continuous range per recording, exclude recordings with their checkbox,
and use **Move earlier / Move later** to set the final source order.

For example, select the Diablo portion from three VODs for one project. After
starting it, reopen the creation form, change the project name and ranges, and
create another project for the other game. Choices remain in the form until you
leave or reload the page. Embeds retain their own playback controls and can play
past the selected end; processing stays inside the chosen range. If a provider
blocks embedding, open the original recording and enter its timestamps instead.

For a single YouTube URL, the optional part lookup matches titles ending in
`(Part 1/N)` through `(Part N/N)` (also `Osa`) using the configured channel and
normalized base title. It searches saved entries and up to six catalogue pages.
Disable this lookup to use only that URL. Missing or ambiguous parts can be entered
manually, one URL per line. Unrecognized/zero-based numbering is not guessed.

Only completed recordings are accepted. YouTube channel validation remains in
place. Twitch public VOD URLs use yt-dlp without browser cookies. Protected or
unavailable sources can fail acquisition; access restrictions are not bypassed.

Choose a model provider and encoder, then **Create highlight video**. Length
follows the worthwhile content. There is no runtime floor, ceiling, or scene
quota; weak material is not used to fill time.
Codex uses the configured bridge/model and account allowance; paid API providers
require a spending limit. Local providers reuse the existing model installation.

## Processing and review

- Retain full audio per recording for alignment, but transcribe only its chosen
  range with Turbo. Compatible, hash-verified audio and transcripts from shorts
  or completed highlight projects are copied into the new workspace. A transcript
  is reused only when it covers the requested range. Copies protect projects from
  cleanup of the originals. Transcripts and edits retain original VOD timestamps.
  Only words wholly inside the chosen range reach the editor, and rendering
  rejects retained sections outside those boundaries.
- Map source scenes in six-minute transcript cores with 90 seconds of context.
  Enjoyable commentary, personality and exchanges qualify without a standalone
  punchline. Routine chatter and filler do not. Low discovery reasoning is unchanged.
- Use Low thinking for new runs and rebuilds. Rate source speech cheaply before
  detailed editing. Every scene scoring at least 60/100 is eligible; none are
  excluded to meet a scene count or a preferred runtime.
- Edit up to four independent scenes per request, within the model context budget.
  Keep complete thoughts and reactions; remove spoken detours and repetition.
  No proportional allocation based on the unedited scene's length.
- Shorten verified gaps of at least 2.5 seconds to 0.8 seconds, with 0.4-second
  margins around speech. Short pauses remain natural. Timing uncertainty protects
  a gap from automatic cuts. No volume detector is used.
- The editor can explicitly preserve a gap with a retained evidence passage, or
  retain 1-15 seconds before the following reaction as an inferred gameplay lead-in.
  Unknown visual content alone does not justify retaining long waits. The model
  has not watched gameplay; inference can still be wrong and needs human review.
- Rate actual edited material in bounded batches, recording every score and reason.
  The configurable final floor defaults to 75/100; edits marked discard are also excluded. Assemble worthwhile
  scenes in source order, rejecting overlapping footage. Keep every worthwhile
  non-overlapping scene, regardless of the resulting duration.
- Review assembled speech, original neighboring passages, pacing and joins. Revise
  affected scenes once, re-score their changed edits, then review again. Stop
  when passed, unchanged or after the second review. This bounds review loops,
  not scene count or video length. Remaining notes are shown for human review.
- Fetch selected video windows with context handles, check their audio alignment
  against the full source audio, and render a 720p draft. Source parts retain their
  own clocks and never form one seamless cut across an unverified part boundary.
- Review playback. **Revise draft** edits selected scenes and reuses the saved
  scene pool. **Rebuild from full recording** maps and edits the full saved
  transcript again using current instructions, retaining the earlier draft for comparison.
  For a range-based project, rebuilding stays within its saved source selections.
  Revising an old highlights-v1/v2 draft also rebuilds selection because it lacks an
  edited scene pool. **Restore this draft** restores a saved revision, and future
  changes still get a new revision number. Saved final approvals always render their
  original plan without rerunning the editor.
- **Approve and render 1080p** renders the exact approved edit from original source
  windows. Approval does not schedule or publish anything. Download the video or
  its JSON edit plan from this tab. **Reject draft** records the review decision.

A transcript-only model cannot hear a clipped syllable, observe gameplay or know
that an untranscribed interval is silent. It sees gap durations and speech clues.
Human playback is still necessary, particularly for gameplay-heavy recordings.
Routine automatic volume-based silence cutting and final caption processing are
not part of this workflow. No subtitles are added.

## Isolation and recovery

Highlights uses `workdir/highlights/state.sqlite3`, `manifests/` and `runs/` under
that same highlights folder. It reuses the Store/request machinery, with its own
job admission, reviews, usage and artifacts. Highlights never creates a clip row
in the shorts database or enters its publishing pool. Shorts catalogue metadata
may be fetched during multipart resolution, but clip decisions are not modified.

The existing worker owns one additional Highlights execution slot. It does not
consume a shorts processing slot or block interactive review jobs. All local ASR
and LLM stages still respect the shared GPU lock; physical GPU/CPU/disk resources
are shared. Downloading and NVENC encoding can overlap other work.

Pause, cancel and resume are supported. Completed stages and model request results
are checkpointed with hashes. Transient service errors use existing timed retries.
Discovery and ranking use two timed repair attempts (5 and 15 seconds). Scene batches
and individual repairs use one timed retry (5 seconds). Each scene is validated
independently; valid batch neighbors are not regenerated when one scene is invalid.
Only failed/missing scenes receive individual repairs. Pause/cancel remains available.
Context-only proposals are left to their own section; sequences crossing a section
boundary may retain their setup, and overlapping proposals are deduplicated.
Discovery can salvage verified proposals with a warning. Incomplete rankings or
unrepaired new scene edits pause the job instead of silently losing material.
Three unrepaired scenes stop a batch sweep immediately. Cached successful batch
responses and individual repairs remain reusable. Failed pacing revisions retain
verified edits with visible warnings. Incomplete critique is listed beside the draft. Resume reuses completed transcription
and valid model results. Access, spending limits and unsafe cuts are never bypassed.
Approval verifies the saved edit and media hashes and renders directly without loading ASR or calling the LLM, even if prompts have since changed. User revisions
reuse the original transcription and, for episode drafts, the edited scene pool. Source windows
are retained for final rendering; no automatic cleanup is added in this version.

Rendering uses one 30 fps timeline for picture and sound. Intermediate MOV segments
keep exact video time bases and PCM audio; final assembly copies H.264 and encodes
AAC once, avoiding repeated AAC encoder padding at every join. Drafts are 720p;
finals are 1920x1080. NVENC defaults are VBR/CQ20 with 4 Mbps draft and 10 Mbps final
targets. CPU H.264 encoding is also available. Aspect ratio is preserved with padding.
All outputs undergo full decode, format, frame rate, duration and A/V sync checks.

## Validation (19 September 2026)

- New offline tests cover multipart discovery, unsafe source URLs, muxed Twitch
  audio extraction, passage/gap validation, pause preservation, bounded review,
  user revision, isolated state, stale approval, restore history, local API
  protection and resume/final rendering orchestration.
- Final targeted run: 29 backend tests passed; all 25 UI tests passed; touched Python files passed Ruff.
- UI tests cover automatic resolution without starting work, explicit approval,
  revision guidance and stable playback while status refreshes.
- Actual NVENC renders with 20 cuts passed at both 720p and 1080p: 200 frames,
  6.666667 seconds at exactly 30 fps, decode and A/V timing checks passed.
- Public Twitch VOD `2877568028` passed metadata lookup and a 30-second range
  acquisition test with both picture and audio (30.02 seconds downloaded).
  This does not establish full-VOD ASR quality or every Twitch format variant.
- An isolated live Codex test used invented Finnish dialogue, not production
  transcripts. After making the score direction explicit, it found three candidate
  sequences, selected two and completed two critique passes, producing a 221.7s
  edit with no unresolved model notes. This checks integration, not real VOD taste.
- Full backend run: 430 passed, nine failed. The same nine failures were reproduced
  in an isolated archive of unchanged HEAD: four clip selection fixture failures,
  one missing layout-name fixture, three short-duration selection fixtures and one
  missing caption words fixture. None were changed or suppressed for this feature.

A private-transcript live test was rejected by automatic approval review; no private
source transcript was sent for that test. Real VOD editorial quality still needs
review on the first user-started Highlights runs. See the original research and
tradeoffs in [the design](vod-supercut-design.md).

## Episode pipeline rework (20 September 2026)

`episode-v4` plans save the mapped scenes, screening scores, quality-filtered edited scene pool, ranking reasons,
selection and reserve decisions, pacing history, warnings, and source-anchored final
intervals. The UI shows edited/selected scene and retained-range counts. New request
prompts and schemas invalidate only the relevant model cache entries; audio and
transcript checkpoints remain reusable. Old draft/final media is never overwritten
by a rebuild. Existing shorts processing and publishing are unchanged.

An offline check applied the new default pause policy to the original draft's
unchanged spoken ranges: 402.13 seconds became 184.11 seconds, with no cut intersecting
a transcribed word. This is a compiler/timing check, not a new editorial result;
the actual new pipeline also edits filler and selects from the whole recording.


## Runtime optimization (20 September 2026)

The first episode rebuild exposed a schema mismatch: the model used `seconds: 0.8`
for automatic shortening while validation required zero. The new proposal schema
only offers two pause exceptions: `keep` (no seconds field), and `lead_in` (1-15
seconds). Its reaction anchor is derived from the supplied gap ID. Default cutting
is deterministic. Saved legacy edits accept harmless redundant shortening values;
a supplied gap removed with its speech needs no action. Invented IDs, unsupported
retained evidence, overlapping speech cuts, and uncertain timing remain rejected.

Rebuild is available from paused/failed/cancelled jobs as well as completed drafts.
It creates a fresh revision, starts discovery again, and uses Low thinking without
changing the source configuration or invalidating audio/transcript checkpoints.
The UI labels progress as belonging to the current stage and reports model request
counts, retries/repairs and measured request time per phase for the current revision.
These timings exclude retry countdowns and are not an end-to-end ETA.


## Content-led length (20 September 2026)

The preferred-length picker, runtime-based scene limits, reserve-filling logic,
and 20-minute timeline ceiling have been removed. Existing `target_minutes`
configuration is ignored by the episode planner; old API clients may still send
it, but new requests do not persist it. Earlier approved plans remain unchanged.
Tests cover all 220 eligible scenes reaching editing in batches of four, a single
worthwhile scene producing a short video, and a timeline exceeding 20 minutes.
Batch/context limits and bounded retries remain execution safeguards rather than
editorial quotas. More worthwhile material can legitimately require more work.


## Final quality selection and edited-timeline review (21 September 2026)

Initial screening stays at 60/100. The final assembly floor defaults to 75 and can
be set with **Minimum final score** when starting a run. It does not impose a
runtime or scene-count quota. The chosen floor, selected count, and planned duration
are saved and displayed before footage preparation and rendering. Existing saved
and approved drafts retain their original plans; they are not silently re-filtered.

Scoring sees actual output-relative speech times and jump-cut markers. Pacing review
uses the continuous episode output clock, including real pause lengths across cuts
and scene boundaries. Nearby excluded speech is labelled reference-only, without
source timestamps that could be mistaken for waiting in the finished video.

The existing reviewer can request minimal setup/payoff passages from this supplied
reference material. The compiler checks same-source anchors, safe boundaries and
no overlap with other retained scenes before inserting them into the strong scene.
This does not promote an entire lower-scoring scene. Changed scenes are re-scored.
Filler edits still operate inside scenes and preserve complete thoughts.

A compact episode-wide index supports repetition checks across review batches.
Structured duplicate decisions name both the scene to remove and the scene to keep.
Conflicting decisions cannot remove both versions; a duplicate is removed only if
the named replacement remains eligible after revisions. Similar topics alone do
not justify deletion. These checks use the existing two-pass review loop, with no
additional full-VOD model pass. Unresolved review notes remain visible.


## Visual source timeline

Completed drafts show a full-width source timeline above the review workspace.
Green ranges are retained footage; dark gaps are excluded. Split recording parts
share one chronological ruler. Each range has source and output times, plus its
saved scene score and explanation when available. Click a range (or focus it and
press Enter) to seek in the draft. Seeking uses the renderer's per-range 30 fps
rounding. Zoom up to 64x and scroll horizontally for short cuts; Fit recording
restores the overview, and Show playhead locates the current playback position.
Polling preserves zoom, scroll and playback. This is a read-only view of the saved
revision, so it adds no model calls and does not change or re-render the video.


## Video title and description

After a draft renders, one bounded low-thinking request writes a Finnish title
and a conversational description, usually 80-140 words in two short paragraphs.
It considers retained speech, edited scene durations, output positions, and recurring
themes across the whole video; quality scores are secondary to representativeness.
Regeneration starts fresh without the previous wording or its previous guidance.
Only guidance currently supplied by the user is applied. Very long edits use
explicitly labelled excerpts from every retained scene to stay within the model input budget, without another summarization pass.
Removed speech, source titles, and editorial notes are not evidence for the text.
Model service failures use the existing timed retries; if generation still fails,
the video remains available and the text can be retried or entered manually.

The Highlights review page exposes title, description, optional generation guidance,
and Generate/Regenerate and Save text buttons. Existing drafts can generate text
without a new render. Text is saved separately for each revision and edit fingerprint;
restoring a revision restores its text. Final 1080p rendering reuses any saved text,
including manual changes. Concurrent saves and responses for stale revisions are
rejected. No upload or publishing action is triggered by preparing this text.


### Frame-based YouTube thumbnails

After a draft or final video completes, pause its player and choose **Use current frame for thumbnail**.
Review the captured frame, optionally describe the composition/style or exact text, choose low/medium/high
quality and click **Generate thumbnail**. The generator uses `gpt-image-2.5-sunburst` through OpenAI's
Images edits API at 1536x864 (16:9), returning a downloadable JPEG. It sends the captured frame, saved
title/description and instructions; no full video or public hosting is required. Save posting text first.

Set `OPENAI_API_KEY` in the local ignored `.env` and restart normally. Image API billing and model access
are separate from the Codex bridge, ChatGPT subscription and the recording's text-model budget.
Generation only happens on request and does not alter the video, approval, editing queue or publishing.
Thumbnails and original frames are kept under that revision's `thumbnails/` folder; generating another
keeps all earlier candidates. Download the preferred image for manual upload to YouTube.

Repeated requests with the same request ID do not create another paid generation. Requests run separately
from the video worker, with one thumbnail request per revision at a time. There is no automatic retry of
ambiguous image-service failures: a timeout can still incur a charge. Refresh thumbnails after a lost
connection. A request interrupted by an app restart stays marked as requested; check API usage before
starting a new one. Previous revisions keep their own images.


### Preview a different score floor

After scene analysis is saved, **Score floor preview** provides a 0-100 slider and exact-score field.
Moving it instantly shows predicted length, scene count, retained sections and scenes added/removed.
The preview uses the whole saved edited pool, so lowering the floor can restore omitted scenes;
early discovery rejects and scenes discarded as duplicates are not restored. Existing cuts and
required setup inside each scene stay together. Duplicate, overlap and discard checks still apply.
Duration uses the renderer's per-section 30 fps rounding, including preserved pauses.

The slider is read-only until **Render new draft with this floor** is pressed after processing finishes.
The new revision preserves earlier drafts, reuses verified downloaded footage and fetches missing
sections if needed. It does not rerun transcription, scoring, editing or automatic posting-copy generation.
The player and source timeline continue to describe the existing video until the new draft is ready.
An empty selection or an unchanged edit cannot be rendered. Review the new joins and regenerate title,
description and thumbnail as needed. New recordings still use the initial score setting and automatic
first draft; this control previews adjustments once scene analysis exists.


### Highlights workspace

The selected recording and its player are the main workspace. Use the recording selector to switch
videos, or expand **New highlight video** to start another. The creation form is open automatically
when no recordings exist; model, initial score and encoder options are under **Model, score & encoding**.

- **Review & edit** contains the score preview, source timeline and an expandable edit request.
- **Title & thumbnail** groups the posting text and thumbnail tools, keeping the player available
  for frame selection. Tab changes preserve playback position and unsaved form input.
- **Processing & history** contains request timing, model editing notes and previous drafts.

Approve, reject and download controls stay below the player. Completed videos show their revision,
length, quality and review state; running jobs show progress and pause/resume controls. Editing notes
remain signposted above the tools. Tool tabs support arrow keys, Home and End.


### Editorial corrections and processing efficiency

New analysis uses episode-v5. Discovery, first screening and scene editing keep their existing
request identities, so paused runs can reuse verified model responses. The completed edit stage is
versioned separately. Already rendered revisions and final approvals are never rebuilt implicitly.

After independent scoring and the chosen final floor, the reviewer returns one verdict per scene:
acceptable, a structured correction, or unresolved. Corrections remove whole retained passage
ranges, restore only offered surrounding context, or change a verified retained gap. Protected
setup/payoff and necessary restored context survive subsequent corrections. The compiler applies
changes atomically and checks anchors, word boundaries, overlap and pause evidence.

Each scene permits at most two correction attempts. Changed scenes and affected joins are checked;
unchanged scenes are not routinely rewritten. A final verification cannot propose another edit.
Reversals and no-op edits stop with an explicit note rather than causing a loop. Review records in
plan.json include before/after fingerprints, the correction and its resolution. Processing & history
shows verified, unresolved and unreviewed scene counts separately from technical warnings. Lowering
the score floor can include scenes that have not had the final editorial check; this is shown honestly.

Episode-wide repetition uses a compact overview once, then full edited evidence for proposed pairs.
Local review sees only its scene and neighboring joins. Independent Codex score, edit and review
batches use at most two concurrent requests; local model calls remain serial. Displayed model time
is the sum of response times, not elapsed wall time when requests overlap. No new prompt-cache API
options are assumed for the user's bridge; existing response caches remain in use.

Highlight footage acquisition first tries packet copying with padded source windows. The actual audio
origin is still measured, every required cut must fit, and video/audio decoding is checked before the
section is accepted. A failed copy attempt falls back once to the existing encoded acquisition in a
separate folder. Cancellation never triggers fallback. Final cuts still use the verified source clock
and the selected renderer. acquisition.json records acquisition and verification timings per attempt.
The clipping acquisition defaults are unchanged.

Alignment keeps its original coarse drift probes and confidence/separation checks. Dense fallback
skips positions too close to an established anchor, rejects silent query samples before decoding
reference audio, and reuses a bounded decoded reference buffer. This is only audio clock alignment;
editorial silence decisions continue to use transcript timestamps, not a dB detector.

### Pause instruction reliability

New scene-edit requests use short scene-local gap references (`g1`, `g2`, …). The response schema
lists the allowed gap references and passage IDs for each scene. The model copies
`evidence_passage_id`; it does not recreate compound timing references or quote supporting speech
into an ID field. The application translates these references back into the existing saved edit
format. Exact pre-upgrade batch responses can still be read from their original cache keys.

Speech ranges are validated separately from pause annotations. A pause-only repair receives the
original proposal, every detected pause problem, and the supplied choices. Its response can replace
only the invalid pause slots; valid speech ranges, scene quality, and independent valid pauses are
not regenerated. Identical duplicate annotations are removed without a model request.

Each set of invalid pause annotations gets one targeted repair request. If its output still cannot
be verified, the affected pause is retained. If the gap reference itself is unknown, all retained
pauses in that scene are preserved rather than guessing which one was intended. This fallback keeps
speech selections unchanged, records `pause_recovery` in the scene, adds a visible warning, and
explicitly asks final editorial review to check the retained pauses. Invalid speech anchors continue
to require a verified repair; cancellation and service failures never become editorial fallbacks.

Batch workers perform their own targeted repairs within the two-request concurrency limit. A ready
batch can release its worker to subsequent work while another worker repairs a scene. Final results
are restored to source order before ranking. Local models remain serial. Request records distinguish
pause repairs with an `-repair-…-pauses` step and retain normal elapsed-time and token accounting.

### Render frame-rate verification

The final MP4 must contain exactly the planned number of frames and report nominal 30 fps.
Average frame rate is checked numerically against the planned duration, allowing at most one
1/30000-second clock tick per retained range, capped at one millisecond for the entire video.
This accepts tiny concatenation timestamp rounding without accepting missing frames, different
nominal rates, or material timing drift. Picture format, audio synchronization, and full-file
decoding checks remain required.
