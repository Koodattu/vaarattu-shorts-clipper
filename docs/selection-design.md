# Transcript presentation and selection, passages-v2

Implemented 2026-09-06. This document describes the running code after an app restart, rather than the broader research options in the original plan.

## Decision: retain words; present speech passages

Whisper still transcribes locally with word timestamps. The canonical words, IDs, probabilities and microsecond timestamps remain unchanged. Captions, precise cuts and audio/video alignment need this information. Replacing it with sentence-only timestamps would discard useful editing precision.

Discovery receives readable passages assembled deterministically from those words. We do not ask another model to rewrite Finnish, restore punctuation, translate, summarize or delete fillers. Negation, dialect, repetitions, names and hesitations can carry the meaning or humor. There is no extra model call for grouping.

Each passage ends at terminal punctuation, a pause of at least 1.2 seconds, or a readability limit of 15 seconds / 45 words. A small abbreviation list avoids common false sentence endings such as `esim.`. These are reading aids, not certified grammatical sentences or speaker turns. The LLM is explicitly told that a thought may span many lines. Every word belongs to exactly one passage in source order. Long pauses are shown explicitly; time is never compressed.

Illustrative prompt line (not a quote from a VOD):

```text
w010..w019 | 45.2-51.8 | En minä sitä tarkoittanut, vaan että tämä riippuu tilanteesta.
[pause 3.1s]
```

Discovery sees two boundary IDs and one time range per passage instead of an ID and two timestamps for every word. Display times are rounded to tenths of a second; all actual cuts still resolve through the unrounded canonical word records. Candidate contracts keep their existing word-ID fields, avoiding a separate segment-to-word migration.

## Two different jobs for the LLM

1. **Discovery:** six-minute owned cores, with 90 seconds of original surrounding speech on both sides, expanded to complete passages. Find zero to three strong spoken moments per core. Each proposal's idea anchor must belong to that core, allowing context overlap without duplicate ownership. Prefer complete opinions, stories, explanations, observations and jokes; gameplay remains background. Scan all speech, including quiet regions. No summary or chat-activity filter can remove speech from this pass.
2. **Verification and boundaries:** sort proposals by their rubric scores, remove substantial temporal duplicates and take at most ten. Send the original surrounding passages, initially 90 seconds before/after the proposed clip. Only the clip and 15 seconds around it receive individual word IDs inline; distant context stays readable. The verifier can refine individual words near the clip and use passage boundaries elsewhere. Discovery scores, title, summary and rationale are withheld to reduce anchoring on the first model judgment. The verifier independently scores the excerpt and writes its final title/summary.

If verification requests more context, expose up to 180 seconds on each side once. Make that extra call only when it adds original words. If sufficient context cannot fit, retain `needs_context`; do not silently shorten context or declare the candidate safe.

Coarse proposals of 10–120 seconds can reach verification, allowing correction of passage boundaries. Final accepted clips must still be 20–90 seconds, score at least 15/20, score at least 3 for standalone and fidelity, and have no flags. Refined spans must retain the original idea. Export still selects up to three non-overlapping passing clips. This is contiguous clipping, without sentence rearrangement or pause removal.

The same model does both passes. They are separate requests, not independent model opinions or a guarantee of factual/editorial correctness. For OpenAI Luna, discovery and verification both explicitly use `low` reasoning, following the creator's preference. Other providers retain their existing reasoning settings: Gemini low, Z.ai thinking enabled, DeepSeek thinking disabled, and local llama.cpp unchanged. The OpenAI setting is recorded per request. `low` is an initial quality/cost tradeoff, not a measured Finnish-quality winner. [Official Luna model documentation](https://developers.openai.com/api/docs/models/gpt-5.6-luna) lists the supported reasoning levels.

## Context budgeting and request control

The old API path combined a local 16K profile, a conservative UTF-8 byte bound, and word-by-word formatting. It repeatedly halved windows down to roughly 11 seconds and eventually narrowed context. The resulting request count was an application defect, not a model limitation.

- API planning now has an independent 48,000-unit conservative input bound. It counts UTF-8 bytes plus schema and framing allowances, **not measured tokens**. The final send gate is 60,000 with up to 4,096 output tokens inside a conservative approximately 64K application envelope. We do not need the provider's entire advertised context window.
- Local planning still uses llama.cpp's tokenizer and the actual selected 16K/32K allocation, reserving output and schema room. Local tokenization uses local HTTP calls; these are not generation requests or paid API calls.
- If a window is too dense, split ownership at a passage boundary while retaining the full 90-second surrounding context. If even one owned passage plus its context cannot fit, fail explicitly instead of repeatedly shrinking context.
- Include instructions, schema and control text in sizing. Native structured-output providers receive the JSON schema through the API only. JSON-mode providers receive it in the prompt once. Avoiding duplicate schema instructions follows [official OpenAI prompting guidance](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.5).
- Requests remain sequential: one VOD, one model request in flight. No paid tokenizer calls, extra summarizer, embeddings, additional dependency or concurrent discovery pool was added.
- Plan the full scan before generation and save `discovery-plan.json` in the run's versioned inference folder. The UI reports `Scanning speech: X of Y sections`, then verification progress. The plan records the discovery request count and a maximum of 20 verification calls; one repair per call can add attempts.

The native-schema choice keeps field definitions out of readable transcript text without removing JSON validation. Semantic validation also checks visible anchors, ordering, ownership and the retained idea. Discovery validates each suggestion separately: invalid IDs, reversed boundaries and out-of-section ownership are logged and discarded without a paid repair or losing valid siblings. A rejected suggestion is not a run failure. Verification permits one repair, then marks only that candidate ineligible. A wholly unreadable model response gets one repair; if still unreadable, that discovery section is recorded as a coverage gap and later sections continue. Invalid responses are still charged and recorded. A missing context response is not an automatic acceptance.

Issues are saved after each occurrence in `selection-issues.json` and included in the selection result and run report. A finished run with unevaluated sections reports `coverage=partial`, `outcome=needs_attention` and the skipped-section count; valid clips can still be delivered. The channel library does not mark a partial scan as fully processed. Rerendering preserves the coverage warning. Service access/transport errors, budget exhaustion, cancellation and local integrity failures still stop processing rather than being disguised as model-content problems.

## Measurements on the saved first VOD

Source `OJ-bDXbfEos`, duration approximately 6 h 14 min, 20,757 canonical words. Replayed both planners offline on the identical saved transcript; **no inference, model download or paid call**. Machine-readable results and transcript hash: [selection-efficiency.json](selection-efficiency.json).

| Measure | Previous planner | Passages-v2 |
|---|---:|---:|
| Discovery requests | 1,111 | 63 |
| Transcript representation, UTF-8 bytes, one full copy | 816,513 | 345,327 |
| Aggregate discovery instructions + transcript bytes | 3,796,649 | 628,243 |
| Canonical words preserved | 20,757 | 20,757 |

This is about 94% fewer discovery requests and 83% less aggregate discovery text. Aggregate byte counts exclude the JSON schemas and protocol wrappers in both columns; the request sizing guard includes them. Byte counts are not token counts, bills or latency benchmarks. Different transcripts/local context sizes can produce different counts. For this VOD, expect 63 discovery calls plus up to 10 initial verification calls and up to 10 useful context expansions, excluding repairs. Previously paid attempts remain in the same run's spending ledger.

## Recovery and reproducibility

`passages-v2` salts the selection checkpoint chain **after transcription**. Audio and transcript checkpoints remain reusable; unfinished old selection requests cannot be mistaken for the new format. A completed, hash-valid selection checkpoint retains its saved version so resuming delivery does not change existing clips or repeat paid selection. Existing artifacts are preserved in their old inference folder. A response cache additionally hashes provider/model, instructions, transcript prompt, schema, reasoning, output limit and context setting. Response/usage filenames include request identity; the ledger records logical prompt hash, exact request-body hash (including any repair suffix), OpenAI effort and response elapsed seconds.

An existing paused run requires an **app restart before Resume**, because a long-lived worker has already imported the previous implementation. Resuming then reuses the transcript but starts a fresh selection pass. Existing spending and any uncertain reservation still count against its original cap. No resetting of billing or silent budget increase occurs.

## What would establish excellent clip quality

Structural tests prove source coverage and safe mechanics. They do not prove that the model picked the best Finnish moments. The next authorized processing run should compare results against creator judgment:

- Mark useful moments in a fixed sample containing calm speech, slang, a story crossing a window boundary, delayed qualifications, game-only chatter, repeated words and a second voice. Keep a separate unseen sample for confirmation.
- Record how many marked moments discovery finds, how many survive the ten-candidate shortlist, and how many final clips are worth posting. Inspect rejected/pruned proposals as well as exports.
- Listen to starts/ends and surrounding speech for incomplete thoughts, changed stance, mistaken speaker attribution and caption errors. Text cannot verify a voice or recover words missed by ASR.
- Record measured input/output/reasoning tokens, latency, final spend and usable clips per VOD. Compare verification `low` with `none` on the same candidates before making a quality claim.
- A three-proposal-per-window cap and ten-candidate shortlist can miss good moments. Increase those only if evaluation shows recall loss; do not solve it by blindly adding requests, larger context or another model pass.

Topic diversity, calibrated scores, diarization and factual verification remain future evaluation work. The current version has reliable transcript preservation and much lower overhead, with editorial quality still requiring real output review.


Recovery follow-up: the first passages-v2 live run returned the same out-of-section proposal twice for source seconds 5040–5400. Its saved, already-accounted response was recovered into the existing prompt cache without changing the raw response or ledger. No prompt/window version changed for this fix, so earlier completed sections remain reusable. The app was restarted and the same run resumed under its original $1 cap.
