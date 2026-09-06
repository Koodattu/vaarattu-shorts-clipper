# Implementation status

Updated 2026-09-06. The user authorized implementation but explicitly asked not to run the application/pipeline. The latest STT decision is **faster-whisper Turbo only**; other ASR models are research for later.

## Implemented

- Python package/CLI, locked dependencies, local FastAPI/static UI and separate worker launcher.
- Project-root `.env` secrets loading with process-environment precedence, blank local file and tracked example. Values are single-line literals; no extra dependency, shell execution or interpolation.
- One active VOD enforced transactionally, request idempotency, pause/cancel/resume, restart recovery, hashed stage checkpoints and dependency fingerprints.
- Model preparation commands with pinned revisions and manifests under project `.cache/models/`; all inherited model-cache paths overridden locally. No automatic downloads on startup/Run.
- Windows Job Object ownership for subprocess cleanup, GPU mutex, Turbo's finite CUDA process, complete exit before the temporary Gemma llama.cpp server, explicit full GPU residency/context checks.
- One-video channel validation, native audio-only download, bounded chunked/timed Finnish transcription and full transcript-window discovery with a shortlist verifier and source-word validation.
- Local Gemma 31B/26B selection plus Gemini/OpenAI/Z.ai/DeepSeek transports, response parsing, one schema repair, usage reservations and no provider fallback.
- Durable per-request token/rate/cost ledger, per-run UI totals and downloadable JSON reports. Truncated/invalid replies and repair attempts count; cached selections do not double-count. Missing usage remains explicit. Local generation is tracked with zero API cost.
- Optional stream title/date matching and confirmed-offset aggregate chat ranking; missing activity degrades gracefully.
- Selected-range video acquisition, two-anchor waveform alignment, contiguous cuts, 1080×1920 camera/gameplay composition, Finnish SRT/ASS, full decode/container checks and ready/held outputs.
- Screenshot crop editor, source/result playback, caption/boundary/title/layout edits with revision checks, source links and output-folder action.
- Immutable ready revisions, interrupted-promotion reconciliation, local backup command and setup documentation.

## Deliberate implementation boundaries

- No STT model besides Turbo, automatic caption re-transcription, MTP, embeddings, face tracking or sentence/silence splicing. Caption edits use the original Turbo word timeline.
- Meta's requested API remains disabled until its exact primary API documentation is accessible and verified. It is not a fake adapter and is not counted as implemented support.
- Channel library/backfill/scheduling/publishing remain future scope, as requested for the single-video first version. A manual published marker and creator-feedback analytics are also deferred.
- The UI uses a locally supplied source screenshot for first crop calibration. A pre-run YouTube thumbnail/sample downloader is not implemented; metadata validation occurs when the worker starts the selected run.
- One fixed project output root, `workdir/ready`, keeps path handling simple. Choosing an arbitrary external output root from the UI is deferred; all model assets remain strictly project-local.
- Caption layout uses conservative character bounds and a 30 characters/second hold threshold, rather than a font-metric layout engine. The reference font is system Arial; no font was downloaded. Visual validation remains necessary.
- Original mixed audio is preserved without automatic loudness normalization or speaker identification. A calibrated solo-host source assumption enables automatic delivery; this can miss other voices and needs creator evaluation. Layout/scene uncertainty is held; an explicit source review can resolve a scene warning but cannot bypass technical checks.
- No automatic cache eviction or routine housekeeping schedule. Disk/download limits are enforced. Backup integrity is checked on creation; a restore drill remains pending.
- Provider accounting is conservative, not a claim to match invoices exactly. Rates need review as promotions expire. Ambiguous failures keep reservations; access/transport failures require explicit retry.

These boundaries supersede the broader design options in the planning documents. They do not remove the first-version path from one Run action through finished output files.

## Verification evidence

Secrets-file follow-up: **41 offline tests passed** and Ruff lint passed. Added checks cover project-root resolution outside the working directory, process precedence, comments/quoted literal values, optional files, safe parse errors and API non-disclosure. No application or model was started.

Usage-tracking follow-up: **35 offline tests passed**; Ruff lint/formatting, JavaScript syntax and Git diff whitespace checks passed. Added checks cover usage normalization, cached/reasoning subsets, truncated response accounting, repair/cache reuse, unknown usage, zero-cost local calls, restart persistence, report endpoints and preservation of legacy requests. The test run reports two third-party Starlette/AnyIO deprecation warnings; they do not affect the pass result.

The earlier implementation pass also checked formatting, built source distribution/wheel offline with packaged UI assets, and checked Markdown links/fences/JSON and Git whitespace. Those build artifacts predate the usage-tracking follow-up; source/editable installs contain the new ledger.

Tests cover concurrent admission, idempotency, pause/cancel recovery, budget reservations, provider-specific reasoning/schema paths, invalid JSON repair and timeout accounting, source identity, crop validation, chunk ownership, repeated Finnish words, timeline correlation/ambiguity, captions/escaping, full window coverage, stream dates, HTTP origin/token controls, no startup downloads, mocked pipeline sequencing, checkpoint invalidation, technical holds and interrupted export reconciliation.

Dependencies were installed into `.venv` and the package was built during environment setup. ASR runtime dependencies, model weights and external llama.cpp runtime were not downloaded/installed. No inference, real media acquisition/render, application server or worker processing run was performed. Test-created files under `.cache/tests/` are synthetic fixtures, not generated shorts.

## Live validation still required, intentionally not run

1. Install the optional ASR runtime and configure/check FFmpeg, JavaScript extraction runtime and pinned llama.cpp.
2. Explicitly download Turbo and the chosen Gemma into `.cache/models/`; test CUDA compatibility and full GPU residency at 16K, then 32K if useful.
3. Process one selected actual VOD. Record network bytes, ASR coverage/accuracy, unload/reload behavior, model latency and useful candidates.
4. Check early/middle/late selected section mapping against the original audio and inspect actual camera/gameplay layout and Finnish subtitle timing at phone size.
5. Test pause/restart/cancel on real subprocesses, including GPU cleanup and a deliberately interrupted render. Offline ownership tests do not certify the installed Windows runtime.
6. If API mode is desired, configure one key/cap and validate that provider's live responses and actual billing usage. Do not run all providers merely because keys exist.
7. Annotate creator acceptance/missed moments before promoting a model profile or adding backlog processing.

Do not describe these live quality/performance gates as passed until their results exist.
