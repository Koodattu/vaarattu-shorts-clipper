# Setup and operation

Implementation date: 2026-09-06. The application code and offline checks exist. **No application server, worker processing run, model inference, VOD download or real render has been started during implementation.** Model fit, media timing and Finnish editorial quality still need the first authorized live validation.

## Project-local files

Run commands from the repository root. Python 3.12 is recommended; the package supports 3.11–3.13. `uv.lock` pins the resolved dependencies. Model assets are always under this project's `.cache/models/`, independently of the process's working directory or inherited global Hugging Face cache settings.

```text
.cache/
  models/turbo/                   # pinned faster-whisper snapshot and manifest
  models/gemma4-31b/               # pinned GGUF and manifest
  models/gemma4-26b-a4b/            # optional local LLM choice
  huggingface/                    # download metadata/library cache
  torch/  cuda/  runtime/  uv/     # other explicitly localized caches
  tools/llama.cpp/                 # suggested external runtime location
workdir/
  state.sqlite3
  runs/<run_id>/                   # checkpoints, audio, ASR, inference, sections
  ready/<clip_id>/<revision>/      # completed exports
  ready/.staging/                  # pending/held render packages
  ready/runs/<run_id>.json         # run outcome and output index
  logs/  backups/
```

These directories and `config.toml` are ignored by Git. STT is **only local faster-whisper Turbo**. The alternative STT research remains in [models-and-providers.md](models-and-providers.md); it is not an installed adapter or a UI option.

## Prepare the environment when ready

### Current PC checklist

Read-only inspection on 2026-09-06 found FFmpeg, FFprobe, `uv` and Node on PATH. It did not find this project's `config.toml`, Turbo manifest, llama-server at the example path, or installed `faster_whisper` package. GPU DLL compatibility and YouTube extraction have not been tested.

For **local Gemma**, prepare Turbo, Gemma and llama.cpp; no LLM API key is needed. For **API selection**, prepare Turbo and configure only the selected provider's key; Gemma and llama.cpp are unnecessary. Both modes use the local GPU for Finnish transcription and require the same media tools and saved crop layout.

The implementation pass installed the base application/test dependencies in `.venv` and generated `uv.lock`. The optional ASR packages were not installed. The following commands are instructions for the later setup/run session; they were not executed to start the product here.

```powershell
uv --cache-dir .cache/uv sync --locked --extra asr
if (-not (Test-Path config.toml)) { Copy-Item config.example.toml config.toml }
```

Set FFmpeg, FFprobe and llama-server paths in `config.toml`. Relative tool paths resolve against the repository root. FFmpeg must provide libx264, AAC and libass subtitles. The installed CUDA/CTranslate2 combination must support the RTX 4090; see [faster-whisper requirements](https://github.com/SYSTRAN/faster-whisper). Do not silently switch to CPU if CUDA initialization fails.

Turbo's current GPU runtime needs **cuBLAS for CUDA 12 and cuDNN 9 for CUDA 12** accessible to the launcher. Installing the Python ASR extra does not by itself establish that these Windows DLLs are available. Follow the [upstream Windows GPU installation guidance](https://github.com/SYSTRAN/faster-whisper#gpu); a project-local DLL directory such as `.cache/tools/cuda/` can be added to the launching terminal's PATH. The llama.cpp CUDA archive is not a substitute for checking Turbo's cuDNN requirement.

For llama.cpp, the inspected reference runtime is **b9956**, with these upstream assets and reference checksums:

| Asset | SHA-256 |
|---|---|
| [Windows CUDA 12.4 runtime](https://github.com/ggml-org/llama.cpp/releases/download/b9956/llama-b9956-bin-win-cuda-12.4-x64.zip) | `ec4bb65d0be917bea46c6719653f73ee339ddab0e4cb6da25009d1b871397e94` |
| [Matching CUDA DLLs](https://github.com/ggml-org/llama.cpp/releases/download/b9956/cudart-llama-bin-win-cuda-12.4-x64.zip) | `8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6` |

Download/verify/extract the runtime into `.cache/tools/llama.cpp/` during setup and point `llama_server` at the actual executable. These binaries were not downloaded or launched in this project. A newer build needs its own compatibility check; the app requires explicit full GPU offload evidence and sufficient context from the running server. MTP is disabled.

yt-dlp is installed as a Python dependency and invoked with an isolated configuration and no playlist traversal. A supported JavaScript runtime may be needed for current YouTube extraction; consult the [yt-dlp EJS guide](https://github.com/yt-dlp/yt-dlp/wiki/EJS). The app does not borrow browser cookies or a sibling project's extractor configuration.

## Explicit model preparation

### Turbo performance settings

New runs now default to the same main acceleration approach as niilo22: `BatchedInferencePipeline` with batch size **16**, CUDA FP16, Finnish, VAD and beam size 5. Word timestamps remain enabled. This batches short speech segments inside each 20-minute checkpoint chunk; it does not load multiple VODs or keep the LLM resident. No PyTorch installation is required.

Optional settings in `config.toml`:

```toml
asr_batch_size = 16
asr_flash_attention = false
```

Batch size accepts 1–64; 0 explicitly selects the original unbatched implementation. Larger batches consume more VRAM and must be benchmarked; 16 matches niilo22, not a measured optimum for this machine. Flash Attention is opt-in because the installed Windows build, performance and timestamp quality still need validation. CTranslate2 documents it for Whisper self-attention; the code retains cross-attention word alignment. It never silently falls back if CUDA/FP16 or an explicitly requested optimization fails. See [batched faster-whisper](https://github.com/SYSTRAN/faster-whisper#batched-transcription) and [CTranslate2 Whisper options](https://opennmt.net/CTranslate2/python/ctranslate2.models.Whisper.html).

Restart the launcher after the current run finishes to load the new implementation. Each newly admitted run snapshots these settings. Existing runs keep their original unbatched profile on resume, even after config changes, so their completed chunks and transcript meaning are not silently replaced. New runs with different batching/attention settings have separate decode fingerprints. The run view displays the saved batch mode.

`workdir/runs/<run_id>/asr/runtime.json` records actual device/compute type, requested batch/attention settings and model load time. Newly generated chunk JSON files include audio seconds, elapsed transcription seconds and audio-seconds-per-second throughput; `asr.log` summarizes each chunk. Timings include preprocessing/VAD/alignment for that chunk, exclude initial PCM preparation/model loading, and include overlap audio. They are not whole-pipeline speed measurements.

CUDA FP16, VAD, fixed Finnish and loading the model once per ASR process were already enabled. Batching is the newly enabled optimization. Greedy beam-1 decoding, INT8, larger batches, concurrent GPU workers and Flash Attention are not all enabled by default: compare throughput, peak memory, Finnish accuracy and word alignment on the same audio before adopting them. Changing the decoding strategy can change output even with the same weights.

Startup and Run never automatically download models. Use the following commands when model download is wanted:

```powershell
uv --cache-dir .cache/uv run --locked --extra asr vaarattu-shorts models turbo
uv --cache-dir .cache/uv run --locked --extra asr vaarattu-shorts models gemma4-31b
# Optional alternative local LLM:
uv --cache-dir .cache/uv run --locked --extra asr vaarattu-shorts models gemma4-26b-a4b
```

The downloader uses pinned repository revisions, records file hashes and sizes, and verifies the predeclared Gemma 26B checksum. Turbo is pinned to revision `0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf`. The 31B model is approximately 17.3 GB on disk; this is not a measured VRAM fit. Model preparation does not load a model or run inference. At inference, only verified local paths are used and model-library network access is disabled.

Models are not automatically evicted. Media has a per-acquisition byte limit and a minimum-free-space guard. Automatic age/LRU cache eviction from the earlier design is deferred; the app reports disk exhaustion rather than deleting user work. Durable exports and backups can grow beyond the media download limit.

## Optional API keys

Put keys in `.env` at the project root. A blank file has been created for this checkout; [.env.example](../.env.example) is the tracked template for fresh checkouts. All CLI commands load the selected project's `.env` before checking providers or starting child processes, even when launched from another working directory with `--project`. Local mode needs no LLM API key. Restart the launcher/backend after editing `.env`.

```dotenv
GEMINI_API_KEY=
OPENAI_API_KEY=
ZAI_API_KEY=
DEEPSEEK_API_KEY=
HF_TOKEN=
```

Fill only the keys you use. `HF_TOKEN` is optional for Hugging Face downloads requiring authentication. The file supports one `NAME=value` entry per line, blank lines, `#` comments, optional single/double quotes, and an optional `export` prefix. Unquoted inline comments start with whitespace followed by `#`; quote values containing that sequence. Values are literal: no variable expansion, shell execution, multiline values or backslash unescaping. Keep runtime/tool settings in `config.toml`.

Existing process environment variables take precedence, including empty values. If an old terminal key masks a new `.env` value, remove that variable from the launching terminal (for example `Remove-Item Env:GEMINI_API_KEY`) before restarting. `.env` and `.env.*` variants are ignored by Git except `.env.example`. Keys are not returned by the UI/API or included in usage reports. Do not paste real keys into chat or the example file.

| UI selection | Environment variable |
|---|---|
| Local · Gemma 4 | None |
| Gemini 3.8 Flash | `GEMINI_API_KEY` |
| GPT-5.6 Luna | `OPENAI_API_KEY` |
| Codex · gpt-5.6-luna | No Platform key; optional `CODEX_API_KEY` for a protected local bridge |
| GLM-5.3-Flash | `ZAI_API_KEY` |
| DeepSeek V4 Flash | `DEEPSEEK_API_KEY` |

Create a key with access/quota for the selected model in that provider's API dashboard and save it in `.env`. Alternatively, this PowerShell example overrides the file for the current terminal without putting the key in command history or showing it on screen; change the variable name for another provider:

```powershell
$clipperApiKey = Read-Host 'Gemini API key' -AsSecureString
$env:GEMINI_API_KEY = [System.Net.NetworkCredential]::new('', $clipperApiKey).Password
Remove-Variable clipperApiKey
```

The PowerShell override lasts for that terminal session and its child processes; `.env` persists your saved configuration. Once launched, the provider selector indicates missing keys. Set a positive API spending limit in the form (for example `$1.00` as a first-run ceiling, not a predicted VOD price); local mode can keep it at zero. Codex uses a separate subscription allowance and disables this dollar cap.

To use the already-running local Codex bridge instead of the Platform API, select **Codex · gpt-5.6-luna**. Defaults are `CODEX_BASE_URL=http://127.0.0.1:18080/v1` and `CODEX_MODEL=gpt-5.6-luna` in `.env`; leave `CODEX_API_KEY` empty unless your bridge requires its own local key. Token usage is still recorded, while subscription/credit costs are shown as unavailable. See [Codex provider setup and limitations](codex-provider.md).

Gemini 3.8 Flash, GPT-5.6 Luna, GLM-5.3-Flash and DeepSeek V4 Flash have implemented provider-specific request/response paths. They have fixture validation, not live API certification. Meta Muse Spark 1.3 appears as unavailable because its exact API contract remains unverified; entering a Meta key does not enable a guessed endpoint. This is the one requested provider whose implementation is deferred pending accessible documentation.

Select a positive spend cap for API runs. Accounting uses conservative standard uncached rates, and peak rates for DeepSeek, so it can overestimate the provider's final invoice. Response usage includes reported reasoning where applicable. A timed-out or otherwise ambiguously billed request retains its reservation. There is one JSON repair attempt; transport/access/rate errors stop for explicit retry instead of silently sending repeated paid requests. Budget exhaustion preserves completed windows. A run's cap is immutable; choose a sufficient cap initially or start a new run with a revised cap. There is no automatic paid fallback from local mode.

### Token usage and costs

Each run now displays request count, reported input/output tokens, reported cached-input/reasoning subtotals, estimated API cost, pending/uncertain reservations and the chosen limit. **Download usage report** saves a JSON snapshot with every request's provider/model, window or verification step, attempt, timestamp, rate snapshot, reported tokens, estimate and reservation status. The durable ledger lives in `workdir/state.sqlite3` and survives restarts; no keys, prompts or transcripts are included in the downloaded report.

- Cached input is a subset of input; reasoning is a subset of output. Neither is added twice. Gemini output combines candidates and thinking (or uses reported total minus input). See [Gemini usage fields](https://ai.google.dev/api/generate-content#UsageMetadata), [OpenAI usage fields](https://developers.openai.com/api/reference/cli/resources/responses/methods/create), [Z.ai usage fields](https://docs.z.ai/api-reference/llm/chat-completion) and [DeepSeek cache fields](https://api-docs.deepseek.com/guides/kv_cache/).
- Truncated/refused/invalid selections still count when usage is reported. A repair is a separate request. Reusing a saved selection makes no request and adds no tokens or cost.
- Missing counts remain unknown, not measured zero. The UI flags incomplete requests, and report `reported_counts` shows how many requests supplied each subtotal. Timeouts and uncertain billing keep their budget reservations, even after retry/restart. Old requests without token records keep their saved cost and appear as unreported; history is not fabricated.
- Local llama.cpp generation counts are also tracked when reported, with zero API charge. Electricity and GPU time are not priced. Tokenization/readiness checks are not generation requests.
- Prices are **estimates**, not invoice reconciliation. Rates are snapshotted at request start; no retroactive recalculation is done. Cached-token discounts, DeepSeek off-peak discounts, taxes, credits and account-specific pricing are not applied. OpenAI requests explicitly select the standard service tier. Provider rate changes can invalidate an estimate, so the app's cap is a dispatch guard using those estimates, not a provider-enforced billing guarantee. Compare the first paid run with the provider dashboard.

## Run one video later

### Browse your YouTube channel

Set these entries in the project's ignored `.env` (they have been added without replacing existing entries):

```dotenv
YOUTUBE_API_KEY=
YOUTUBE_CHANNEL_ID=UCUCV40VqBZqt83afjbbICvw
```

Fill the key value with a Google API key from a project with **YouTube Data API v3 enabled**, then restart the launcher. The channel ID above is VaarattuVODs; use a `UC…` channel ID, not a handle or URL. This is separate from the LLM provider keys. An absent/blank channel setting defaults to VaarattuVODs for compatibility; an invalid ID fails configuration. See Google's [YouTube API setup guide](https://developers.google.com/youtube/v3/getting-started).

In **Channel videos**, press **Fetch latest videos**. This reads the channel's uploads playlist and saves up to 50 entries with titles, publication dates, duration and availability. Thumbnails load from YouTube's image host. Search saved titles/IDs, filter processed/unprocessed videos, and press **Select video** to fill the existing Run form. Selection does not download media or start processing. You can still paste a URL manually.

**Show more saved videos** expands the local list. **Load older videos from YouTube** fetches the next uploads page. Reloading the app and processing-status polling read SQLite only; they do not spend YouTube API quota. Refreshing latest keeps all previously saved videos and restarts the older-page cursor from that latest page, so repeated pages are safely deduplicated. It does not scan the whole channel automatically. If a cursor becomes invalid after uploads change, fetch latest again.

Processing badges use the entire durable run history, including runs originally started by pasting a URL. A run completed with ready clips, held clips, or no suitable candidates counts as **Processed**. Queued/running/paused/failed/cancelled states stay separate. A later failed rerun does not erase an earlier completed result; **Previous results** opens that run. Explicit reprocessing remains possible with **Select again**. There is still only one queued/running/paused video at a time.

Catalog rows and the channel cursor live in the existing `workdir/state.sqlite3` and are included in database backups. Refreshes cannot reset processing history. Failed API calls preserve cached rows/cursors. Live/upcoming, unfinished and known unavailable entries cannot be selected in the library; worker metadata validation remains authoritative because availability can change after fetching. Each new run snapshots its configured channel ID, so later `.env` edits cannot change a resumed run's source. Legacy runs retain the original VaarattuVODs channel.

The backend follows niilo22's uploads-playlist approach using the existing HTTP client, with no additional dependency or YouTube search requests. The key is sent in a server-side header and never returned to the browser, persisted in the catalog or included in errors. A normal nonempty latest-page fetch makes three requests (`channels.list`, `playlistItems.list`, `videos.list`); subsequent older pages make two. Each of these methods currently costs one YouTube quota unit. These quota units are separate from LLM token/cost accounting. See [channels.list](https://developers.google.com/youtube/v3/docs/channels/list), [playlistItems.list](https://developers.google.com/youtube/v3/docs/playlistItems/list) and [videos.list](https://developers.google.com/youtube/v3/docs/videos/list).

### Start the launcher

```powershell
uv --cache-dir .cache/uv run --locked --extra asr vaarattu-shorts serve
```

Open `http://127.0.0.1:8765`. `serve` starts the local FastAPI UI and one separate worker; neither is a scheduled service. Closing the browser preserves processing. Stopping the launcher stops its owned worker/model processes; completed checkpoints are recovered on the next launch. Separate `web` and `worker` commands are also available for separate terminals. Do not launch multiple workers for one workdir.

1. Save a layout: load a screenshot from the recording in the crop editor, draw camera and gameplay rectangles, and confirm the layout/source assumptions. This is a one-time preset per known source layout, not per-clip approval. No guessed camera coordinates are built into the app.
2. Select one video from the channel library or paste its URL, then choose a saved layout and local LLM or configured API. STT is fixed to Turbo. Start with 16K local context; 32K is available only if it actually fits.
3. Press Run. The worker validates metadata/channel identity, downloads full audio, checkpoints transcription, exits the ASR process, runs discovery/verification, exits the local LLM, downloads selected video ranges, verifies timing at two audio anchors, renders vertical shorts and captions, checks output and promotes eligible packages.
4. Open the output folder. Ready clips include `short.mp4`, Finnish SRT/word sidecars, title, metadata and a contact sheet. Technical logs/ASS/filter files are also retained. Held clips show a reason and may have previews. A run can finish with no suitable candidates or with all outputs needing attention.

Held clips offer **Retry render**, which queues just that clip's media work without repeating STT or LLM selection. Retry one at a time; use Edit when boundaries/captions/layout need correction. Ready cards also offer **Download vertical video**. New selections prefer 15–45-second general-audience moments with no clip-count quota. Saved selections retain their original decisions. See [clip fixes and validation](clip-fixes.md).

Before starting a new video, optionally enable **Optional chat peak review** with a confirmed vaarattu.tv stream ID and measured stream-to-VOD offset. This adds a separate LLM look at peak regions after the full transcript scan. It never filters the ordinary scan or automatically qualifies clips. The activity API is now live; timing cannot be inferred from the title or upload date. See [chat integration and offset example](stream-data.md).
5. Optional edits change bounds/title/caption words/layout and queue a new render revision. Boundaries cannot split known words. Caption IDs/times remain attached to the original transcript. Confirm the edited source/meaning before rerendering; technical caption/layout checks still apply. Old ready revisions are preserved; the run index/UI identifies current revisions, so use it when choosing which file to publish.

Only one queued/running/paused VOD is admitted. Pause/cancel requests are handled at worker checks, including while external tools or model requests are in progress; completed chunks/windows remain saved. Cancel releases the slot only after cleanup. Resume uses matching checkpoints. Channel browsing is manual; there is no automatic next video, overnight schedule or publisher.

Optional chat settings require a known stream ID and a measured offset, with beginning/end confirmation. Otherwise matching is informational and chat cannot affect ranking. Current aggregate outages or unverified timing do not prevent transcript-only processing. The stream service itself was not modified.

## Selection efficiency and an existing run

Selection uses compact original speech passages for discovery and detailed word anchors only around shortlisted clips. OpenAI Luna uses `low` reasoning for both discovery and verification. The local context selector controls llama.cpp allocation; API request budgets are independent. See [selection design and measured request reduction](selection-design.md).

After installing the passages-v2 changes, restart the launcher before resuming an existing paused selection run. Its completed audio/transcript are reused, but selection starts with the new format in a separate inference folder. Old responses and spending remain saved. The UI reports the planned speech-section count.

## Checks and recovery

Offline checks do not download media/models, invoke FFmpeg, perform inference or launch a network server. HTTP fixture responses and in-memory ASGI requests validate orchestration; they cannot certify Finnish quality or GPU compatibility.

```powershell
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/ruff.exe check src tests
.venv/Scripts/ruff.exe format --check src tests
node --check src/vaarattu_shorts/static/app.js
node --test tests/catalog_ui.test.cjs
```

In restricted Windows environments, pytest's shared user temp folder can be inaccessible. Use a fresh directory under `.cache/tests/` with `--basetemp`, after creating its parent. Tests allow only the standard library's internal asyncio wake-up socket pair; outbound network calls are blocked.

Use `vaarattu-shorts status` to inspect saved local state without processing. Use `vaarattu-shorts backup` with the launcher/worker stopped to create a SQLite-backup-API snapshot plus durable artifacts/checksum manifest under `workdir/backups/`. Backup excludes downloaded audio/sections, scratch WAVs and logs. Model files remain in `.cache/models/`. Restore into the same project workdir with the app stopped and verify the manifest; stored absolute paths need migration before a restore to a different project path. A full restore drill is not yet performed.

Before production use, authorize and perform the separate live validation checklist in [implementation-status.md](implementation-status.md): model fit, actual YouTube partial-transfer behavior, two-anchor timing, visual crop/caption QC and creator quality. These remain intentionally unrun at the user's request.
