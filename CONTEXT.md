# Current project state

Updated 2026-09-06 after end-to-end single-video implementation and the Turbo-only STT correction.

## User intent and confirmed choices

Create a fresh Vaarattu-only tool to find interesting spoken Finnish conversations/opinions/stories in YouTube VODs. Initially select one video and press Run: the whole pipeline automatically produces clips in the output directory. Admit only one queued/active VOD; no automatic next video. Newest-first archive processing comes later. Full audio first; download only selected video ranges. Compose camera above gameplay, add captions and automatically create ready-to-post files. Juha handles publishing. Process on this Windows PC; local UI now, private remote access later. Other projects, especially highlight-clipper, stay separate.

## Current status

The application code, local UI, worker, model preparation commands and offline tests now exist. Base/test dependencies were installed in .venv and uv.lock was generated. The ASR extra, model weights and llama.cpp runtime were not installed/downloaded. No application server, processing run, real media acquisition, inference, render, scheduled job or public API change was performed. Work remains uncommitted. See docs/implementation-status.md for exact scope and deferred features.

Read [README.md](README.md) for the document map and [docs/implementation-plan.md](docs/implementation-plan.md) for ordered milestones. [docs/decisions.md](docs/decisions.md) records decisions, alternatives and unknowns; update it when a choice changes.

Verification: 25 offline tests passed, Ruff lint/format and JavaScript syntax passed, sdist/wheel built offline and required UI/ASR modules were checked in the wheel. Twelve docs/44 local links passed validation. Two third-party test-client deprecation warnings remain. No live runtime or quality gate is claimed.

## Recommended implementation

Python 3.12 + uv; FastAPI with static HTML/CSS/JS; one durable Python worker; SQLite and local artifacts. yt-dlp and FFmpeg. Only faster-whisper Turbo STT is implemented, per the latest user instruction. Other ASR remains research. All model weights and model-library caches stay inside this project under .cache/, with actual weights in .cache/models/. Use Gemma 4 via llama.cpp, testing 31B Q4 first and 26B-A4B Q4 alongside it. Aim for the largest useful fully GPU-resident LLM at tested context, not a small model sharing memory with ASR. Owned ASR process exits before LLM load; LLM exits before any caption-refinement ASR reload. One slot, 16K initial fit test, 32K only after measurement; MTP disabled for the baseline.

Gemini 3.8 Flash, GPT-5.6 Luna, GLM-5.3-Flash and DeepSeek V4 Flash adapters are implemented with fixture checks. Meta Muse Spark 1.3 remains visibly disabled pending a verified API contract. Configure only the selected provider's key; local mode remains independent. Meta release availability is verified, but primary API/pricing documentation was login-gated; its exact contract remains unverified. Current prices, promotion expiries and provider-specific reasoning behavior are in [docs/models-and-providers.md](docs/models-and-providers.md). No Finnish winner, hardware fit or API behavior has been benchmarked. This supersedes the initial Qwen3.5-9B/Gemini 3.5 Flash-Lite recommendation. No embedding/vector DB or generic multi-agent pipeline initially.

Scan the full timed transcript with overlapping context, return source word/segment IDs, verify original surrounding speech, and preserve contiguous source chronology. Chat is optional, with separately confirmed identity and offset. Known layout presets allow automatic output; uncertain layout/alignment/meaning goes to attention. Normal export does not require user approval.

## Verified facts to retain

- PC: i7-13700K, about 64 GB RAM, RTX 4090 24 GB. C: had about 356.5 GiB free; E: about 3,868.2 GiB. Neither throughput nor exact runtime compatibility has been measured.
- Verified YouTube channel ID: `UCUCV40VqBZqt83afjbbICvw`.
- Sample VOD `OJ-bDXbfEos`: title date 2026-07-09, upload 2026-07-16, hydrated duration 22,440 seconds. Flat duration differed by one second.
- Public stream list reported 199 streams. Likely identity match: stream 254, July 9 14:51:00Z to 21:05:31.793Z. Duration difference 31.793 seconds is not a measured start offset.
- vaarattu.tv local source commit `96a2afa` contains activity API; live `/api/streams/277/activity` returned `Route not found`. Matching/list API works; activity deployment remains separate work.
- Current aggregate `activeChatters` is peak minute-distinct chatters in widened chart buckets. It is not whole-bucket uniqueness. Collector timestamps are receipt time. Unknown coverage must not become known zero.
- highlight-clipper's own status says quality promotion is pending. Reuse small timestamp/caption/recovery techniques; do not claim its smoke tests prove clip quality or inherit its architecture.

## Next action

Do not start the product until Juha requests a run. When authorized, follow docs/setup.md to install the optional ASR runtime, configure tools, explicitly prepare Turbo/Gemma, calibrate one source layout and validate a single actual VOD end-to-end. Record fit, timing, output quality and actual costs before backlog work. Models stay project-local; output currently uses workdir/ready. Meta, other ASR, cache eviction, arbitrary output roots and backlog/scheduling remain deferred as documented.

The 2026-09-06 setup/usage follow-up added a durable request token/rate ledger, UI totals and JSON usage downloads. Reported truncated/invalid response usage is settled before content validation; missing usage keeps reservations. Conservative costs do not apply cache/off-peak discounts or reconcile invoices. Local requests record zero API cost. 35 offline tests passed. No app, model, media or paid API was run. Setup docs now include session-only key entry and the remaining Windows GPU runtime prerequisites.

The subsequent secrets-file request added automatic project-root `.env` loading to CLI settings initialization, a blank ignored `.env`, and tracked `.env.example`. Existing environment variables win. The dependency-free parser supports single-line literal values, quotes and comments, without expansion or execution. Setup docs now prefer `.env`. 41 offline tests passed; no app or pipeline was started.

No paid inference or automatic publishing is authorized/configured by this documentation. Do not run migration/deployment commands merely because they appear in reference documents.
