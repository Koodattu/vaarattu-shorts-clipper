# Audit of the three latest successful runs

Audited 7 September 2026. Historical counts below describe the saved v5/v6 runs before changes. Following the creator's clarification, conversation-v7 and optional human review reasons were implemented locally. The production database was opened read-only; no app restart, inference, download, render, or live review mutation was performed. Existing uncommitted work was preserved.

Evidence: saved selection/checkpoint files, raw model responses, request usage, current revision-specific reviews, transcript words and timing diagnostics, alignment reports, source code and historical v5 code. Three rendered contact sheets were inspected. This was not a full listening/viewing pass over all videos; comments about content and cut quality below are transcript-based hypotheses unless otherwise stated. Word gaps do not prove silence or absence of missed speech.

[Complete evidence ledger](2026-09-07-three-run-evidence.md) contains all current clips and reviews, final verifier reasons and excerpts, discovery proposals, and every discovery section. Reproducible extraction is in `.cache/audit-three-runs.py`; derived structured data is in `.cache/three-run-audit-facts.json`.

## Implemented after the creator clarified the goal

The creator wants every source-valid proposed clip available for human review, including model rejections and low scores. **Not approved means not chosen for publishing; it does not mean generation was a mistake.** The objective is useful clip yield and recall, not maximizing the percentage approved by suppressing borderline candidates.

- conversation-v7 explicitly welcomes gamer humor, game references, banter and streamer personality. Discovery should include plausible borderline moments. The system prompt shrank from 4,655 to 4,362 characters.
- Verifier outcomes, standalone/substance/fidelity scores and flags are advisory review notes, not editorial export gates. The verifier still improves faithful boundaries; if validation fails, the valid original proposal remains available with a note. Invented/unordered anchors and unsupported duration remain technical constraints, and media integrity checks remain in place.
- Fresh scans retain distinct overlapping proposals and valid suggestions from adjacent visible context; exact duplicates are deduplicated. Recovery also considers saved `outside_section` exclusions, and preserves already represented clips and their edits.
- The current 2-second minimum is unchanged; no new 3- or 5-second veto was added. Source-valid cuts up to the existing 90-second render limit can proceed, with shorter complete cuts preferred.
- Both the queue and gallery's review-notes dialog now include an optional **Your review reason** text box. Reasons are saved against the current revision, independently of approval, and survive approval/status changes. Saving a reason does not rerender or advance the queue. A new media revision starts unreviewed with no current reason; this remains a current-review record, not review history.
- After the user restarts the app, **Recheck excluded moments** on the latest run will reconsider the saved exclusions under v7. It does not rescan the 44 empty sections. New discovery prompts apply to future full scans.

An offline replay using the latest run's actual transcript and saved model responses routed all 20 proposals as eligible, with 12 advisory notes and 12 new mocked delivery calls, preserving all eight existing clip bodies/revisions. This is routing evidence, not twenty real renders or a new model-quality evaluation. No live recovery was started. Replay harness: `.cache/audit-v7-replay.py`.

## Findings that matter most

1. The latest 20 → 8 reduction happened entirely in verification/eligibility: seven model rejections and five model accepts blocked by numeric scores. No rendering losses, duplicate pruning, or count quota caused it.
2. Several score-blocked accepts explicitly say the joke or observation works without detailed game knowledge. The audited v6 product had two conflicting editorial decisions: the model's outcome and deterministic score gates.
3. Long pauses are a major pacing problem in already-selected clips. Several latest exports contain 9–24 seconds of gaps between recognized words. Boundary prompting alone cannot remove internal gaps: the current product deliberately makes contiguous cuts.
4. Creator approvals support game-related humor and personality. Repeated strict general-audience instructions can suppress content matching those approvals. Conversely, being a general-interest topic has not guaranteed approval.
5. The v6 recovery was productive: the older run increased from two eligible clips to eleven; four of the newly recovered rendered clips are now approved. Preserve these improvements.
6. There is a concrete discovery miss to evaluate: the 32-versus-23 age-guessing story around 00:03:21 in the rerun of `OJ-bDXbfEos`. This is a plausible candidate, not an audio-verified winner.

## Run scope and counts

The first-ever run `a10dd4555f3142fc938bc9035607285a` was excluded. A later run of the same source video is included because it is one of the requested three latest successful runs.

| Source / run prefix | Selection | Full-scan sections: nonempty / empty | Raw proposals | Final eligible | Ready / held | Approved / not approved / unreviewed ready |
|---|---|---:|---:|---:|---:|---:|
| `_Oc4pNx1ztY` / `a7b71ff9` — latest | v6 | 18 / 44 | 20 | 8 | 8 / 0 | 0 / 1 / 7 |
| `OJ-bDXbfEos` / `3d29cbce` — rerun | v6 | 6 / 57 | 7 | 4 | 4 / 0 | 1 / 3 / 0 |
| `tYfuF30Gl08` / `ed8155b3` | v5, then v6 recovery | 14 / 47 | 16 | 11 | 10 / 1 | 6 / 4 / 0 |

All three report complete transcript-scan coverage. That means the sections were processed; it does not prove that every worthwhile moment was discovered. There are 22 current ready exports, seven approved and eight not approved; seven ready exports remain unreviewed. The held clip is also unreviewed.

Chat peak discovery added three empty regions for `OJ-bDXbfEos` and two empty regions for `tYfuF30Gl08`. It added no proposals in these runs. The latest run's chat timing was unconfirmed, so that extra pass did not run. Do not infer a valid offset from similar titles; confirming the mapping would enable an existing feature, not guarantee additional good clips.

The older run originally had 16 raw proposals: four invalid-anchor proposals were filtered, leaving twelve; two sub-five-second proposals were then excluded by the old shortlist minimum, leaving ten verifications and two eligible clips. The recovery reconsidered the saved proposals/anchor exclusions under v6, retained the two winners, and produced nine additional eligible clips. It did not scan the empty sections again.

## Exactly where the latest twelve went

Five final responses said `accept` but failed score gates:

| Candidate | Source start | Gate | Assessment from source text |
|---|---|---|---|
| Vanha peli, uusi hinta ja ennakkopääsy | 00:29:31 | Standalone 2 | Strong reconsideration candidate. The excerpt itself explains an old game, remastered graphics, a higher price, and paid early access. Knowing the title seems unnecessary for that point. |
| Peli pitää pelaajat mukana porkkanalla | 04:55:19 | Standalone 2 | Plausible general observation about retaining subscribers, but has unclear references, jargon, and substantial gaps. Needs editing judgment. |
| Kun 100 % varma veikkaus menee pieleen | 03:46:49 | Standalone 2 | The verifier explicitly says the basic joke works without game knowledge. Source text supports an overconfident prediction failing; the 47-second selection is loose. |
| Pelaajat eivät kadonneet – he hajaantuivat | 05:44:07 | Standalone 2 | Potential community-fragmentation take, but the model acknowledges unresolved game-version references. Worth review, not automatic acceptance. |
| Twitch onnistuu joskus | 04:24:23 | Standalone 2 and substance 2 | Weakest rescue case: appreciating an emote can depend on seeing it. The positive rationale is inconsistent with the scores, but that does not establish quality. |

Seven were explicitly rejected: the unclear Pokémon purchase; confused anime names; the unexplained 7×7-day declaration; the extra-raid scheduling joke; the AI/digital-marketing excerpt; the seed-carrier explanation; and the casual-guild discussion. Several concerns are reasonable from the supplied text. Under the creator's clarified workflow, all twelve should nevertheless be rendered for human review, with these concerns visible. A reasonable model criticism is not an editorial veto.

The AI example is particularly useful for cut evaluation. Discovery selected 00:36:12–00:36:40, ending on an unfinished qualification. The verifier rejected it and referred to a later distinction between artistic and practical AI use. That later discussion is visible around 00:37:07–00:37:44, but outside the original proposal's idea interval. Current code allows the start/end to expand into visible context; it restricts the retained *idea* to the original proposal. Spanning the original start through the later qualification would also exceed 60 seconds and include unrelated spoken interruption. Therefore this is not simply a missing-context-window bug. A separate candidate anchored in the later discussion is a better thing to test than blindly extending the first excerpt.

## What the “Checking clip 5 of 20…” step does

This progress message reports the second LLM pass over proposed cuts. Historically it also preceded editorial filtering; in v7 its editorial verdict is advisory.

For each shortlisted proposal, the same selected LLM receives its start/end/idea word IDs and original surrounding transcript. It initially sees about 90 seconds of context on each side. Individual word IDs are shown around the proposal ±15 seconds; distant context uses readable passage boundaries. The first pass's title, rationale, scores, and chat provenance are hidden to reduce anchoring.

It independently chooses boundaries, scores five dimensions, writes a title/summary/reason/flags, and returns `accept`, `reject`, or `needs_context`. Only `needs_context` can trigger a second request with up to 180 seconds per side, and only if new words are available. Invalid model output can receive one repair. In the audited v6 runs, deterministic checks then required accept, standalone/substance/fidelity ≥3, and a 2–60-second excerpt. v7 removes those editorial gates and permits source-valid 2–90-second cuts. Flags are advisory in v6. Opening and payoff do not have minimum gates.

The latest run used exactly twenty first verification requests: no context expansion and no repair. This step does not listen to audio, inspect expressions/game footage, run speech detection, or check physical audio/video sync. Alignment and rendering happen later.

## Reviews, missed sections, and cut quality

All seven approved titles:

- LoL:n lopettaminen paransi elämää
- WoW-hahmoluokka on persoonallisuustesti
- Uutta classia valitsematta? Amatöörivirhe
- Kun mikki on liian lähellä
- Kun League of Legends oli vielä rennompi
- Kun päivitysmuistiinpanot eivät kerro mitään
- Kun keräilyvimma ei anna jättää mitään

The eight not-approved titles were the historical esports victory, paint-drying story, knee-pain explanation, anime-versus-manga habit, own-stream drop, Facebook decision, Härmä geography joke, and Diablo wordplay. These labels do not say why the creator chose not to post them. At audit time the schema stored only current revision and status, with no reason/history. The new optional note records future reasons without treating disapproval as a generation error. The creator described short/incomplete context and content they would not post, and explicitly said these clips should still have been generated. The latest run has only one review, so its approval rate cannot yet be judged.

The creator confirmed during this audit: **include gamer humor and personality**, even when a few terms are unexplained. Both “new class is OP” and vague patch notes passed creator review. The repeated strict general-audience requirement is therefore an editorial mismatch. Replace it with a concise instruction that unfamiliar game terms are acceptable when the humor, personality, or observation still carries the clip; routine mechanical chatter alone is not sufficient.

Examples worth testing for boundary improvements:

- The rejected paint story starts with an unrelated apology and frustration before the painting anecdote. Advancing the opening may improve it; it may still be weak or inaccurately transcribed.
- The rejected Härmä clip starts on a broken phrase and repeats the geography question. A cleaner setup and less repetition are plausible improvements, not proven reasons for rejection.
- The accepted anime-ending clip includes “En tiedä mikä on Fuuga” between the explanation and its continuation. Removing silence alone would retain that spoken tangent. Internal dialogue editing is a separate, more demanding capability.
- The approved nostalgic LoL clip is 48.6 seconds and still has substantial gaps. Approval and a need for pacing improvements can coexist.

All 128 saved v6 section explanations were inspected, alongside older proposal outputs and selected surrounding transcript. Most empty sections concern genuine game mechanics or fragmented commands. Spot checks of festival discussion and a fish-and-chips question support leaving some sections empty. The bar anecdote around 00:03:21–00:03:30 is a plausible miss in an empty six-minute section: someone guesses 32 and learns the person is 23. It needs audio/speaker verification. The section's broad explanation (“fragmented birthdays/dating/gameplay”) is not proof that no short joke exists within it.

The rerun also lost a valid in-context proposal, “Mellakointi ei onnistu helteellä,” at 05:12:28–05:12:34 because its idea belonged to the following owned section. That following section returned empty. Keep ownership for deduplication, but consider retaining a valid adjacent-context proposal for verification after the full scan instead of discarding it. It still needs the contextual/editorial check; this is evidence of candidate loss, not a guaranteed additional good clip. The audited v6 recovery seed included invalid-anchor exclusions but omitted `outside_section` exclusions; v7 now includes both.

## Silence removal and minimum duration

| Current clip | Duration | Gaps between recognized words ≥1.5s | Longest gap |
|---|---:|---:|---:|
| Anime's invented ending | 54.87s | 24.09s | 16.87s |
| 6×7 holiday | 33.36s | 15.82s | 8.26s |
| Weighted blanket | 32.64s | 14.52s | 11.28s |
| Tori purchase | 32.86s | 11.63s | 8.34s |
| Boycott joke | 17.00s | 9.15s | 9.15s |
| Approved patch-note joke | 23.78s | 10.42s | 10.42s |

Proposed first experiment: shorten only long, acoustically verified non-speech gaps (initial threshold around 1.5–2 seconds), retaining about 0.4–0.7 seconds of natural pause and protective margins around speech. These are starting values to evaluate, not calibrated constants. Preserve meaningful comedic beats, laughter, quiet speech, and reaction sounds. Transcript gaps merely nominate regions; ASR can miss speech, and game/music audio makes a volume threshold insufficient.

Use an explicit list of source intervals to retain, with monotonically ordered source→output time mapping. Apply exactly the same cuts to audio and video, retime every caption cue/word, preserve original source timestamps and an unchanged original revision, and provide a contiguous preview for comparison. Protect overlapping/uncertain speech regions; leave them intact when uncertain. Crossfade audio only within verified non-speech margins if needed to avoid clicks. Validate audible syllables around each cut, A/V sync, and caption continuity.

This requires a deliberate edit-plan extension because current contracts/rendering assume one contiguous start/end interval. Do not quietly apply an audio-only silence filter: FFmpeg's silence detection/removal and trim/concat tools exist, but an audio-only removal would not implement synchronized video/caption editing. [FFmpeg filter documentation](https://ffmpeg.org/ffmpeg-filters.html).

For a later duration policy, **3 seconds hard / 5 seconds soft** is a reasonable candidate to evaluate; complete four-second jokes should remain possible. Following the creator's request to review candidates first, this audit leaves the existing 2-second minimum unchanged. Do not pad. The current minimum is 2 seconds. None of the latest eight selections would change under either a 3- or 5-second minimum. Across all current clips, the only sub-five-second cases are the not-approved own-stream drop (3.92s) and held flower joke (4.04s); that is too little evidence to ban all four-second jokes. None of the seven approved clips is below 8 seconds.

## Errors, flags, and observability

- Latest run: no selection issues, incomplete response statuses, context expansions, or repairs. One ASR timing-overlap issue intersects the Steam clip; it is flagged in the export. There were thirteen final accepts: five fail gates and eight export.
- Rerun: one `outside_section` candidate loss; two ASR overlap records outside the selected clips. Three chat-peak passes returned empty.
- Older run: four invalid-anchor discovery exclusions and one `verification_unreadable` issue under v5. The saved microphone responses were valid JSON accepts; historical code rejected the refined idea for moving beyond the original idea passage. The generic “unreadable” label obscured a boundary-validation failure. v6 recovery successfully produced that now-approved microphone clip.
- The older ledger also contains **two reserved requests without saved responses or usage**, both `verify-0-0`. There are 91 request records but only 89 saved completed responses. The available evidence does not establish what happened upstream. Report unknown usage separately; do not call it zero or silently erase reservations. This was Codex subscription routing, not evidence of a paid API charge.
- The flower joke remains held. Its two correlation matches are approximately 0.929 and 0.926 and agree on the origin, but are 6.016s apart; the 24.064s section requires 8.422s separation. Other attempted samples were silent. Prefer an adaptive/wider-context alignment retry with preserved evidence over disabling alignment checks.
- Fifteen of 22 ready clips have a “captions too fast” warning, including five of seven approved clips. The current detector flags the whole clip if any cue exceeds 30 characters/second. This is useful diagnostic data but too common to distinguish a bad selection. Show affected cue locations and an aggregate severity; consider extending short phrase display into safe gaps while preserving word highlighting and avoiding the next cue.
- Finnish/English ASR errors are visible in the saved excerpts, including incomplete Finnish words and corrupted terms. More LLM reasoning cannot recover absent audio. Consider targeted audio-backed transcript correction for promising uncertain clips, retaining canonical provenance. Do not ask the text LLM to fabricate cleaner speech. No implemented second caption-ASR phase was found in the execution path, despite older design references to optional refinement.
- The text verifier cannot establish speaker identity or audiovisual punchlines. Dialogue is present in an approved microphone clip. Uncertainty needs source review rather than automatic attribution to Vaarattu.

## Prompt/input/output efficiency and medium reasoning

The current transcript representation is sensible: deterministic original-text passages, six-minute owned cores with 90-second overlap, and precise word IDs near verifier cuts. Keep canonical word timing. Do not replace it with LLM summaries or per-word timestamps throughout discovery. All saved v6 response instructions match the 4,655-character baseline system prompt; the implemented v7 prompt is 4,362 characters. Native JSON schema is supplied once through the API.

| Run | Request records | Input tokens | Output tokens including reasoning | Reasoning tokens | Recorded response time |
|---|---:|---:|---:|---:|---:|
| Latest | 82 | 337,876 | 25,660 | 13,906 | 10.72 min |
| Rerun | 72 | 367,008 | 14,951 | 8,155 | 7.63 min |
| Older, including recovery | 91, two unknown | 438,262 known | 24,373 known | 15,503 known | 10.33 min known |

All ledger entries request Luna `low`. Latest discovery used 62 calls; verification used twenty. The largest saved output across these runs was 841 tokens, including reasoning. No evidence suggests that output truncation or an export quota constrained yield. Codex routing deliberately omits an enforceable output limit; API routing's 4,096 planning cap is a different path. Reported cached input tokens were zero.

Keep the prompt smaller by replacing repeated hook/payoff/game-knowledge exceptions with one consistent editorial rubric. Avoid adding more examples for every failure. A lean discovery result can contain anchors, category, a brief selection reason, and only the ranking data still needed; final titles and summaries are currently generated twice and discovery's versions are intentionally hidden from verification. Removing them from discovery can reduce redundant work, but is a modest optimization while responses are this small. Keep brief empty-section feedback—it is valuable for auditing recall.

The creator is the authoritative editorial decision-maker. v7 retains model-rejected and low-score proposals with review notes; source anchor and media integrity validation still apply. Review disapproval must not automatically become a negative training label for discovery. Use the optional reason and specific boundary feedback to distinguish “not something I would post” from an actual cut defect.

**Medium reasoning is worth a controlled comparison, not a promised fix.** Official Luna documentation lists medium as supported and the default; the app explicitly chooses low. More reasoning might help scanning mixed discussions and handling context, but can also reinforce the current overly strict target. [Official Luna documentation](https://developers.openai.com/api/docs/models/gpt-5.6-luna).

First freeze the current transcript and a small creator-rated evaluation set: approved clips, poor clips, the age anecdote, the five score-blocked accepts, short jokes, incomplete thoughts, and long pauses. Compare low versus medium with identical prompts and source text, separately for discovery and verification. Include empty sections; testing only existing candidates cannot measure discovery recall. Measure useful clips found, creator approval, boundary corrections, missed qualifiers, tokens, and latency. Keep an unseen sample and repeat a few ambiguous cases to distinguish setting effects from sampling variation. No such inference experiment was run for this audit.

Saved raw outputs include the human-readable `reason` field and v6 section feedback. Reasoning token counts exist, but reasoning summaries are empty; there is no full internal reasoning transcript to audit. Exact input text is not saved in raw Responses artifacts, although instructions/schema, source transcript, and request hashes are. Saving the rendered request input or a versioned request artifact would make later exact replay easier without requesting longer reasoning explanations.

## NVENC

Yes. A read-only local check confirmed FFmpeg 8.0.1 exposes `h264_nvenc`, `hevc_nvenc`, and `av1_nvenc`; the installed GPU is an RTX 4090 with driver 610.88. FFmpeg supports NVIDIA hardware encoding. [NVIDIA's FFmpeg documentation](https://docs.nvidia.com/video-technologies/video-codec-sdk/13.1/ffmpeg-with-nvidia-gpu/index.html).

There are two CPU H.264 encoding points: section acquisition (`youtube.py:93`, x264 fast/CRF18) and final composition (`render.py:293`, x264 medium/CRF20). Start with the final encoder, retain H.264/AAC, and benchmark an actual composed clip for speed, text detail, gameplay quality, size, and decode/sync checks. NVENC uses its own quality/rate controls; CRF20 is not directly interchangeable. Software crop/scale/stack/subtitle filters remain CPU work, so this is not automatically an all-GPU pipeline or a guaranteed speed multiplier. Change section encoding separately because its timestamp/alignment behavior is important. No NVENC encode benchmark was run.

## Remaining work, in priority order

1. Review the previously hidden candidates after a user-started v7 recovery. Record optional reasons such as **needs more context/longer**, **trim start/end**, **long pauses**, **would not post**, and **captions/audio**. These are free-text examples, not new required classification buttons.
2. Evaluate the revised discovery prompt on known empty-section misses as well as selected candidates. The latest recovery cannot discover clips from previously empty sections. More useful candidates is the target; a lower approval percentage alone is not a regression.
3. Implement conservative synchronized long-pause editing as a separate revision, with source-to-output timing and listening checks. This remains a design recommendation, not implemented behavior.
4. Benchmark final-render NVENC and compare low/medium reasoning on a small fixed sample. No encoder, model effort, silence-removal, or minimum-duration setting was changed in this task.
5. Reduce duplicated discovery title/summary output if measurement justifies it; save exact rendered request inputs for reproducible evaluations. Larger output budgets, longer system prompts, and extra model passes for every clip are not supported by this audit's evidence.

## Verification of implemented changes

98 targeted offline Python tests passed across selection, recovery, storage, review API and delivery; five Node UI behavior tests passed; Ruff lint passed on changed Python modules/tests. Tests include all twenty distinct proposals surviving reject/low-score notes, overlapping proposals, invalid-boundary fallback, persistent optional reasons, old review-schema upgrade, stale revision protection, and preservation of earlier recovery exports. Two existing third-party deprecation warnings were reported. No live browser review, model-quality comparison, actual render, or production database migration was performed.
