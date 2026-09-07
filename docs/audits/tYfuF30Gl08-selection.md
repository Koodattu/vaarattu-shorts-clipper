# Selection audit: tYfuF30Gl08

Audited 2026-09-07. Run `ed8155b3d8a24001be117a2f335bee78`, saved selection version `conversation-v5`. Read-only analysis of saved artifacts; no model calls, processing, rerendering, state changes or selection-rule changes.

## What happened

Full VOD duration: **6:03:50.061**. Saved transcript: **19,984 words**, grouped into **4,543 passages**. All **61 discovery windows** have contiguous recorded coverage; normally six-minute owned regions plus 90 seconds of context on each side. Coverage means the transcript sections were submitted successfully, not that ASR was perfect or that every worthwhile moment was recognized.

**16 raw proposals → 4 invalid passage anchors removed → 12 proposals → 2 below the five-second minimum removed → 10 verification targets → 2 eligible clips.** No duplicates were removed and no clip-count cap applied.

47 of 61 discovery windows returned an empty candidate list; the other 14 produced 16 proposals. Those empty responses contain no rejected-alternative inventory. This audit cannot reconstruct why individual moments inside them were omitted, and does not establish that the VOD contained only two worthwhile clips.

## The ten verification targets

| Source time | Suggested title | Recorded decision / actual gate |
|---|---|---|
| [1:02:51](https://www.youtube.com/watch?v=tYfuF30Gl08&t=3771) | LoL:n lopettaminen paransi elämää | Exported: accepted, all required scores pass, no flags. |
| [0:59:15](https://www.youtube.com/watch?v=tYfuF30Gl08&t=3555) | Seitsemän vuoden odotus päättyi | Not eligible: standalone 2/4 and missing team/game context flag, despite outcome=accept. |
| [1:04:56](https://www.youtube.com/watch?v=tYfuF30Gl08&t=3896) | Kun pelaaminen ei ollut vielä niin vakavaa | Not eligible ONLY because of a flag about game terminology. Accepted; standalone/substance/fidelity 3/4/3. |
| [3:40:15](https://www.youtube.com/watch?v=tYfuF30Gl08&t=13215) | Kun mikin etäisyys ratkaisee kaiken | Verification validation failed twice: model moved the idea to a later passage. Both raw replies were valid JSON and said accept. First also flagged another speaker; the second did not. Saved as needs_context. |
| [0:01:37](https://www.youtube.com/watch?v=tYfuF30Gl08&t=97) | Miten alamäki ärsytti polvea | Not eligible ONLY because of health-topic and unclear-ending flags. Accepted; required scores all 3. Payoff 2 did not cause the rejection. |
| [1:51:42](https://www.youtube.com/watch?v=tYfuF30Gl08&t=6702) | Animea kyllä, mangaa ei juuri koskaan | Not eligible: substance 2/4, despite outcome=accept. No flags. |
| [3:13:15](https://www.youtube.com/watch?v=tYfuF30Gl08&t=11595) | WoW-hahmoluokka on persoonallisuustesti | Exported: accepted, required scores 3/3/4, no flags. Opening 2 was correctly allowed. |
| [1:20:46](https://www.youtube.com/watch?v=tYfuF30Gl08&t=4846) | Tehokas tapa tehdä voittoa | Not eligible ONLY because of unclear-transcription flag. Accepted; required scores all 3. |
| [2:41:13](https://www.youtube.com/watch?v=tYfuF30Gl08&t=9673) | Uusi classi on aina OP | Editorial reject: standalone 1, substance 2; depends on game terminology and prior discussion. |
| [3:09:37](https://www.youtube.com/watch?v=tYfuF30Gl08&t=11377) | Väitteille ei löydy videomateriaalia | Editorial reject: standalone 1, substance 2; depends on damage numbers and previous context. |

The final eligibility gate requires outcome=accept, standalone >=3, substance >=3, fidelity >=3, **no flags at all**, and a 5–60 second interval. Opening and payoff have no minimum. The model can say accept while the application discards its result. The three flag-only exclusions are the clearest evidence of over-filtering. Their flags are not all equivalent: game vocabulary or a topic label can be advisory, while unclear words that change meaning still merit inspection.

**These selection flags are separate from render warnings.** The recent change to export successfully rendered videos with warnings does not change this earlier gate. A clip discarded here never reaches rendering.

## Four proposals lost to passage-anchor validation

Every referenced ID in these four suggestions exists in the transcript. However, discovery exposes only passage endpoints and requires the idea marker to be a passage start. Three suggestions used an interior idea-word ID; the Dwarf suggestion also used an interior start-word ID. No candidate-specific correction was attempted at this point.

| Source time | Suggested title | Exclusion |
|---|---|---|
| [0:12:16](https://www.youtube.com/watch?v=tYfuF30Gl08&t=736) | Enempää en pyydä elämältä | Real transcript IDs, but idea marker is not a permitted passage start. |
| [4:18:23](https://www.youtube.com/watch?v=tYfuF30Gl08&t=15503) | Kaikesta voi joustaa – paitsi tästä | Start and idea markers are interior words, not permitted passage starts. |
| [5:28:07](https://www.youtube.com/watch?v=tYfuF30Gl08&t=19687) | Viime kesä meni seinää tuijottaessa | Real transcript IDs, but idea marker is not a permitted passage start. |
| [5:40:12](https://www.youtube.com/watch?v=tYfuF30Gl08&t=20412) | Kun päivitysmuistiinpanot eivät kerro mitään | Real transcript IDs, but idea marker is not a permitted passage start. |

The painting-story suggestion is especially relevant to the requested everyday/general subject matter: discovery gave standalone 3, substance 3, fidelity 4, and no flags. It was never independently verified. It is a review candidate, not a confirmed missed good clip; its transcript also contains recognition errors and surrounding conversational setup.

## Two proposals removed before verification for length

| Source time | Suggested title | Exclusion |
|---|---|---|
| [1:11:52](https://www.youtube.com/watch?v=tYfuF30Gl08&t=4312) | Kun kukkakimppu ei teekään vaikutusta | 4.64 seconds of selected speech; below the 5-second pre-verification minimum. |
| [0:58:19](https://www.youtube.com/watch?v=tYfuF30Gl08&t=3499) | Yksi stream ei enää riitä | 4.26 seconds of selected speech; below the 5-second pre-verification minimum. |

These durations are tested before the automatic cut adds speech padding. The flower joke also deserves source/context inspection: the discovery paraphrase is not itself proof that the model interpreted the joke correctly. Do not automatically restore every lost proposal.

## Chat and model requests

Chat enrichment was active for aligned stream 253. Two additional regions were evaluated: 00:12:30–00:16:30 and 02:30:30–02:34:30. Both returned empty candidate lists. Neither exported clip came from the chat pass. Detected peak buckets had five active chatters against a local baseline of two; this coarse signal does not establish that speech was interesting.

75 completed model requests: **61 full-transcript discovery + 2 chat-peak discovery + 12 verification calls for 10 targets**. Microphone and layoffs verification each used one additional repair request; the microphone repair still failed the same-idea-passage constraint. Raw responses identify **gpt-5.6-luna**, reasoning effort **low**.

Tokens from saved usage records: **382,757 input**, **18,212 output**, including **12,276 reasoning tokens**. Cached input: 0. Recorded request elapsed time sums to 473.391 seconds; this is not end-to-end pipeline runtime. Codex subscription usage has no dollar-cost estimate here.

## Recommended next changes (not implemented by this audit)

1. Separate advisory selection notes from blocking concerns. Keep score/meaning/context gates, but do not discard an accepted candidate just because a free-text flag exists. The old-LoL discussion is the clearest first review target.
2. Recover valid interior idea IDs by mapping to their containing displayed passage while preserving ownership and bounds, or make a bounded correction request. Do not permit arbitrary unseen IDs. Record the exact validation failure instead of the generic unreadable label.
3. Review the five-second minimum and the same-passage refinement rule deliberately. A nearby later joke may be a valid separate candidate, but jumping to an unrelated idea should not silently count as successful verification.
4. Build a labelled evaluation set: retain the two user-approved clips, review the discarded promising candidates, and sample empty windows throughout the VOD. This distinguishes detection misses from application filtering and gives a basis for comparing prompts/models without simply requesting more clips.

## Evidence

- [Selection checkpoint](../../workdir/runs/ed8155b3d8a24001be117a2f335bee78/selection.checkpoint.json)
- [Discovery plan](../../workdir/runs/ed8155b3d8a24001be117a2f335bee78/inference/d94f40f7ed493b37/discovery-plan.json)
- [Recorded selection issues](../../workdir/runs/ed8155b3d8a24001be117a2f335bee78/inference/d94f40f7ed493b37/selection-issues.json)
- [Chat review](../../workdir/runs/ed8155b3d8a24001be117a2f335bee78/inference/d94f40f7ed493b37/chat-peak-review.json)
- [Selection implementation](../../src/vaarattu_shorts/discover.py)

Raw model responses and per-request usage files are alongside the discovery plan. The audit uses their explicit outputs and recorded validation rules; it does not infer private reasoning from token counts.
