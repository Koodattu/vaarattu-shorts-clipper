# Optional tighter edits

In the review queue or editor, choose **Suggest tighter edit**. Add optional editing guidance,
then **Find removable passages**. The recording's saved model/provider, verification reasoning
and spending limit are reused. This is one clip-level request, with the normal bounded validation
retry; it does not reprocess the VOD or run Whisper.

The review queue also has **Edit with instructions**. Enter a required editing prompt and choose
**Plan my edit**. This uses a separate model prompt: your instructions determine which discussion
to remove, even if that discussion is relevant or is not filler. It supplies timestamps from the
current preview, after existing speech and silence cuts, so you can refer to what you are watching.
The original **Suggest tighter edit** prompt and optional guidance remain unchanged.

Both actions use the same cut preview, selected-cut application, undo and independent review worker
path. Starting another check replaces the previous unapplied proposal for this clip; opening the
other action never applies or displays that proposal as its own. Applied edits remain saved.
Supported edits remove internal speech passages. Adding/reordering speech, rewriting subtitles or
changing the opening/ending are outside this action; the model must explain those limitations.

The panel shows each proposed removal, its reason, its current-preview time and the surrounding
phrases that would meet at the join. **Play this passage** seeks the existing preview. Select the
cuts you want, then **Apply selected and render**. Inspect the new revision before approving it.
**Undo last speech cuts and render** restores the previous set of cuts. The original files and all
source transcript words remain saved. Applying an edit makes a new revision that needs review.

The model proposes internal word ranges only. Short request-local references map back to the saved
word IDs; the model does not generate timestamps, replacement dialogue or subtitles. The prompt
asks it to retain the main point, personality, qualifiers, disagreements and joke setup/payoff,
while removing complete dispensable detours. No useful cut is a valid result. Up to six cuts can
be proposed per request, without a target duration or requirement to shorten the clip.

Validation rejects unknown, overlapping, boundary-crossing or uncertain joins and edits leaving
less than three seconds after silence trimming. Cut boundaries use the saved word timings and
leave up to 300 ms around retained speech where a gap permits it. Approved speech cuts and the
existing transcript-gap edits share one timeline; subtitles are retimed to the retained speech,
and only explicitly removed words disappear. Audio joins use the existing short edge fades.
There is no audio-volume silence detector.

Suggestions never alter a preview. Revision, request ID and transcript fingerprints protect
against applying stale suggestions. The worker persists proposals and supports pause/retry.
Applied cuts preserve their word anchors during rerendering; requesting more context or changing
the outer excerpt boundaries requires undoing speech cuts first. Layout and caption text edits
can retain the cuts. Automatic discovery, ranking and rendering do not request editorial cuts.

The model only reads the transcript. It cannot identify voices reliably, hear a clipped syllable,
or guarantee that a proposed join sounds natural. Human playback remains part of approval.

## Verification

`tests/test_tighten.py` covers anchors, unsafe joins, compact-reference mapping, selected subsets,
minimum duration, subtitle retiming, durable queue/apply/undo, resume and ASR preservation.
`tests/tighten_ui.test.cjs` checks guidance, current-preview playback times, individual selection,
render submission, undo and unsaved editor protection. Existing pacing, captions, context repair,
final transcription and frontend tests also cover the shared paths.

An isolated real-model check of **Logitechin vanhat hiiret kestivät, uudet hajoavat takuun jälkeen**
proposed removing the inventory/gameplay detour, keeping the return to the mouse discussion.
The test NVENC render shortened 57.91 seconds to 40.77 seconds. All 128 retained word IDs appeared
in the output captions in order; the original 167 words remained saved. FFmpeg decode, dimensions,
duration and audio/video timing checks passed. Test files are in `.cache/tighten-eval/`; no live
clip record or review decision was changed. This checks the mechanism on one example, not model
accuracy across the collection.
