# Transcript presentation and selection, conversation-v6

## Current behavior: conversation-v10 (8 September 2026)

New selections use a ranked review queue capped at ten clips per run (one VOD). Discovery still scans the complete transcript without a count quota and verification checks every distinct proposal. The substance rubric now distinguishes a worthwhile clip from merely specific gameplay terminology or an instruction. Gamer humor, personality and gradual/open-ended discussion remain welcome.

Before rendering, candidates require valid source boundaries, verifier accept, substance >=3, standalone >=2 and fidelity >=2. Opening and payoff have no hard threshold. These gates retained all ten creator approvals in the latest saved run, but still admitted 43 candidates; they are not sufficient ranking evidence. See [the review audit](audits/2026-09-08-review-calibration.md).

A separate comparative request receives the actual candidate excerpts, existing scores and concerns, without catchy titles, creator labels or a requested clip count. It returns every admitted ID in priority order with review/defer and a short reason. The application keeps review recommendations, removes overlapping variants covering at least half the shorter clip or the same idea anchor, and admits at most ten. It may select fewer. Raw section responses, verification and ranking decisions remain saved. The run's **Selection feedback and decisions** shows exclusions, rank, reasoning and a link to original context.

The comparison uses the saved verifier reasoning setting, provider and spending ledger. Its own checkpoint avoids repeat calls on resume. Malformed output receives the existing bounded repair; if still invalid, or if the whole comparison exceeds the existing input budget, a visible warning accompanies deterministic score ordering. Operational or budget errors remain explicit failures. No chat boosts or creator rejection keywords silently change ranks. The 43-candidate saved-run comparison fits the current Codex input bound (37,641 / 48,000 conservative units); no live comparison was run during implementation, so improved ranking precision is not yet measured.

Only ranked candidates are downloaded/rendered and enter the queue, best first within each run. Existing clips count against the allocation on recovery; older exports and human reviews are preserved even when already over the limit. Completed pre-v10 selections retain their saved behavior. Nothing reprocesses the latest completed run automatically. Restart the app after active work finishes to use v10 for new selections.

## Current behavior: conversation-v9 (7 September 2026)

Discovery must identify an actual interesting remark, joke, insight or story in the speech. Gamer references, banter and personality remain welcome, including worthwhile moments with uncertain context or a few unclear words. Unclear fragments are not leads merely because they might conceal something interesting. Routine coordination, mechanics instructions and generic reactions need a distinctive observation, humor, personal detail or engaging explanation beyond the immediate task. Each proposal's existing reason names its value before any uncertainty; no new output fields or count limits were added.

This narrows v8's speculative discovery instructions. Every generated source-valid proposal still proceeds to human review; verifier outcomes and numeric scores remain advisory. Reasoning settings, duration limits and rendering behavior are unchanged. The active v8 run is left running with its loaded instructions and saved artifacts; v9 takes effect after the user restarts the app. Finished selection checkpoints remain reusable. Offline checks validate mechanics, not the new prompt's real selection quality.

## Current behavior: conversation-v8 (7 September 2026)

v8 retains v7's human editorial control, changes the hard minimum to three seconds (five preferred), removes duplicate discovery titles/summaries, and supports separate low/medium reasoning controls for OpenAI/Codex. Both efforts remain low by default. Exact request bodies are saved alongside responses. Isolated comparisons can target saved sections, including empty ones, without rendering clips. See [pacing and evaluation](pacing-and-evaluation.md). Older run settings and completed checkpoints remain preserved.

## Current behavior: conversation-v7 (7 September 2026)

This supersedes historical editorial gates below. Discovery welcomes gamer humor and personality and returns plausible borderline moments for creator review. Verifier verdicts, scores and flags are advisory; source-valid 2–90-second proposals proceed, with faithful short cuts preferred. The 2-second minimum remains unchanged. Fresh scans retain distinct overlapping proposals and suggestions anchored in adjacent visible context, removing only exact duplicates. Invalid IDs and media integrity failures still need repair. Empty-section feedback remains required. No count quota or approval-rate target should suppress plausible candidates. “Not approved” records a publishing choice, not a generation failure.

Optional revision-specific human review reasons are available in the queue/gallery notes dialog. See [the three-run audit](audits/2026-09-07-three-run-audit.md) for evidence, implemented scope, and deferred pacing/model/encoder recommendations. No live processing was started by the agent.

Updated 2026-09-07. This document describes the running code after an app restart, rather than the broader research options in the original plan.

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

## Discovery, extra chat leads and independent verification

Both passes now explicitly target Finnish viewers who do not know the game or previous chat. Personal stories, everyday observations, relationships, work and broader internet/culture takes are preferred. A game-related moment still qualifies if the humor or broader point survives without the footage. Routine crafting/build advice and ability complaints are rejected even when coherent. Standalone must score at most 2 when game knowledge or unseen action is required; the prompt forbids invented visual payoffs. This is an editorial instruction, not a proven classifier: quality still needs creator review on actual outputs.

1. **Discovery:** six-minute owned cores, with 90 seconds of original surrounding speech on both sides, expanded to complete passages. Find every distinct strong spoken moment per core, with no count quota; return an empty candidate list when none qualify, together with a required concrete Finnish feedback sentence of at most 240 characters explaining the section-level decision. Each proposal's idea anchor must belong to that core, allowing context overlap without duplicate ownership. Prefer complete opinions, stories, explanations, observations and jokes; gameplay remains background. Scan all speech, including quiet regions. No summary or chat-activity filter can remove speech from this pass.
2. **Optional peak discovery:** when the stream identity and timing are confirmed, independently inspect speech around unusual active-chatter buckets after completing the full scan. Chat is a lead, never proof of quality. No full-transcript window is removed. See [chat integration](stream-data.md) for detection, timing and failure semantics.
3. **Verification and boundaries:** sort proposals by their rubric scores, remove substantial temporal duplicates and verify all remaining proposals. Send the original surrounding passages, initially 90 seconds before/after the proposed clip. Only the clip and 15 seconds around it receive individual word IDs inline; distant context stays readable. The verifier can refine individual words near the clip and use passage boundaries elsewhere. Chat counts/source labels and discovery scores, title, summary and rationale are withheld to reduce anchoring on the first model judgment. The verifier independently scores the excerpt and writes its final title/summary.

If verification requests more context, expose up to 180 seconds on each side once. Make that extra call only when it adds original words. If sufficient context cannot fit, retain `needs_context`; do not silently shorten context or declare the candidate safe.

Positive-length coarse proposals up to 90 seconds can reach verification, including short jokes below five seconds. Final automatic selections must be 2–60 seconds, receive an accept judgment, and score at least 3 for standalone, substance and fidelity. Flags are advisory review notes, retained in rendered metadata and UI; they do not veto an accepted candidate. Genuine context, attribution, meaning or substance problems must be expressed through outcome and scores. Opening and payoff have no acceptance threshold and there is no minimum total score. Prefer 15–45 seconds, but do not pad short jokes. A verifier may move the idea within the original proposed excerpt to reach the complete point or joke; it cannot move outside that interval. All references must remain real, visible, ordered and inside the final excerpt. Export non-overlapping passing clips without a quota. Manual edits support 2–90 seconds. This remains contiguous clipping without sentence rearrangement or pause removal.

The same selected model performs full discovery, optional peak discovery and verification. They are separate requests, not independent model opinions or a guarantee of factual/editorial correctness. For OpenAI Luna, discovery and verification both explicitly use `low` reasoning, following the creator's preference. Other providers retain their existing reasoning settings: Gemini low, Z.ai thinking enabled, DeepSeek thinking disabled, and local llama.cpp unchanged. The OpenAI setting is recorded per request. `low` is an initial quality/cost tradeoff, not a measured Finnish-quality winner. [Official Luna model documentation](https://developers.openai.com/api/docs/models/gpt-5.6-luna) lists the supported reasoning levels.

## Immediate opening and editorial judgment

A specific subject, relatable tension, opinion, story event or joke setup within roughly the first two seconds is preferred, not required. Natural conversational openings, brief fillers and a subject that emerges later can still qualify. Trim expendable preamble only when doing so preserves natural speech and meaning; do not force the topic into the first words. Nothing is rewritten or reordered. The timing requirement is separate: speech should begin within 0.5 seconds of the cut. Automatic cuts already allow at most 200 ms before the first selected word, bounded by adjacent speech. This uses canonical ASR timestamps, so actual audible onset still depends on their accuracy.

Substance must contain a worthwhile idea, personal detail, relatable tension or actual joke. A conclusion, consequence, answer or punchline is preferred, but engaging discussion and open-ended observations can qualify without one. An unresolved topic is different from cutting off a sentence or omitting a meaning-changing qualification. End naturally; do not extend an excerpt just to manufacture closure. The verifier explains why the whole excerpt is worth watching, or identifies a concrete substance, context or fidelity problem. Low opening/payoff scores alone must not trigger rejection in either discovery or verification. Natural calm Finnish speech is welcome; shouting, outrage and fabricated clickbait are not requirements. The rubric gates are deterministic checks of model-reported scores, not proof that a model understands quality. A real creator-rated comparison remains necessary.

## Context budgeting and request control

The old API path combined a local 16K profile, a conservative UTF-8 byte bound, and word-by-word formatting. It repeatedly halved windows down to roughly 11 seconds and eventually narrowed context. The resulting request count was an application defect, not a model limitation.

- API planning now has an independent 48,000-unit conservative input bound. It counts UTF-8 bytes plus schema and framing allowances, **not measured tokens**. The final send gate is 60,000 with a 4,096 output-token planning allowance inside a conservative approximately 64K application envelope. We do not need the provider's entire advertised context window. Codex's bridge does not enforce that output cap; see [Codex limitations](codex-provider.md).
- Local planning still uses llama.cpp's tokenizer and the actual selected 16K/32K allocation, reserving output and schema room. Local tokenization uses local HTTP calls; these are not generation requests or paid API calls.
- If a window is too dense, split ownership at a passage boundary while retaining the full 90-second surrounding context. If even one owned passage plus its context cannot fit, fail explicitly instead of repeatedly shrinking context.
- Include instructions, schema and control text in sizing. Native structured-output providers receive the JSON schema through the API only. JSON-mode providers receive it in the prompt once. Avoiding duplicate schema instructions follows [official OpenAI prompting guidance](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.5).
- Requests remain sequential: one VOD, one model request in flight. No paid tokenizer calls, extra summarizer, embeddings, additional dependency or concurrent discovery pool was added.
- Plan the full scan before generation and save `discovery-plan.json` in the run's versioned inference folder. The UI reports `Scanning speech: X of Y sections`, then verification progress. The plan records the discovery request count; verification_requests_max is null because there is no candidate quota. Each unique proposal can use one initial verification and one useful context expansion; one repair per call can add attempts. The API spending limit and 4,096-token response limit still apply. Dense responses can exhaust that response limit; exhausted repairs remain explicit partial coverage rather than silently claiming a complete scan.

The native-schema choice keeps field definitions out of readable transcript text without removing JSON validation. Semantic validation also checks visible anchors, ordering, ownership and the retained idea. Discovery validates each suggestion separately: real interior start/end IDs expand to their containing displayed passage boundaries, and an interior idea ID maps to its passage start. Adjustments are recorded in `anchor_adjustments`. Invented IDs, reversed boundaries and out-of-section ownership are logged and discarded without a paid repair or losing valid siblings. A rejected suggestion is not a run failure. Verification permits one repair, then marks only that candidate ineligible. A wholly unreadable model response gets one repair; if still unreadable, that discovery section is recorded as a coverage gap and later sections continue. Invalid responses are still charged and recorded. A missing context response is not an automatic acceptance.

Issues are saved after each occurrence in `selection-issues.json` and included in the selection result and run report. A finished run with unevaluated sections reports `coverage=partial`, `outcome=needs_attention` and the skipped-section count; valid clips can still be delivered. The channel library does not mark a partial scan as fully processed. Rerendering preserves the coverage warning. Service access/transport errors, budget exhaustion, cancellation and local integrity failures still stop processing rather than being disguised as model-content problems.

## Measurements on the saved first VOD

Source `OJ-bDXbfEos`, duration approximately 6 h 14 min, 20,757 canonical words. Replayed both planners offline on the identical saved transcript; **no inference, model download or paid call**. Machine-readable results and transcript hash: [selection-efficiency.json](selection-efficiency.json).

| Measure | Previous planner | Passages-v2 |
|---|---:|---:|
| Discovery requests | 1,111 | 63 |
| Transcript representation, UTF-8 bytes, one full copy | 816,513 | 345,327 |
| Aggregate discovery instructions + transcript bytes | 3,796,649 | 628,243 |
| Canonical words preserved | 20,757 | 20,757 |

This is about 94% fewer discovery requests and 83% less aggregate discovery text. Aggregate byte counts exclude the JSON schemas and protocol wrappers in both columns; the request sizing guard includes them. Byte counts are not token counts, bills or latency benchmarks. Different transcripts/local context sizes can produce different counts. The passages-v2 measurement used 63 discovery calls and capped verification at ten candidates. Those are historical v2 results; v3 verification count depends on the actual number of distinct proposals. Previously paid attempts remain in the same run's spending ledger.

## Recovery and reproducibility

`conversation-v6` salts the selection checkpoint chain **after transcription**. Audio and transcript checkpoints remain reusable; unfinished old selection requests cannot be mistaken for the new format. A completed, hash-valid selection checkpoint retains its saved version so resuming delivery does not change existing clips or repeat paid selection. Existing artifacts are preserved in their old inference folder. A response cache additionally hashes provider/model, instructions, transcript prompt, schema, reasoning, output limit and context setting. Response/usage filenames include request identity; the ledger records logical prompt hash, exact request-body hash (including any repair suffix), OpenAI effort and response elapsed seconds.

An existing paused run requires an **app restart before Resume**, because a long-lived worker has already imported the previous implementation. Resuming then reuses the transcript but starts a fresh selection pass. Existing spending and any uncertain reservation still count against its original cap. No resetting of billing or silent budget increase occurs.

## What would establish excellent clip quality

Structural tests prove source coverage and safe mechanics. They do not prove that the model picked the best Finnish moments. The next authorized processing run should compare results against creator judgment:

- Mark useful moments in a fixed sample containing calm speech, slang, a story crossing a window boundary, delayed qualifications, game-only chatter, repeated words and a second voice. Keep a separate unseen sample for confirmation.
- Record how many marked moments discovery finds, how many survive deduplication and verification, and how many final clips are worth posting. Inspect rejected/pruned proposals as well as exports.
- Listen to starts/ends and surrounding speech for incomplete thoughts, changed stance, mistaken speaker attribution and caption errors. Text cannot verify a voice or recover words missed by ASR.
- Record measured input/output/reasoning tokens, latency, final spend and usable clips per VOD. Compare verification `low` with `none` on the same candidates before making a quality claim.
- There are no per-window, verification or export count quotas. Track useful clips per API dollar; more suggestions are not inherently better.

Topic diversity, calibrated scores, diarization and factual verification remain future evaluation work. The current version has reliable transcript preservation and much lower overhead, with editorial quality still requiring real output review.


Recovery follow-up: the first passages-v2 live run returned the same out-of-section proposal twice for source seconds 5040–5400. Its saved, already-accounted response was recovered into the existing prompt cache without changing the raw response or ledger. No prompt/window version changed for this fix, so earlier completed sections remain reusable. The app was restarted and the same run resumed under its original $1 cap.

## Rechecking saved exclusions

[Recovery workflow](selection-recovery.md): **Recheck excluded moments** uses a separate recovery checkpoint/cache, preserving the original selection and existing clips. It re-verifies saved exclusions with v6 rules; no full transcript or chat discovery calls are made. For tYfuF30Gl08 this routes 14 exclusions back through verification and preserves the two earlier winners. Outcome is still the new verifier's decision, not automatic acceptance. Historical empty responses have no feedback to recover. New scans require and save concise feedback in `section-feedback.json`, selection results and the run report; the UI shows feedback and individual candidate decisions. A recovery interrupted during rendering remains recoverable through Resume / retry without switching to a full scan.
