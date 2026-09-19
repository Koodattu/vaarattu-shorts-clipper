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

Review the matched parts, choose preferred length (5-20 minutes), model provider
and encoder, then **Create highlight video**. Length is a preference: weak material
is not used to fill time. A draft below five minutes is explicitly labelled.
Codex uses the configured bridge/model and account allowance; paid API providers
require a spending limit. Local providers reuse the existing model installation.

## Processing and review

- Download full audio per source part, then transcribe with Turbo. Compatible,
  hash-verified audio and full transcripts from shorts runs are copied into the
  highlights workspace when available. Copies protect them from shorts cleanup.
- Scan overlapping six-minute transcript cores with 90 seconds of context;
  subdivide oversized requests. Keep anchored, worthwhile sequence proposals.
- Compare candidate evidence, reject overlap, and limit detailed editing to at
  most 40 minutes (or three times the preferred duration). No per-hour quota and
  no requirement to produce a fixed number of highlights.
- Edit selected sequences using original passages and bounded surrounding context.
  Keep chronological order. The default edit preserves gaps. Removing a pause
  between retained passages requires an explicit gap decision; listing adjacent
  passages separately cannot silently remove their pause.
- Independently critique the assembled speech and revise at most twice. Stop when
  passed, unchanged or at the limit. Remaining editorial notes are shown for review.
- Fetch selected video windows with context handles, check their audio alignment
  against the full source audio, and render a 720p draft. Source parts retain their
  own clocks and never form one seamless cut across an unverified part boundary.
- Review playback. **Revise draft** takes instructions for the selected sequences
  and their context; it does not perform a new search of the entire recording.
  Each revision preserves previous drafts. **Restore this draft** restores a saved
  revision, and future changes still get a new revision number.
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
are checkpointed with hashes. Transient model errors use existing timed retries.
Approval verifies the saved edit and media hashes and renders directly without loading ASR or calling the LLM, even if prompts have since changed. User revisions
reuse the original transcription and edit only the selected material. Source windows
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
