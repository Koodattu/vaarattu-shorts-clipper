# Alignment sampling repair — 11 September 2026

The two newly held clips from the 4 July audit run were false holds caused by sparse sampling, not contradictory audio timing:

- `8011025615b8ed7fc00afc245760545d` — Buildi, jolla ei voi kävellä. Original matches were 6.60 seconds apart; the existing spacing requirement was 9.24 seconds. The fallback found a ten-second span. Measured source origin: 21736.628462 seconds.
- `87f5ac08a4beddea9a32de91de2f4534` — “Miksi jokainen leffa on peppujuttu?” Original matches were 6.93 seconds apart; the existing spacing requirement was 9.70 seconds. A further match at section second four extends the span to 9.86 seconds. Measured source origin: 11799.158333 seconds.

Both sections contain quiet padding. The five coarse sample positions found strong matching speech in the middle but missed useful speech near its edges. The fallback tries six-second samples at one-second offsets only when the coarse positions have not already established alignment. The 0.65 correlation threshold, repeated-audio ambiguity rejection, 80 ms drift limit, and required separation of max(6 seconds, 35% of section duration) remain unchanged. This does not change speech-based pause shortening or use an audio loudness detector to choose clip cuts.

The original cached files passed the production alignment function with the change. No new download or transcription was needed. Diagnostics and the before-restart snapshot are in `.cache/alignment-investigation/`; the original failed alignment diagnostics were retained. Corrected mappings were saved alongside them as `alignment-fixed.json`.

All four audit jobs were briefly paused before restarting the verified clipper worker, then resumed. Both affected clips rendered successfully using the verified cached sections and now appear ready and unreviewed in the gallery/review API. Their IDs, revisions, titles, source boundaries and words were preserved, and both exported video checksums were verified. The remaining batch is running with the updated code.

Validation: 22 alignment/media/clip tests and 39 delivery/recovery/worker/concurrency tests passed. Added synthetic cases cover sparse speech, total silence, repeated speech and genuine drift. Ruff and the repository's normal diff whitespace check passed. Earlier uncommitted ranking/audit changes were preserved.


## Follow-up: measure the excerpt, not download padding

The 3 July run later held `74650df113a3f8a7115753d28d5c1bc9`, a 3.22-second excerpt about a CEO sending a message at nine in the evening. The first denser-search fix alone was insufficient: ten matching samples agreed on the offset, but their seven-second span fell short of 35% of the 23.25-second padded download (8.1375 seconds).

The delivery pipeline now passes the actual clip duration to alignment. Required separation is max(6 seconds, 35% of clip duration), independent of quiet acquisition padding. The six-second floor keeps the two six-second query windows non-overlapping. Existing correlation, repeated-audio ambiguity and 80 ms offset-spread checks are unchanged. Callers without a clip duration retain the conservative section-duration rule. Diagnostics now record clip/section durations, required/matched spacing and offset spread.

The actual cached clip passes with a 6.625-second span and 0.5 ms offset spread, with correlations around 0.91. No boundary padding was added to the exported clip, no cuts or words were changed, and no new download or transcription was needed. The normal guarded retry created revision 2; its ready preview, gallery availability and video checksum were verified. Its original model-verification note remains attached as an editorial audit note.

New tests vary quiet padding (10 or 25 seconds on each side): short clips pass independently of padding, while a brief utterance still cannot validate a genuinely 60-second clip. Existing silence, repeated-audio and drift tests remain in place. All 57 clip/media/delivery tests passed, along with Ruff and the diff whitespace check.

The active job was briefly paused to load the updated worker and then resumed. The failed clip's completed source run now has its recovered preview, and the final audit video continues rendering with the new rule. Original diagnostics and a before-restart snapshot are preserved; verification artifacts live under `.cache/alignment-clip-span/`.
