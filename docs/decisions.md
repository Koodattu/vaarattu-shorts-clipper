# Project brief and decisions

Design date: 2026-09-06. These are proposed implementation decisions unless explicitly marked as user-confirmed. This document describes the intended product, not existing software.

## Product goal

Build a usable inventory of shorts from [VaarattuVODs](https://www.youtube.com/@VaarattuVODs/videos), prioritizing what Juha says: opinions, observations, anecdotes, explanations, jokes and conversational exchanges. A viewer should understand the point without knowing the current game, previous hours of the stream, or a missing chat message.

Gameplay remains the visual backdrop. Loud reactions, boss kills and chat floods are not inherently valuable for this product. A calm story with a complete point can be a better clip than a noisy game event.

Success is useful finished clips per processed VOD hour and low correction effort. One post per day is a possible consumption rate, not a quota that forces the system to invent worthwhile moments.

## User-confirmed choices

- This is a fresh project focused on one channel. In particular, highlight-clipper stays independent.
- Use the current Windows PC for processing.
- Generate a ready-to-post folder automatically; Juha handles publishing. Normal successful clips do not require approval before export.
- Start with a UI on the processing PC; private remote access can come later.
- Initially select one YouTube VOD and press Run; automatically complete all stages through ready-to-post files. Only one VOD can be queued/active. Newest-first channel/backlog processing is deferred until this works reliably.
- Acquire full audio and only the selected video sections.
- Prefer Gemma 4 through llama.cpp, using the same model family as highlight-clipper and video-generator. Aim for the largest useful model fully on the GPU; unload ASR completely before loading the LLM.
- Implement only local faster-whisper Turbo for STT now; other ASR remains research for later. Support local and selectable API-based text evaluation, researching all five requested providers in [models-and-providers.md](models-and-providers.md).
- Keep all downloaded models and model-library caches inside this project, under `.cache/`.
- Implement the single-video path end-to-end, but do not run the application, download models/media or perform inference during this implementation pass. Offline code checks are separate from live validation.
- Use stream activity when available, without requiring historical coverage.

## Decision log

| ID | Decision | Reason and tradeoff | Revisit when |
|---|---|---|---|
| D01 | Python 3.12 + uv | ASR, media orchestration and validation are Python-friendly; match the working patterns in reference projects. C# would still need Python inference; a TypeScript backend adds a language boundary without solving a current need. Pin exact dependencies after the runtime spike. | Native packaging becomes a real requirement |
| D02 | FastAPI serves a static HTML/CSS/JS UI; separate Python worker in the same package | A video element, timeline and crop editor need browser JS, but a separately hosted React/Next service is unnecessary for one local user. A plain page also avoids tying worker lifetime to UI requests. | Editing complexity justifies a frontend build system |
| D03 | SQLite + files; one worker | Durable progress with little operational overhead. This explicitly limits the first version to one machine. | More than one processing machine or concurrent users |
| D04 | Original compressed audio, not mandatory MP3 | YouTube audio-only Opus/AAC avoids an extra lossy encode and works as ASR input. Decode bounded PCM chunks as needed. MP3 is an optional convenience export, not the canonical source. | A tool requires a different audio format |
| D05 | Full transcript coverage in bounded overlapping LLM windows | A quiet, interesting passage must be eligible even without keywords/chat peaks. More text is evaluated, but coverage and cost are measurable. | A measured retrieval filter preserves recall while saving substantial work |
| D06 | Anchor-based candidate output and a separate verification pass | Prevent invented timestamps, inspect setup/payoff and preserve meaning. Two passes cost more than one; run the second only on the shortlist. | A simpler tested approach achieves the same useful yield |
| D07 | Local ASR first; Gemma 4 local plus five planned API provider choices | Turbo is the ASR baseline with Whisper/Parakeet challengers. Test Gemma 31B Q4 and 26B-A4B Q4; provider keys enable explicit selection, never silent paid fallback. Meta API details still need verification. | The Vaarattu benchmark identifies a better profile |
| D08 | Chat is optional supporting evidence | Historical gaps and alignment uncertainty are expected; popularity is not editorial quality. | An ablation shows better discovery without material false positives |
| D09 | Preset camera/gameplay crops + FFmpeg ASS captions | Predictable, inspectable and inexpensive for a single creator. Unknown layouts require calibration; automatic face tracking is deferred. | Layout changes make presets too laborious |
| D10 | Contiguous clips in v1 | Keep the original delivery and chronology. No automatic sentence splicing, silence removal or rearranged hooks. | Real examples justify a separately evaluated editing feature |
| D11 | Automatic export after checks, exceptions held aside | Matches the requested ready-folder workflow. Optional review improves individual clips without blocking all successful exports. | Juha changes the publishing/review preference |
| D12 | Prove quality before archive scale | Old-project integration evidence does not establish useful clips. A small actual-VOD benchmark determines promotion. | Always retained as a release criterion |
| D13 | One selected VOD, one Run action, complete automatic pipeline | User-confirmed initial scope. Internal durable stage jobs are allowed; a second queued/active VOD is not. A candidate-only report is an implementation checkpoint, not the first product deliverable. | Single-video acceptance gates pass and backlog work is deliberately enabled |
| D14 | Separate owned ASR/LLM processes with verified exit between stages | User-confirmed GPU sequencing frees the 24 GB card for the largest useful local LLM. Keep each model warm only within its own stage; optional caption refinement follows the same rule. | Hardware or product needs change |
| D15 | Turbo-only STT and project-local `.cache/models/` | Latest user-confirmed implementation scope; alternatives stay in research. Explicit preparation localizes actual weights as well as metadata caches. | User requests another ASR option |

Revision history: the initial 2026-09-06 proposal used Qwen3.5-9B and Gemini 3.5 Flash-Lite and introduced batches early. The user's subsequent direction supersedes those choices with Gemma 4, broader provider research and single-video-only initial operation. No previous recommendation was implemented or benchmarked.

## Starting editorial defaults

These are tuning defaults, not platform rules: aim for 30–60 seconds; allow 20–90 seconds for automatic output. Favor natural beginnings and completed thoughts over an exact duration. Keep original Finnish register, names and code-switching. Do not rewrite spoken opinions or remove profanity by default. Suggest a short Finnish title faithful to the actual clip, without invented claims or hashtags.

Shortlist up to 10 candidates per VOD and automatically deliver up to 3 that pass the checks. Zero is a valid result. The first version stops after the selected VOD; Juha may select another afterward. A later backlog mode can continue to older VODs to build inventory. A weak VOD does not need a consolation clip. Longer shorts, translations, automatic posting, multi-channel profiles, embeddings and visual reasoning are outside the first release.

## Known unknowns and working assumptions

| Unknown | Working assumption / next evidence |
|---|---|
| Best Finnish ASR and editorial model | Run the benchmark; published general benchmarks cannot answer this |
| Camera position and layout changes across years | Calibrate using actual selected frames; keep dated/VOD-specific presets. No fabricated coordinates |
| Overnight availability and streaming schedule | Manual start/pause initially; background processing never assumes this PC is idle |
| Storage location | Start under ignored `workdir/` in this repository with a bounded cache; offer a configurable local data root at setup. E: has more observed free space, but has not been selected or written to |
| API budget/provider credentials | Local mode is the zero-API-cost default; current prices and cap behavior are in models-and-providers.md. Caps must be explicitly configured for the chosen provider; no API inference or credentials are configured yet |
| Chat time alignment | Stream identity can be matched separately from an offset; use no chat boost until timing is confirmed |
| Historic camera-off/multi-speaker segments | Flag uncertain speaker/layout cases; visual correctness cannot be inferred from the transcript |

## Scope of this planning pass

Repository and public metadata research, architecture, contracts, evaluation design and implementation ordering. No media was downloaded, no model was run, no dependency was installed, and no reference project or remote service was modified. The public stream API proposal is documented work for a later change in vaarattu.tv, not a claim that it has been deployed.
