# Editorial selection audit — 12 September 2026

## Scope and evidence

Reviewed the saved transcript content, duration, model judgments and human decisions for all 387 current clip records. Excluding the first-ever run leaves 385 clips: **83 approved, 298 not approved, four unreviewed**. All 381 decisions refer to the current reviewed revision. This was a transcript/content audit, not a fresh audiovisual viewing of every clip. Optional rejection notes were not used to train a rule or included in the model replay.

The latest four recordings have now been reviewed beyond their original top ten:

| Recording | Clips reviewed | Approved | Not approved | Approved in existing top ten |
|---|---:|---:|---:|---:|
| 2 July — aluminium folio | 83 | 30 | 53 | 7 |
| 3 July — fidget spinning | 96 | 21 | 75 | 6 |
| 12 April — millo leagues | 49 | 7 | 42 | 2 |
| 4 July — F | 68 | 6 | 62 | 1 |
| **Total** | **296** | **64** | **232** | **16 / 40** |

These are clip counts, not independent moments: some long/short versions and overlapping cuts received separate decisions. Older runs were inspected too, but their smaller, already filtered pools are less useful for estimating discovery recall.

## What the decisions support

The main distinction is **a reason to watch the whole excerpt**, rather than merely an understandable sentence or a specific topic. The old prompt's repeated permission to include observations, personality and mildly interesting remarks was too easy to satisfy.

Approved material includes game-specific humor, inside references, technical explanations and calm discussion. A general-audience gate would remove good material. Examples:

- **“Kun joku varastaa killan lockoutin”** explains the unusual exploit and its consequence: the guild cannot enter its own raid. Routine raid instructions and equipment comparisons were usually rejected.
- **“Odottakaa ensi seasoniin…”** develops a criticism with a concrete example: a simple gem-drop fix was postponed for a whole season. **“Viitti Blizzard”** merely notes an item nerf and was rejected.
- **“Wikipedia oli epäluotettava – Google AI onkin luotettava?”** develops an identifiable contradiction. Passing product preferences and unsupported complaints often did not earn approval.
- The long **Groundhog Day / unchanged life** excerpt was approved, while its highly ranked short setup was rejected. The actual developed satire matters more than an immediately clear opening. Boundary refinement must preserve the rewarding continuation.
- **“Mummo halusi juhlia Tšernobylin jälkeen kovemmin”** is a compact setup and comic turn. Raid callouts decorated with a joke or theatrical delivery often remained uninteresting coordination.

This is not an exact taste classifier. Some recognizable jokes were rejected; some brief personal remarks and visual reactions were approved. Captions, performance and cuts can affect a decision independently of transcript content.

### Duration and scores

Approved clips have a median rendered duration of **22.68 seconds**, versus **12.22 seconds** for rejected clips. Approved durations range from **4.18 to 129.26 seconds**, including a long repaired/audit version. Longer is not automatically better: long rambles were rejected too. No new minimum or length-based editorial gate was added.

For the reviewed clips' saved selection scores:

- Substance 0–2: **60 rejected, zero approved**.
- Substance 3: **62 approved / 287 reviewed** (22%).
- Substance 4: **21 approved / 34 reviewed** (62%).
- Opening 4: **5 approved / 27 reviewed** (19%). A strong opening is a poor substitute for actual content.

Requiring substance 4 everywhere would discard 62 of the 83 approvals. Instead, the prompt now defines substance 3 more strictly, and substantive value takes priority over opening polish. These are final saved scores; they must not be mistaken for an unbiased estimate of discovery-score accuracy.

## Implemented pipeline

1. **Scan all speech as before.** Discovery has no numerical output quota. The shorter prompt requires an actual comic turn, story consequence, revealing contrast, or supported explanation/opinion. Clear but ordinary remarks should be omitted, not proposed with caveats. Game vocabulary and personality remain allowed.
2. **Build a global shortlist before context checks.** Drop discovery rejects and substance below 3. Merge duplicate moments, then prioritize substance, payoff, standalone clarity, fidelity and opening, in that order. For equally scored overlapping versions, retain the fuller version so verification can preserve its setup and continuation. Check at most **20 distinct candidates**, across transcript and chat-peak discovery combined.
3. **Verify only that shortlist.** Refine faithful boundaries and independently judge value. The prompt explicitly protects the interesting continuation instead of shortening a clip into a setup. A promising missing-context candidate can still receive the second, wider context pass.
4. **Compare the resulting speech independently.** Ranking receives the excerpt text and candidate IDs, without previous scores, titles, summaries or persuasive model reasons. All twenty candidates are compared together when they fit the input budget; oversized pools retain bounded comparisons. It can defer even the best item in a weak pool. Since context has already been checked, incomplete fragments should not be passed onward simply for another investigation.
5. **Render at most ten distinct qualifying clips.** The existing application-level render limit remains in force; fewer, including zero, are valid. Discovery and comparison are not told to fill ten places.

The 20-candidate budget means at most 40 contextual passes if every candidate needs the wider second pass; transport and malformed-output retries are separate. Previously the latest four recordings verified **289 proposals**, with **327 verification calls** in the saved initial audit. An 80-candidate total across those four recordings would reduce candidates entering that expensive stage by **72%**. This is a workload bound, not a measured token or runtime saving on newly processed recordings.

Every proposal remains saved. `verification-shortlist.json`, `verification_shortlist` in the run result, and selection issues record budget exclusions, weak candidates and duplicates. Ordinary editorial exclusions do not turn an empty successful selection into a failed job. Existing reviews, previews and explicit “review all suggestions” behavior remain available; the broad audit mode is not the default pipeline.

Discovery is versioned `conversation-v11`; ranking is `ranked-v3`. New recordings use them. Finished discovery checkpoints and human-reviewed revisions are preserved. Manual “needs more context” expansions remain exempt from the ordinary automatic clip duration cap.

## Validation and limits

The targeted Python suite ran **63 tests: 62 passed, one existing caption fixture failed**. The failing `test_audit_refinement_preserves_long_originals_without_widening_normal_limits` supplies no `words` field to `apply_refinement`, which requires that field. Its assertions and the caption code were not changed in this task. Ruff passed for every touched production/test file; `git diff --check` reported no whitespace errors.

New tests cover a 63-proposal batch that makes only 20 verification calls, preservation of exclusion evidence, retention of a promising context gap and its fuller duplicate, zero verification calls for weak material, successful empty results, and ranking inputs without prior editorial persuasion, and a single comparison for a normal twenty-candidate pool. Existing tests cover global ranking, duplicate suppression, the ten-render cap, resumability and preservation of reviewed revisions.

An isolated live replay uses the existing Codex/Luna provider at low reasoning. It writes only under `.cache/editorial-audit-2026-09-12`, never to the production review database, and performs no rendering. It tests saved initial candidates through the new shortlist and ranking, and separately rescans eight six-minute sections (one approval-rich and one rejection-rich section per recording).

The eight discovery rescans proposed **13 candidates versus 27 originally**. This is a 52% reduction in this deliberately selected sample, not a forecast for every VOD. It retained examples such as the long WoW-hours roast and the grandmother/Chernobyl joke, but also retained some weak material and missed the approved Tuska festival reflection. New-video human review remains the necessary test of editorial quality and recall.

The hard shortlist budget deliberately trades some recall for cost. The latest four recordings alone contain more approved clip versions than forty final slots. This change should be judged by approved clips per review and by meaningful missed moments, not by whether it can reproduce every earlier approval. No claim of perfect ranking or unbiased held-out accuracy is warranted by this retrospective audit.


### Final ranking replay

The final code shortlisted from each recording's saved discovery scores, then compared the saved verified speech. It selected **14 approved / 30 clips (47%)**, compared with **16 approved / 40 clips (40%)** in the existing top tens:

| Recording | Existing approved / selected | Replay approved / selected |
|---|---:|---:|
| 2 July | 7 / 10 | 6 / 9 |
| 3 July | 6 / 10 | 5 / 7 |
| 12 April | 2 / 10 | 2 / 6 |
| 4 July | 1 / 10 | 1 / 8 |

That is a modest precision improvement and less review work, **with two fewer approved selections overall**. It does not establish that ranking is solved. The two gameplay-heavy recordings remain particularly weak. This replay also uses old discovery scores and cuts; it does not measure the entire new discovery-and-verification pipeline. Current review labels can reflect later caption or context edits.

All four final pools fit one comparison each, versus four comparisons each with the previous batch size. In the intermediate multi-comparison trials, 40 of 136 repeatedly judged excerpts changed review/defer recommendation between comparisons under the same prompt. Comparing the normal shortlist together removes those repeated judgments; it does not make the model deterministic or guarantee editorial agreement.

Aggregate and per-run evidence is saved in [the evidence JSON](2026-09-12-editorial-selection-evidence.json). Full private transcript ledgers, requests and replay outputs remain in `.cache/editorial-audit-2026-09-12`. The local app was restarted after the final code change, with no active processing jobs interrupted.
