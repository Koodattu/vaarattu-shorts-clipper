# Local models and API providers

Decision/research update: 2026-09-06. This is a design, not an installed model catalogue or a completed benchmark. The user's revised preference supersedes the initial Qwen3.5-9B and Gemini 3.5 Flash-Lite defaults. Local inference should use Gemma 4 through llama.cpp, with the largest useful model that fits entirely on the RTX 4090 at a tested context size. API selection should cover all five requested providers.

Latest implementation direction: **implement only faster-whisper Turbo for STT; investigate the other ASR options later.** Their comparison below is retained as research, not implemented support. Downloaded weights live under this repository's `.cache/models/`; library caches also stay under `.cache/`. The Gemma/Turbo catalogue and four API transports are implemented, with Meta disabled pending verified documentation. See [implementation status](implementation-status.md). No model was downloaded or run in this implementation pass.

## Local LLM shortlist

The reference projects already describe the relevant Gemma family. Reuse their asset-selection lessons; do not import their orchestration or assume that a catalogue entry proves runtime success.

| Priority | Model and proposed GGUF | Approximate target file size | Purpose |
|---|---|---:|---|
| First quality candidate | Gemma 4 31B IT QAT, `gemma-4-31B-it-qat-UD-Q4_K_XL.gguf` | 17.3 GB | Dense model; first full-GPU fit and Finnish editorial test |
| Required comparison | Gemma 4 26B-A4B IT QAT, `gemma-4-26B-A4B-it-qat-UD-Q4_K_XL.gguf` | 14.2 GB | MoE alternative with more memory headroom and potentially faster inference |
| Secondary reference-family challenger | Qwen3.6-35B-A3B, `Qwen3.6-35B-A3B-UD-IQ4_NL.gguf` | 18.5 GB | Compare only if Gemma misses important Finnish/context cases |

Sizes are decimal file sizes from reference catalogues, not total VRAM requirements. Total parameters, active parameters and quantization affect different aspects of quality and speed; a larger parameter count alone does not select the winner. Favor quality and full GPU residency with usable context. Do not force a larger model by silently offloading to CPU or applying an untested, much harsher quantization.

Reference provenance:

- `../highlight-clipper/src/highlight_clipper/assets/model-catalog.json`: Gemma 31B repository `unsloth/gemma-4-31B-it-qat-GGUF`, revision `1f1e54258d4a2cf7522856a5789045d9f2ea6d16`; Gemma 26B repository `unsloth/gemma-4-26B-A4B-it-qat-GGUF`, revision `c1f25db7cf31985b52caa1db777eb72d17ca1c7c`. Both list 32K context profiles. Its implementation-status document does not establish tested Gemma quality or fit.
- `../video-generator/src/video_generator/setup.py`: the same Gemma 26B target family, but repository revision `9e8946010e8234901f15b8c10e74b51723c26832`. Its `docs/model-matrix.md` and `docs/adr/0011-windows-first-ephemeral-model-servers.md` describe pinned assets, one inference slot and temporary owned model servers. Historical profile notes are not proof of the current private configuration.

Select one exact upstream revision and verify file hashes when implementing; do not combine one project's revision with another revision's checksum. The [31B GGUF repository](https://huggingface.co/unsloth/gemma-4-31B-it-qat-GGUF) and [26B GGUF repository](https://huggingface.co/unsloth/gemma-4-26B-A4B-it-qat-GGUF) are quantization distributions, distinct from the base model publisher.

Start the fit experiment at 16K context, one slot, no vision projector and no speculative drafter. Test 32K separately if useful. Weights, KV cache, compute buffers, runtime overhead and other applications all consume VRAM. Record loaded/offloaded layers, actual peak memory, context, cache precision, runtime build and cold/warm throughput. Pin a compatible Windows llama.cpp build; verify its full-offload and no-auto-fit controls (reference flags are `--n-gpu-layers all --fit off`) against that build's help and startup logs. See [llama.cpp server documentation](https://github.com/ggml-org/llama.cpp/tree/master/tools/server).

Both reference Gemma profiles have optional separate MTP drafters (roughly 280 MB for 31B and 252 MB for 26B). Leave MTP disabled for the first fit/quality baseline. Add it only as a separately measured profile with its paired revision, extra memory and acceptance/throughput results. Neither MTP nor large context should reduce the main model's GPU residency. If the selected profile cannot fit, wait for memory or report the failed fit; selecting a different profile is explicit, recorded and invalidates affected inference artifacts.

## GPU lifecycle is a pipeline requirement

For one selected VOD, the normal local sequence is:

```text
download full audio
  -> load selected ASR in an owned process
  -> finish/checkpoint all transcript chunks
  -> stop ASR process and confirm exit/resource release
  -> load selected Gemma GGUF in owned llama-server
  -> scan all windows, then verify the shortlist
  -> stop llama-server and confirm exit/resource release
  -> acquire selected video sections, render, check and export
```

Do not keep ASR imported and allocated in the long-lived worker. Deleting a Python model variable or emptying a framework cache alone is not the unload contract. Use process exit as the reliable ownership boundary, wait for child cleanup, and check available GPU memory before the next load. A noisy global VRAM reading need not return to an exact numeric baseline because the desktop and other programs are independent. A surviving owned model process blocks the next model stage and produces a recoverable resource error.

The workdir singleton and application-wide GPU mutex cover the entire resident model session. Keep a model loaded across that stage's adjacent requests, not across unrelated stages. Cancellation/crash recovery cleans only owned process trees; it never kills the user's other llama.cpp instance. No concurrent ASR/LLM, speculative next-VOD preloading or hidden CPU fallback.

Optional caption refinement is another ordered phase: unload the LLM, load ASR once for all shortlisted sections needing refinement, save results, unload ASR. If changed text needs LLM reverification, reload the LLM only after ASR exits. Rendering begins after these model phases. API mode still unloads local ASR when transcription finishes; a remote text API does not require a resident local LLM.

## Finnish ASR alternatives

| Candidate | What changes | Why test it / limitation |
|---|---|---|
| faster-whisper large-v3-turbo | Initial fast baseline | Existing experience is favorable; measure actual Finnish errors and complete iteration time |
| faster-whisper large-v3 | Larger Whisper model | Accuracy challenger; speed advantage cannot be assumed |
| Finnish-NLP Whisper large Finnish v3 | Finnish fine-tuned Whisper weights | Published Finnish results justify a comparison; dialect, code-switching and stream noise still need testing |
| NVIDIA Parakeet TDT 0.6B v3 through NeMo-Speech.cpp | Different ASR architecture and native C++ runtime | Finnish is among its 25 supported European languages; strong alternative experiment, not a demonstrated winner here |
| whisper.cpp with an appropriate Whisper model | Runtime alternative | Useful native Windows/CUDA comparison; the same weights do not become a different recognition model |

Sources: [faster-whisper](https://github.com/SYSTRAN/faster-whisper), [Finnish-NLP model card](https://huggingface.co/Finnish-NLP/whisper-large-finnish-v3), [Parakeet model card](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3), [NVIDIA NeMo-Speech.cpp](https://github.com/NVIDIA/NeMo-Speech.cpp), [runtime customization](https://github.com/NVIDIA/NeMo-Speech.cpp/blob/main/docs/asr/customization.md), [whisper.cpp](https://github.com/ggml-org/whisper.cpp).

The NVIDIA runtime now documents Windows/CUDA support, so test its native path before adding WSL/container infrastructure. Explicitly select Parakeet: the runtime's default model is a different family. Verify the actual word-timestamp output and flags; model-card timestamp support does not establish this adapter's timing fidelity. Runtime options differ by family: do not send Whisper's Finnish-language override, VAD or glossary controls to a model that does not support them. Parakeet runtime documentation does not list word boosting or VAD masking as supported. Diarization is optional and can require another model; do not add it merely to imitate a sample command.

All adapters must emit the same canonical source timeline, coverage, raw words and model provenance. Unsupported confidence fields remain null. They may use model-specific chunk/decode settings, but never silently remove gaps or fabricate word times. whisper.cpp's experimental word-timestamp mode needs separate timing validation. A smaller ASR model is acceptable if it wins accuracy/timing on these recordings; the preference for the largest local LLM is not evidence that ASR parameter count predicts Finnish accuracy.

## Requested API models: verified facts and limits

Prices below are USD per million tokens, standard synchronous uncached text input/output, checked 2026-09-06. Output includes billable reasoning where applicable. Cache, batch and special access tiers are excluded from the comparable columns. No API was called for inference and no Finnish winner has been established.

| Provider / model ID | Input / output | Structured-output and reasoning considerations |
|---|---:|---|
| Google `gemini-3.8-flash` | $0.75 / $3.75 through 2026-12-31; $1.50 / $7.50 from 2027-01-01 | Structured output supported; thinking low/medium/high, not minimal |
| OpenAI `gpt-5.6-luna` | $0.20 / $1.20 for short-context standard requests | Structured outputs; Responses API recommended; reasoning none through max supported. Requests above 272K input use higher rates |
| Z.ai `glm-5.3-flash` | Promotional $0.075 / $0.25; list $0.15 / $0.50 | JSON output documented; validate locally rather than assume strict JSON Schema equivalence. Thinking cannot be disabled for this model |
| DeepSeek `deepseek-v4-flash` | Off-peak $0.22 / $0.66; peak $0.44 / $1.32 | JSON-object mode, not a strict schema guarantee; supports non-thinking. Observed version DeepSeek-V4-Flash-0731 |
| Meta Muse Spark 1.3 | Not verified from accessible primary pricing documentation | Release/API availability announced; exact model ID, endpoint, credentials, response contract and rates remain to be confirmed from accessible API documentation |

Primary sources: [Gemini model](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash) and [pricing](https://ai.google.dev/gemini-api/docs/pricing); [Luna model](https://developers.openai.com/api/docs/models/gpt-5.6-luna) and [OpenAI pricing](https://developers.openai.com/api/docs/pricing); [GLM model](https://docs.z.ai/guides/vlm/glm-5.3-flash), [JSON output](https://docs.z.ai/guides/capabilities/struct-output) and [pricing](https://docs.z.ai/guides/overview/pricing); [DeepSeek pricing](https://api-docs.deepseek.com/quick_start/pricing/) and [JSON mode](https://api-docs.deepseek.com/guides/json_mode/); [Meta release announcement](https://research.meta.ai/blog/introducing-muse-spark-1-3) and [pricing portal](https://ai.developer.meta.com/docs/pricing-rate-limits).

GLM's 50% launch discount expires at 24:00 on September 9, 2026, UTC+8. DeepSeek peak periods are Monday–Friday 01:00–04:00 and 06:00–10:00 UTC; other times are off-peak. Older indexed DeepSeek prices differ from the fetched current table; retain the rate snapshot used for each estimate. OpenAI API billing is separate from Codex access. Meta's pricing portal returned a login requirement during research: do not adopt an aggregator's rate or guess an API slug as a verified contract.

For an illustrative VOD requiring 500,000 input tokens and 12,000 total billed output tokens across bounded requests:

| Rate profile | Illustrative cost |
|---|---:|
| GLM promotional / list | $0.0405 / $0.0810 |
| Luna standard short context | $0.1144 |
| DeepSeek off-peak / peak | $0.1179 / $0.2358 |
| Gemini current / January 2027 | $0.4200 / $0.8400 |

GLM has the lowest verified uncached rates in this requested set, including after its promotion. This does not establish the cheapest useful result: providers tokenize Finnish differently, reasoning volume varies, and repairs or poor candidates increase cost. The illustrative total is not a measured six-hour transcript. Meta is excluded from the ranking until its primary rates are verified. A 500K aggregate workload split across bounded requests does not itself trigger Luna's per-request long-context price tier.

## Provider selection and budget behavior

Plan five named provider choices plus local llama.cpp. Use a small typed adapter per provider, sharing HTTP transport where appropriate: native Gemini, native OpenAI Responses, and provider-specific OpenAI-style chat handling for Z.ai/DeepSeek. Meta's adapter is planned but cannot be called supported until its API contract and access are verified. Do not assume an OpenAI-compatible endpoint implements OpenAI schemas, reasoning flags, refusals or usage accounting identically.

Proposed project key names are `GEMINI_API_KEY`, `OPENAI_API_KEY`, `ZAI_API_KEY`, `DEEPSEEK_API_KEY` and `META_API_KEY`, supplied to the backend through environment or an ignored local secrets file. These are application conventions, not a claim that every upstream SDK discovers them automatically. Only the chosen provider needs a key. The UI shows configured, access-verified and benchmark-tested as separate states; Meta remains visibly unverified until its integration is finished. Missing optional keys never prevent local processing. Keys stay out of browser responses, artifacts, source control and logs.

Before Run, select ASR profile and local LLM or API provider/model, plus a provider-aware spend cap. Snapshot model ID/revision, endpoint identity, thinking settings, token limits, prompt/schema version and rate date into the run. Configure supported reasoning explicitly per model, never one universal disable-thinking flag. Send transcript text/timestamps only. All adapters face identical anchor validation, truncation/refusal/empty-response handling and one bounded repair. The model has no tools or authority over the pipeline.

Display estimated spend and maximum reservation before a paid run. Reserve the worst-case next request, reconcile actual usage including reasoning/repairs, and retain a conservative reservation for ambiguously billed failures. At the cap, checkpoint with an explicit incomplete outcome. Never switch providers or invoke a paid fallback automatically. Merely adding keys does not run a benchmark or send transcripts to all providers. No fixed dollar cap is selected by this design; a cap must reflect the chosen provider's current rates and user configuration.

## How to choose the Finnish winner

Use the fixed corpus and creator judgments in [the evaluation plan](implementation-plan.md). First compare Gemma 31B and 26B on identical timed Finnish windows, with full GPU residency and unload checks. Compare the four documented APIs and Meta once access is verified on the same input, when credentials and a budget are configured. Record reasoning settings and total tokens, not just visible JSON size.

Measure missed useful moments, preserved qualifications/negations, standalone boundaries, duplicate clips, valid anchors, correction effort and cost per accepted short. General multilingual or coding benchmarks cannot establish this task's winner. Promote a quality profile and a faster/cheaper option only after held-out VOD evaluation; until then all model choices above are candidates.
