# Final transcription coverage repair

Reported clip: `83283488402ce3e76855930c580a93e1`, “Rippijuhlat ovat sukuloinnin heavyweight-sarja”, run `32917ac59bd942488098eca116eaf7c4`.

The original 43.79-second excerpt contains 66 saved Turbo words. The raw large-v3 result contains only six words inside the excerpt, starting 39.04 seconds into it. The exported SRT agrees with that incomplete result. This is a transcription omission accepted by our validation, not a missing subtitle filter. The saved diagnostics do not establish why large-v3 skipped the discussion.

`apply_refinement` now compares final word coverage against the original transcript before replacing it. A caption gap containing at least three original words and 1.5 seconds of their speech rejects the refinement. A 500 ms margin accommodates timing changes. This checks timestamp coverage rather than identical text and does not use an audio-level detector. Existing handling retains Turbo captions with a visible warning for audit previews and holds ordinary exports for attention. Small omissions or omissions shared by both transcripts can still escape this heuristic.

Restored the reported clip's 66 original words as revision 2 while the audit worker was paused, with a guarded revision update and backup at `.cache/caption-coverage-repair-before.json`. Source bounds, title, layout, original preview and review record were preserved. Restarted the server to load the validation and resumed processing. The new preview is ready, has subtitles from 0.20 seconds, and runs 28.75 seconds after existing transcript-based pacing removes 15.04 seconds of pauses. It carries the Turbo fallback note and a caption reading-speed flag.

Verification: 40 final-transcription, pacing and clip tests passed; Ruff and `git diff --check` passed. Confirmed both old and new preview hashes, unchanged source bounds, and visibly burned-in subtitles in a frame extracted at one second from the new export.

A read-only scan of 281 saved refined transcripts also flagged these existing clips for inspection; they were not changed by this repair:

- `a6df903933d818ddf109c070accf4ad5`: Poistettu Minions-kohtaus olisi ollut viiden tähden materiaalia (99 original / 19 final words).
- `8a4419a43522a85d2f3c3023e18170e6`: Korealaista kanaa vai KFC:tä? (96 / 75).
- `fe97844af724aa4048170450ab0d23f5`: Blizzardin rajapinta hukkaa hahmot (43 / 1).

Counts alone do not establish which transcription is correct; these were flagged because final caption gaps overlap speech timestamps in the original transcript. Previously saved refinements are not automatically rewritten.
