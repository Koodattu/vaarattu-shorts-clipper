# Investigation: large-v3 missing speech

## Recommendation

Keep large-v3 optional. These experiments do not establish that Turbo is generally more accurate, and Turbo also loses speech on one of the same padded inputs. The immediate problems are input boundaries, language handling, and accepting partial output. Retain the coverage guard added in the preceding repair; trial a shorter caption context and bounded retry before replacing the original transcript.

This investigation changed no production code, settings, clips, or review decisions. Experiments used the existing GPU lock, one model at a time, while the normal rendering job continued.

## Method

Re-extracted the four flagged sections from the saved full-VOD audio using the same PCM extraction routine and original final-ASR bounds. Tested the same decoded arrays with large-v3 CUDA FP16, beam 5, unbatched, and `condition_on_previous_text=False`. Varied VAD, the no-speech threshold, input crop, and language. Also ran Turbo on identical padded and tight audio. Recorded decoded segments, timestamps, temperature fallbacks, no-speech decisions, VAD spans, and elapsed time.

Environment: faster-whisper 1.2.1, CTranslate2 4.8.2, PyAV 18.1.0, ONNX Runtime 1.29.0; existing pinned local models. There are 49 result files. Reproduction script: `.cache/large-v3-omission-investigation.py`; results and source snapshots: `.cache/large-v3-omission-investigation/`.

## Rippijuhlat: reproduced and localized

The excerpt is 43.79 seconds. The original Turbo transcript has 66 words. Our final-ASR input adds 10 seconds at each end, making it 63.79 seconds. The added opening contains an English game voice line, twice: “There is nothing we can do.” The streamer then returns to Finnish.

| large-v3 input/settings | Words inside excerpt | Coverage check |
| --- | ---: | --- |
| Original ±10 s context | 6 | Fails |
| Same input, VAD disabled | 23 | Fails |
| Same input, no-speech skip disabled | 6 | Fails |
| Exact excerpt | 68 | Passes |
| ±1 s context | 69 | Passes |
| ±2 s context | 65 | Recovers speech, fails separate cut-boundary check |
| ±5 s context | 67 | Recovers speech, fails separate cut-boundary check |

The original failure reproduces with the same six words and decoder statistics. Large-v3 translates the opening English lines into Finnish, emits an early ending, and the decoder advances to the next 30-second window. With VAD concatenation, that next window corresponds to approximately 49 seconds into the original padded audio, near the final sentence of the actual clip. The omitted Finnish speech is present in the same array: cropping that array recovers it.

This is not evidence of a 39-second silence or failed audio download. VAD retained 97.6% of the original words' timestamp duration. No decoding window met the no-speech skip rule. Disabling that rule leaves the failure unchanged. The installed faster-whisper code advances the entire window when the model emits an ending timestamp indicating no further speech; see [timestamp-based window advancement](https://github.com/SYSTRAN/faster-whisper/blob/v1.2.1/faster_whisper/transcribe.py#L1024-L1101). The model's internal reason for ending early cannot be read from those outputs; sensitivity to the added context is experimentally established, and the mixed-language opening is a plausible contributor.

## Cross-checks

| Clip | Original saved Turbo words | Saved large-v3 words | Tight large-v3 retry | Tight Turbo retry |
| --- | ---: | ---: | ---: | ---: |
| Rippijuhlat | 66 | 6 | 68 | 67 |
| Minions | 99 | 19 | 63, translated badly into Finnish | 99, English |
| Blizzard API | 43 | 1 | 44 | 43 |
| Korean chicken / KFC | 96 | 75 | 96 | 94 |

Word counts measure gross omissions here, not accuracy or WER. Different tokenization, compounds, translations, and boundary words change counts.

- **Minions:** Turbo also returned only 21 words on the padded audio. Both models skipped most of the first window. Reducing context recovered coverage, but large-v3 still translated the English discussion into garbled Finnish. Forced Finnish is inappropriate for this excerpt. Automatic language detection also incorrectly chose Finnish at about 99% probability on the tight input. Explicit English with the tight crop produced 97 English words, retaining the discussion. This shows why coverage alone cannot validate fidelity or prevent unwanted translation.
- **Blizzard API:** The padded retry varied: one attempt returned 44 words, another one word. These outputs used temperature fallbacks of 0.8 and 1.0 respectively. The tight crop returned 44 words at temperature 0; ±1 s returned 43. Changing a threshold is not a clean causal explanation for the differing padded outcomes, since sampling fallback is involved.
- **KFC:** Default padded retries returned 76 words and failed coverage. Disabling VAD returned 91 and passed; tight input returned 96 and passed. ±1 s returned 92 and passed. ±5 s still failed coverage.
- VAD retained 100%, 100%, and 99.8% of original speech timestamp duration for Minions, Blizzard, and KFC respectively. Removing VAD is therefore not a sufficient general fix. VAD also changes how speech is packed into decoding windows, so disabling it can change omissions even when it did not remove the affected speech itself.

The four cases came from a heuristic scan of 281 saved refinements. This is a biased failure sample, not an estimate of overall model quality or a measured failure rate. No human reference transcript was created, so no WER claim is justified.

## Proposed pipeline changes, not implemented in this investigation

1. Trial roughly one second of context for the first final-caption pass, keeping the larger source/transcript context available separately for boundary checks and context repair. The larger context need not be discarded globally.
2. On a coverage failure, retry large-v3 once on a tighter, speech-bounded input. Validate again. If still incomplete, retain the complete original transcript with a review note rather than silently losing words. Keep this retry conditional so ordinary clips do not pay for multiple passes.
3. Preserve the spoken language. Use the saved transcript as a language-consistency reference, and do not rely solely on short-audio language detection. The English excerpt demonstrates both an incorrect forced language and incorrect automatic detection.
4. Save decode diagnostics for failures, including temperature, VAD duration, language, and skip decisions. The original diagnostics record only surviving segments, which concealed how a window ended early.

Test proposed input changes on a representative set of successful clips before changing defaults. The small experiment supports these targeted changes; it does not justify either declaring large-v3 universally better or removing it entirely.
