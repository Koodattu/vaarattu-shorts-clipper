# Vaarattu Shorts Clipper

A new, independent project for finding worthwhile conversations in Vaarattu's Finnish stream archives and turning them into captioned vertical shorts.

**Status: the single-video application is implemented, with offline verification. It has not been started on real models/media, at the user's request.** No Finnish quality, GPU fit or end-to-end performance is claimed yet. See [setup](docs/setup.md) and [implementation status](docs/implementation-status.md).

The first workflow is: select one VOD and press **Run** → download audio → transcribe locally → unload ASR → load the local LLM (or call the selected API) → find and verify self-contained spoken moments → unload the LLM → download only selected video ranges → compose camera above gameplay → add Finnish captions → automatically save a ready-to-post folder. Juha handles publishing. Only one VOD can be queued or active initially; newest-first channel discovery and backlog processing come later.

## Implementation

- Python 3.12 with `uv`, a FastAPI app serving a small HTML/CSS/JavaScript UI, and one separate durable worker.
- SQLite and local files on the processing PC. No separate hosted frontend, Redis, or distributed queue initially.
- Local faster-whisper Turbo is the only implemented STT model. Other STT options remain research for later.
- Local Gemma 4 through llama.cpp: test 31B Q4 first and 26B-A4B Q4 alongside it, aiming for the largest useful model fully on the GPU. ASR and LLM never stay resident together.
- Implemented API transports for Gemini 3.8 Flash, GPT-5.6 Luna, GLM-5.3-Flash and DeepSeek V4 Flash. Meta Muse Spark 1.3 remains disabled pending its verified API contract; live provider tests are still pending.
- All downloaded models live in this project's `.cache/models/`, with adjacent project-local library caches. Downloading models is an explicit setup command; startup and Run do not download them.
- yt-dlp for metadata/audio/range acquisition; FFmpeg for exact trimming, two-panel composition, captions and validation.
- Optional aggregate chat enrichment from vaarattu.tv. Missing or unaligned chat never prevents speech-based discovery.

Confirmed target machine: Windows, i7-13700K, RTX 4090 (24 GB), approximately 64 GB RAM. The UI runs on this machine; private remote access is a later extension.

## Planning documents

| Document | Purpose |
|---|---|
| [Setup and operation](docs/setup.md) | Environment, project-local models, keys, commands and recovery |
| [Implementation status](docs/implementation-status.md) | Implemented scope, explicit limitations and unrun live checks |
| [Project brief and decisions](docs/decisions.md) | Scope, user choices, architecture decisions and remaining questions |
| [Architecture](docs/architecture.md) | Processes, persistence, recovery, local UI and operations |
| [Discovery and transcript contracts](docs/discovery.md) | Audio acquisition, Finnish ASR, LLM selection, scoring and timestamps |
| [Models and API providers](docs/models-and-providers.md) | Gemma profiles, GPU load/unload lifecycle, ASR alternatives, current API prices and selection |
| [Stream-data integration](docs/stream-data.md) | Existing vs deployed APIs, matching, alignment and proposed public contract |
| [Rendering and delivery](docs/rendering.md) | Partial downloads, camera/gameplay layout, captions and ready-folder rules |
| [Evaluation and implementation plan](docs/implementation-plan.md) | Ordered milestones, quality gates and concrete verification |
| [Research and reference audit](docs/research.md) | Source evidence, old-project lessons, model comparison and cost estimates |
| [Current project state](CONTEXT.md) | Short handoff for the next work session |

The next step, when authorized to run it, is the bounded real-media/model validation described in [implementation status](docs/implementation-status.md). The existing Run UI and worker cover the pipeline through output files. Prove this path before adding archive queues or a larger library UI.

The six reference projects remain separate. Their documents are evidence about those projects, not requirements inherited by this one. This design adds no runtime dependency on them.
