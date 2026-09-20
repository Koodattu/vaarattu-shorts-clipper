# Highlights workflow

Highlights is a separate landscape-video workflow. Open **Highlights** in the
navigation, or **Create highlight video** on a recording in Video library.
Restart the app and its normal worker together after installing this change.
No separate service or dependency is required.

## Starting a recording

Paste a completed YouTube or Twitch VOD URL. **Check recording parts** reads the
source metadata. The library action does this automatically. YouTube titles ending
in `(Part 1/N)` through `(Part N/N)` (also `Osa`) are matched using the configured
channel and normalized base title, which includes the recording date when present.
The resolver searches saved entries and up to six catalogue pages; missing or
ambiguous parts require entering the complete set of URLs, one per line. Part
numbers determine order. Unrecognized/zero-based numbering is not guessed.

Only complete recordings are accepted. YouTube channel validation remains in place.
Twitch public VOD URLs use the existing yt-dlp dependency, without browser cookies.
Protected or unavailable sources can fail acquisition; the app does not bypass
access restrictions. One Twitch VOD is one recording. A Twitch VOD and its YouTube
archive are not combined as consecutive parts.

Review the matched parts, choose a model provider and encoder, then **Create
highlight video**. Length follows the worthwhile content. There is no runtime
floor, ceiling, or scene quota; weak material is not used to fill time.
Codex uses the configured bridge/model and account allowance; paid API providers
require a spending limit. Local providers reuse the existing model installation.

## Processing and review

- Download full audio per source part, then transcribe with Turbo. Compatible,
  hash-verified audio and full transcripts from shorts runs are copied into the
  highlights workspace when available. Copies protect them from shorts cleanup.
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
  Scores below 60/100 and edits marked discard are excluded. Assemble worthwhile
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

`episode-v3` plans save the mapped scenes, screening scores, quality-filtered edited scene pool, ranking reasons,
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
