# Evaluation and implementation plan

Status: historical implementation order and quality gates. The single-video code path is now implemented; see [implementation-status.md](implementation-status.md) for exact scope and verification. Live gates below remain unrun because the user requested implementation without running the product. The latest user direction limits implemented STT to Turbo; other ASR comparisons are deferred research.

Initial product acceptance is explicit: choose one VOD, press Run once, and receive finished clips in the selected output directory. Only one queued/active VOD is admitted. Milestone 1 is an internal checkpoint; milestone 2 is the first complete product path. Channel enumeration, multi-VOD batches and automatic next-video processing are deferred to milestone 5.

## Milestone 0 — Prove the risky parts on this PC

Deliver a small reproducible experiment and results document, not the entire app.

- Select one recent VOD with a likely stream match (the July 9 example is a candidate), one older VOD without usable chat and one speech-heavy segment. Choose actual media deliberately; no archive-wide download.
- Verify audio-only acquisition; probe the resulting file and record network bytes. Acquire a few 30–90-second video ranges at beginning/middle/late positions and verify audio/video offsets against the full audio. Record any range request that downloads unexpectedly much data.
- Load Turbo and large-v3 separately on the RTX 4090; compare Finnish-NLP Whisper and Parakeet TDT 0.6B v3 on the same fixed Finnish sample. Verify the native NeMo-Speech.cpp Windows/CUDA path and timestamp output before selecting Parakeet. Record cold/warm time, word alignment, silence hallucinations and compatibility; an alternative runtime failure is a result, not a reason to block the working baseline.
- Test Gemma 4 31B Q4 and 26B-A4B Q4 through pinned llama.cpp on timestamped Finnish text, measuring editorial/schema quality, full GPU residency, throughput and actual memory at 16K context/one slot. Test 32K only after the first fit; MTP is a separate later variant. Demonstrate ASR process exit before LLM load and LLM exit before ASR reload. API benchmarking requires an explicitly configured provider/budget; no silent paid fallback.
- Calibrate one actual camera/gameplay preset and render a single manually chosen known-good excerpt with Finnish captions.

Pass when a real media section can be delivered with verified source timing and usable captions, and at least one local ASR/LLM profile runs. If range download or timeline mapping fails, solve that bounded problem before designing more UI. Record exact binary/model hashes and parameters in the experiment; metadata-only success from planning is not this gate.

## Milestone 1 — Audio-to-candidates vertical slice

Add `pyproject.toml`, a locked environment, minimal CLI, ignored workdir, versioned contracts, SQLite inventory/jobs and source adapters. Dependencies are selected/installed in that implementation task; none were added during planning.

Implement one explicit channel VOD URL/ID → validated metadata → audio-only acquisition → checkpointed timestamped ASR → confirmed ASR unload → full-coverage Gemma discovery → boundary/context verification → confirmed LLM unload → inspectable candidate JSON/HTML report. Enforce single-VOD admission in the database, snapshot selected profiles and retain resumable stage jobs inside that run. Record raw model output, scanned/failed intervals, costs and timing. Add the local LLM adapter first; provider adapters use the same typed contract and offline fixtures. Do not add a batch or channel scan here.

Pass when a long VOD finishes or reports explicit partial coverage; restart does not retranscribe valid completed chunks; at least a few useful spoken moments can be inspected with exact source links/audio. A run with zero good moments can be correct; compare with creator annotations rather than requiring output by fiat.

Relevant checks: source identity with underscores, wrong-channel/playlist rejection, double-click idempotency and simultaneous admission, metadata hydration, no-video audio selection, nonzero-PTS mapping, chunk-seam words, unknown/reversed anchors, malformed/schema-valid-but-wrong output, full coverage on dense prompts and silence, late-VOD discovery, cancellation/restart, retry caps and no simultaneous owned ASR/LLM processes.

## Milestone 2 — Single-video Run through automatic delivery

Implement the minimal FastAPI Run page plus selected-range caching/alignment, edit recipes, a calibrated layout preset, deterministic FFmpeg crop/stack render, Finnish phrase captions, QC and ready/held outcomes. The page accepts one video and profile/output choices, shows progress, and restores the active run after reopening. Run advances every stage automatically and renders up to three eligible candidates. Write delivery packages and a run index; show Open output folder. A manual initial crop/source calibration is permitted; normal calibrated output must not require candidate approval or separate stage buttons.

Pass when one Run action produces actual clips with clean beginnings/endings, visible camera and gameplay, synchronized Finnish captions and complete ready-folder packages. Closing/reopening the UI preserves the run; a second video cannot be queued while it is active; completing it does not start another. A valid zero-clips result and held exceptions are visible. Invalidate only relevant outputs after edits; do not redo expensive discovery when a crop changes.

Relevant checks: early/mid/late ranges, shared downloads, clipped handles at VOD edges, A/V drift, source frame-rate variation, wrong presets, caption diacritics/control characters, final word retention, full decode, disk-full/partial output and stale-revision fencing. Visually inspect representative outputs; mocked FFmpeg tests are insufficient.

## Milestone 3 — Editing polish and provider selection

Extend the existing Run/results UI with candidate audio/video/context, crop calibration, trim/caption fixes and manual published markers. Complete explicit pause/resume/retry behavior and the five planned API provider selections: Gemini 3.8 Flash, GPT-5.6 Luna, GLM-5.3-Flash, DeepSeek V4 Flash and Meta Muse Spark 1.3. Use only the selected key and provider-specific capabilities/budget controls. Resolve Meta's exact endpoint/model/schema/access documentation before claiming its adapter is supported; that uncertainty does not block local or verified providers. Test invalid/empty responses, reasoning usage, cap exhaustion and missing keys with fixtures. Verify live paid behavior only after credentials and a budget are configured, otherwise label it unverified. Ship a local setup guide with actually tested commands, model/runtime pins, cache cleanup and backup.

Pass when Juha can run one selected VOD, close/reopen the UI, find outputs, fix a caption or boundary and get a revised short without touching JSON. Configured supported providers are selectable without code edits; unavailable/unverified providers are clearly labelled, with no silent fallback. Held exceptions state what needs fixing. Restart/cancel cannot orphan a GPU model or produce duplicate ready clips. Single-VOD admission, local-only boundaries and media range handling remain verified. Record incomplete provider integrations separately rather than describing all five as tested.

## Milestone 4 — Optional stream enrichment

Implement local title/date/duration matching using the existing list API first. Add an offset-confirmation UI and cache aggregate activity when the endpoint becomes available. In a separately scoped vaarattu.tv change, deploy/verify existing activity or build the proposed finer public API. No clipper DB access to the stream service.

Pass when an aligned match contributes traceable evidence, uncertainty is explicit, and an outage/404/unmatched older VOD still completes normally. Compare matched VODs with chat on/off; retain the boost only if it improves useful discovery or selection.

Relevant checks are listed in [stream-data.md](stream-data.md). This milestone is not a prerequisite for the first deliverable clips.

## Milestone 5 — Quality promotion, then backlog scale

Freeze the winning profile based on the development corpus and evaluate unseen recordings one VOD at a time. After the single-video path passes quality/recovery gates, deliberately add channel inventory, newest-first hydration/order and a bounded backlog queue, then process a modest pilot batch before enabling thousands of hours. Add bounded idle/overnight scheduling only after the user chooses available hours and the worker survives an overnight soak. Suggested inventory policy: build approximately two weeks of good ready clips, then pause until inventory falls; this is a proposed later setting, not an automation created by this plan.

Pass when the quality report, stability report and measured storage/time/cost support routine operation. Archive scale should increase queue depth, not require a new infrastructure architecture. Consider automatic publishing only as a separate later product decision.

## Benchmark design

### Start small, then measure whole-VOD discovery

For development, take three one-hour blocks from different VODs/eras, sampling quiet conversational speech, game-heavy speech, music/silence, overlapping voices and Finnish/English code-switching. Include ordinary negatives and contexts where a promising sentence has a qualifier later. Annotate worthwhile moments by listening before looking at model proposals; record acceptable alternate boundaries and whether the visual layout works.

Hand-correct 15–30 minutes spread across those blocks for ASR evaluation, preserving a verbatim transcript plus a documented normalization for WER/CER. Annotate at least 100 word onsets/endings for timing, plus known silent/music stretches for hallucination checks. Keep owners' annotations, model outputs and results as separate versioned artifacts.

Once a configuration is selected, freeze at least one entirely unseen complete VOD and annotate the complete recording for a recall test. Add an unseen chat-covered VOD for the chat ablation if the first lacks data. Do not call recall on only selected interesting segments whole-VOD recall. A larger pilot can collect 20 or more judged delivered clips across enough additional VODs to assess automatic ready-folder precision; at three deliveries per VOD this requires at least seven VODs if every one yields three.

If human annotation time is limited, report only the evidence actually collected (for example sampled precision/ASR error). Do not replace missing recall evidence with LLM self-grading or claim a completed quality gate. New tuning after a holdout run requires a fresh holdout or an explicit exploratory label.

### Separate the failure stages

| Measure | Definition and purpose |
|---|---|
| ASR WER/CER | Compare raw and normalized Finnish against hand transcription; separately count errors in names, negations/numbers and code-switching |
| Timing error | Median/p95 absolute word-boundary error on hand-aligned words; inspect caption onset/exit separately |
| Hallucination | Invented speech events per labelled silent/music minute; don't hide these in aggregate WER |
| Discovery recall | Fraction of labelled worthwhile moments reached by a raw candidate containing the annotated idea/payoff anchor, before ranking |
| Final recall@10 | Same reference matching on the final top 10 per VOD; one candidate can match at most one reference |
| Boundary usefulness | Human complete-start/complete-end labels plus start/end error and temporal IoU against acceptable intervals |
| Ready precision | Fraction of auto-delivered clips Juha would actually post with only minor/no changes; define major edits as changed excerpt/meaning, layout failure or material caption rewrite |
| Duplication | Repeated same moment/point among output, across overlapping windows and VODs |
| Product yield | Useful ready clips per processed VOD hour; correction/inspection minutes per useful clip |
| Runtime/cost | Cold/warm ASR and LLM time separately, total wall time, bytes transferred, peak VRAM/RAM/disk, tokens and spend per VOD and per useful clip |

Use one-to-one moment matching so many near-identical candidates cannot inflate recall. Report per-VOD and per-language/scene slice, not just a pooled mean. Store failure examples alongside summary numbers.

### Minimal comparisons

1. Turbo vs large-v3 vs Finnish-NLP fine-tune vs Parakeet TDT v3 on the same audio. Record model-specific decode/VAD/chunk settings and compare both transcription and word timing. Native whisper.cpp is a separate runtime experiment when useful, not a new model-quality claim.
2. Gemma 4 31B Q4 vs 26B-A4B Q4 on identical transcript windows/prompts, both fully GPU-resident after ASR exit. Compare the four documented API models when configured, and Meta once its API/access is verified. Record thinking settings, repairs, actual input/output/reasoning tokens, rate tier, cost and Finnish editorial failures. Qwen3.6-35B-A3B is a secondary reference-family challenger if Gemma leaves a demonstrated gap.
3. Simple transcript scan vs transcript scan + context/boundary verifier. Compare source fidelity and useful yield, not just prettier reasons.
4. Uniform-time or simple speech-activity sampling as a sanity baseline, plus chat-on/off when alignment exists. Compare ranking without chat on identical proposals and end-to-end discovery separately if chat adds a second-look pass.

No broad tournament of dozens of models, embedding pipelines or multi-agent judges initially. Every added stage must justify its cost with a measured improvement.

### Proposed promotion targets

Freeze target values before the held-out run and label sample counts:

- At least 70% of annotated usable moments reached before ranking, and at least 60% in the top 10, on the annotated holdout. Report sparse/empty recordings separately.
- At least 70% of a pilot's auto-delivered clips judged ready with only minor/no edits, with the exact numerator/denominator shown; do not treat a small sample as a population guarantee.
- No known change of meaning through missing negation/qualifier, wrong speaker attribution or invented caption text in the inspected exports. Such failures block automatic promotion of that profile until understood.
- At least 90% of inspected cuts have complete starts/endings; duplicate delivered moments below 10%.
- Initial subtitle timing target: median error ≤150 ms and p95 ≤400 ms on the aligned sample; no persistent A/V drift. Tune style only after timing works.
- Every delivered artifact passes technical decode/container checks. Force interruption at major stages and show recovery without duplicated outputs or lost edits.
- Planning performance target: one typical six-hour VOD through analysis and a few renders within a two-hour unattended session on this PC. This is an experiment target, not a promise. Revisit it after measuring; prefer useful yield over forcing a bad fast model.

If recall fails, inspect missed windows and ASR before adding retrieval models. If proposals are useful but boundaries fail, improve verification/timing. If captions fail, fix ASR/refinement/layout rather than rewriting spoken text. If chat adds false positives, disable its boost. This keeps failures tied to the stage that caused them.

## Verification during implementation

Use `pytest` for meaningful contract, timebase, recovery and media fixtures; add a small smoke command with actual local models/tools, and label those slower checks explicitly. Run Ruff/type checks only once configured in the new repository. Fake providers test orchestration; they cannot pass the quality gate. No tests or build were run for nonexistent application code during this documentation-only pass.

Before each milestone is called complete, update [CONTEXT.md](../CONTEXT.md) with what is implemented, what was actually tested, measured results and the next blocking uncertainty. Keep research/model claims dated and preserve the decision history when choices change.
