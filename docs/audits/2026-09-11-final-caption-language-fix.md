# Final-caption language and recovery fix

The full-VOD Turbo transcription continues to default to Finnish. Automatic audio language detection is not enabled globally: the preceding controlled investigation found that it could confidently classify the English Minions excerpt as Finnish.

The optional large-v3 final pass now:

- Uses one second of surrounding audio rather than ten.
- Uses the saved excerpt's text as a conservative Finnish/English hint. Sustained English requires at least 12 tokens, six English function-word occurrences, three distinct English function words, at least 25% English function words, and four times as many English as Finnish markers. Otherwise Finnish remains the default. Individual gamer terms and titles do not switch the language.
- Explicitly requests transcription. Coverage and cut checks remain in place; clearly English source text must remain clearly English in the refinement, so a fully timed Finnish translation is rejected too.
- Retries only invalid refinements once on the exact excerpt, then validates again. Valid clips have one pass. Retry batches run in a finite child after the first child exits, under the existing GPU lock.
- Keeps the saved Turbo words, bounds, and timing issues if both attempts fail, with a visible caption warning for normal and audit exports. A clip with no original words still requires attention. This changes the previous ordinary-export behavior that held a clip solely because the optional final pass failed.
- Saves language, VAD duration, segment temperature, context size, and retry reason. Cache identities include the new policy, context size, and per-clip language so old failed results and different language choices are not reused.

Context expansion clears old caption/fallback state so the expanded excerpt can receive its own final pass. Existing legacy audit warnings remain readable. No new dependencies or LLM calls were added.

## Verification

- 119 relevant Python tests passed: final transcription, ASR routing, context repair, pipeline, seams, delivery/recovery, gallery, caption highlighting, and pacing.
- Seven UI tests passed. Ruff and `git diff --check` passed.
- Executed the real final-transcription pipeline on four known problem clips plus nine previously successful clips. All 13 passed validation; 12 passed with one second of context, and one successful clip needed the tighter retry. This checks coverage and boundaries, not human-rated word accuracy.
- The previously failing English Minions clip selected English and produced 97 English words. Rippijuhlat produced 69 Finnish words, Blizzard 44, and KFC 92, all passing coverage and boundary checks. Different word counts from Turbo are not WER measurements.

The validation outputs and pre-repair clip snapshots are in `.cache/final-caption-fix-validation/`. All four known affected previews finished rendering with the validated final transcripts: Rippijuhlat revision 3, Minions revision 2, Blizzard revision 2, and KFC revision 2. Verified their new video hashes and preserved old preview hashes. Extracted frames confirm visible Finnish subtitles in Rippijuhlat and English subtitles in Minions. The nine comparison clips were not changed. Previous previews and review records are preserved; new revisions follow the usual review workflow. The idle server was restarted to activate the fix after the original batch completed.

## Limits

The language hint is deliberately conservative and specific to this Finnish/English workflow, not a general language detector. Short or ambiguous English passages default to Finnish. Mixed-language passages and isolated terms can still be mistranscribed or translated; neither word-timing coverage nor the language-pattern check proves semantic fidelity. The original transcript is also not a human reference. These changes address the reproduced failures without claiming perfect multilingual transcription.
