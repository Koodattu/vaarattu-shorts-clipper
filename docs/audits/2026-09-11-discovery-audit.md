# Discovery and review audit — 11 September 2026

The clearest improvement is a tighter definition of worthwhile content during discovery **plus a repair to comparative ranking**. The ten-clip review limit is working, but comparative ranking did not succeed on any of the four latest videos. Raising generic score thresholds or minimum duration would discard some clips the creator approved.

This is a read-only audit of the four latest completed VOD runs, all created on 8 September with conversation-v10 and Codex/Luna low reasoning for discovery and verification. I inspected saved proposals, verification decisions, all 40 allocated clips' current transcripts and review decisions, ranking requests/responses, seven saved context-repair outcomes, and the application code. No videos were watched or listened to, no inference was run, and no pipeline settings, prompts, database records or exports were changed. Current reviews and revisions are the snapshot, not a reconstructed review history. Detailed data: [evidence ledger](2026-09-11-discovery-audit-evidence.json).

**The latest four videos**

Dates below come from recording titles, not processing dates. Rows follow newest run creation first.

| Recording | Hours | Raw suggestions | Verified candidates | Passed quality gate | Approved | Rejected | Remaining |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2.7 — aluminium folio | 4.51 | 83 | 80 | 68 | 4 | 5 | 1 unreviewed |
| 3.7 — fidget spinning | 4.85 | 98 | 94 | 83 | 7 | 3 | 0 |
| 12.4 — millo leagues | 4.15 | 50 | 48 | 39 | 3 | 5 | 2 held |
| 4.7 — F | 6.13 | 68 | 67 | 47 | 2 | 8 | 0 |
| Total | 19.64 | 299 | 289 | 237 | 16 | 21 | 3 |

The application allocated ten clips per run: 40 total, of which 38 have ready previews and 37 have human decisions. Approval is 16/37 (43%), with substantial variation by recording. Those labels cover the selected pool only. The 249 verified candidates outside the allocated 40 are **not human rejections**, and their approval potential is unknown.

Discovery produced candidates in 171 of 199 speech windows. Only 28 windows were empty; for the two newest videos, just 3/46 and 1/49 were empty. This is roughly 15 suggestions per recorded hour, rather than hundreds per hour. The more actionable concern is that verification then recommended accept for 240/289 and the numeric gate admitted 237/289 (82%). It is doing little editorial narrowing before ranking.

There were 327 verification requests, 199 discovery requests and four ranking attempts: verification accounts for about 62% of these 530 initial selection requests. These counts include retries; they exclude later context repair and media work. Reducing weak discovery suggestions would therefore save repeated verification work, not merely reduce a hidden list.

**The top ten was selected through fallback on every video**

The saved review summaries all say `method: scores`.

- The 68- and 83-candidate pools generated comparison payloads of approximately 53 KB and 65 KB, before the system prompt/schema overhead. The application's conservative 48,000-unit input budget prevented the comparison call. This is an application budget using UTF-8 bytes as an API input bound, not evidence that the model's actual context window was exhausted.
- The 39-candidate pool received a complete JSON response, but it substituted `candidate-22` for the required `candidate-2`. The retry included all required candidates plus eight unrequested IDs.
- The 47-candidate pool received all required IDs plus two unrequested IDs. Its retry omitted two required IDs.

These were not truncated responses. All four saved responses have completed status. The responses included IDs outside the supplied pool. Free-form string IDs and sparse numbering are plausible contributors; the saved responses do not establish the cause. Validation appropriately rejected them, but the system then discarded the whole comparative ordering and fell back to equally weighted score sums. Generic repair feedback did not name the missing/extra IDs.

This matters beyond implementation neatness. On 4 July, the second comparative attempt placed the two ultimately approved clips first and second. The fallback placed them first and eighth. That does not establish how the other proposed top-ten clips would perform, but shows useful comparative information was lost. The intended best-to-worst ranking has not yet had a successful live trial on these videos.

Recommended repair: compare bounded batches, use dense batch-local IDs constrained to the supplied set, and return explicit missing/extra IDs on a repair attempt. If batches still have many review recommendations, reduce them with further comparisons before the final comparison; do not funnel an unbounded survivor list into the same oversized call. Keep the application-level maximum of ten. Do not treat unrequested IDs as real candidates or silently pretend an incomplete comparison succeeded. A fallback should remain visible.

**What approvals and rejections suggest**

These are content interpretations from transcripts and review labels, not claims about why each decision was made. The latest four videos have no saved rejection-note text. Seven clips have later revisions, so the decision sometimes concerns a different cut/transcription from the model's original scoring.

1. Developed content is often stronger than generic moment-to-moment commentary. Approved examples include the stolen WoW lockout story, the 10,000/16,000-hour skill joke, the all-purple boss-design criticism, and the build-change/workplace analogy. Each gives an actual event, contrast, explanation or comic turn.
2. The model overcredits routine remarks as jokes. Rejected examples include “Näin se vaan menee elämässä,” the immune-boss complaint, “Historian huonoin mage,” and the observation that someone is always playing and must be addicted. Reasons repeatedly describe these as clear, recognizable or containing a punchline. That does not establish enough value to publish.
3. Correct mechanics are not automatically interesting explanations. “Ketkä healerit kannattaa valita absorbeihin?” was rejected. Its verification reason emphasizes that it is a coherent explanation with no misleading negation, while the actual content is routine raid coordination. The existing prompt already asks to exclude this: more repetitions of that instruction are unlikely to be the main fix.
4. Short gamer banter remains valuable. “Jos kuolen, älkää naurako” was approved at 4.18 seconds. “Oman elämän sankari” was approved despite being an initially needs-context discovery proposal. A blanket short-clip ban, gamer-reference penalty or removal of every uncertain proposal would conflict with observed approvals.

There is a useful numerical association, but not a safe new hard gate:

| Existing score | Approved / reviewed | Approval rate in this selected sample |
|---|---:|---:|
| Substance 4 | 10/13 | 77% |
| Substance 3 | 6/24 | 25% |
| Opening 4 | 2/10 | 20% |
| Opening 3 | 13/26 | 50% |

The one opening-2 clip was also approved. Equal-weight summation can therefore reward an easily recognized opening over substantive content. However, requiring substance 4 would lose six of the sixteen known approvals. An offline reorder that puts substance first, retaining the existing gate, overlap suppression and ten-clip limit, retained fourteen of sixteen known approved candidates but displaced the approved Roblox-price and calves clips. Seven replacement candidates had no human label. This is a counterfactual coverage check, not proof of better precision.

Across all 289 verified candidates, substance 3 was assigned 222 times. The score is functioning as a broad default. Changing what earns a 3 is more promising than merely changing its numeric cutoff. A stronger ranking preference for substance is worth evaluating; it should not eliminate shorter humorous exceptions.

**Context repair distinguishes salvageable cuts from weak ideas**

Five clips have context-request history, with seven saved outcomes:

- The €315 Roblox/Braindrot clip was expanded twice, from an initial 11.48-second interval to 16.52 seconds, then from the refined 16.83-second interval to 27.83 seconds. The marketplace setup and follow-up details were added. Its current revision is approved.
- The calves observation is approved after an extension. The creator explicitly wrote “a good clip but I would like it to be longer.”
- For the 4.18-second death joke, the creator also called the clip good while asking for extra context. The model found no useful addition, kept it unchanged, and it is approved.
- “Kun elämä on over” found no useful extra context and is rejected.
- The username/Np clip expanded from 27.04 to 63.05 source seconds and remains unreviewed; there is no evidence yet that expansion improved it.

This supports preserving candidates with an identifiable worthwhile idea and a fixable context gap. It does not support treating every vague remark as salvageable. It also shows that adding duration is not itself a success criterion. The calves extension's rationale is weak and fragmentary, even though the creator approved the resulting clip; inspect whether additions supply real information rather than simply satisfying a request for length.

For comparison, six explicit notes on the older 5 July run say two clips might need earlier/more context and four have “nothing clip worthy.” That is useful supporting evidence for two different failure modes; those older labels were not pooled into the table above.

**Recommended prompt change: replace permissive repetition with a concrete value test**

The shared system prompt currently includes discovery, verification, scoring, cut handling, repeated exceptions, and reassurance that the creator is the final judge. That made sense when the objective was to expose rejected proposals, but it now encourages the model to justify weak remarks. The goal should become: find every *worthwhile* distinct moment, not make a clip out of every coherent conversational beat.

Proposed replacement wording for the discovery value criteria, not an additional block appended to the existing prompt:

> Propose a moment only when its spoken content contains a specific reason to watch: a comic turn, an engaging event or personal detail, or an opinion/explanation supported by a reason, example, contrast or consequence. Ordinary gameplay updates, generic complaints, acknowledgements and repeated remarks are not candidates merely because they are understandable. For humor, identify the actual comic turn; the final sentence is not automatically a punchline. Keep genuinely funny short banter and game references. A viewer may know the game without knowing the current stream situation. Preserve worthwhile ideas that need recoverable setup, but do not assume unseen gameplay or additional context will turn a weak remark into a good clip. Empty sections are expected. In the reason, name the actual turn or insight rather than simply saying the moment is clear, relatable or funny.

Use a few short paired examples from this audit to demonstrate the distinction: generic immune-boss complaint versus the purple-design explanation; a routine gameplay-status remark versus the lockout story; a generic closing phrase versus the approved short reversal joke. Treat these as creator-calibration examples, not universal topic bans. Reserve other clips/videos for evaluation so the examples do not become their own test answers.

Separate discovery instructions from verification-only instructions. Discovery should not be asked to provide the best cut even when rejecting a proposal. There are 21 explicitly rejected proposals among the 297 saved discovery proposals; omitting clear discovery rejects could save some work, but that alone addresses only about 7% of the proposal pool. Audit salvage before making it a hard skip. Keep needs-context cases when the underlying value is real: at least one known approval began in that category.

Do not add another model call merely to classify whether something is a clip. Discovery already has the speech and an output reason. First make that existing stage apply the clearer criterion. Likewise, overlap deduplication is secondary: the current proposal pools contain fourteen pairs meeting a same-anchor/50%-overlap proxy, far fewer than the overall candidate excess. These pairs are not all proven duplicates, so aggressive merging is not a substitute for editorial selection.

**How I would evaluate the change**

First fix ranking reliability; in parallel, prepare the smaller discovery prompt and creator examples. Replay the same saved transcripts only when inference is authorized. Compare baseline versus revised discovery on held-out videos, retaining the full scan. Measure proposals per source hour, verification requests/tokens, successful comparison rate, known-approved moment coverage (by temporal overlap, not exact word-ID equality), and human approval among the final ten. Track whether genuinely worthwhile needs-context candidates survive.

A 30–50% reduction in proposals is a reasonable experimental aim, not a promised result or model quota. Do not declare success if fewer proposals simply hide good clips. Since most excluded candidates have never been human-reviewed, inspect a small sample of newly discarded/borderline moments to estimate missed opportunities. Keep review volume capped at ten and allow fewer when fewer qualify. Pairwise/comparative evaluation and calibration against human labels follow [OpenAI's evaluation guidance](https://developers.openai.com/api/docs/guides/evaluation-best-practices).

I would not increase reasoning effort across the pipeline yet. All four latest runs use low/low; older medium/medium runs used different videos and selection policies, so they do not isolate the effect of reasoning effort. Fixing failed/skipped ranking and a permissive value criterion addresses observed defects. A matched low-versus-medium evaluation can follow if the revised task still needs it.

Finally, separate editorial evaluation from caption quality. The rejected Minions clip has a long English passage in the original Turbo transcript, while its large-v3 caption transcript contains a much shorter Finnish rendering. The model's selection reason was based on the former. This is a transcription discrepancy worth investigating separately, not evidence by itself that discovery invented the entire idea. Without listening, this audit cannot attribute that rejection to content versus captions or judge which transcript is accurate.

No production changes were made. The recommended next implementation is robust bounded ranking plus a shorter, example-calibrated discovery criterion, followed by a matched evaluation that protects known good clips.
