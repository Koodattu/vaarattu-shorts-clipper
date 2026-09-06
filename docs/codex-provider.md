# Codex through the local API bridge

Implemented 2026-09-06 as an additional provider in **Vaarattu Shorts Clipper**. It uses the already-running [openai-api-server-via-codex](https://github.com/hotchpotch/openai-api-server-via-codex), which exposes Responses requests using its own Codex/ChatGPT login. It does not require an OpenAI Platform API key. Direct OpenAI and local Gemma remain separate choices.

## Setup

The project `.env` supports these settings; matching defaults were added without replacing existing values:

```dotenv
CODEX_BASE_URL=http://127.0.0.1:18080/v1
CODEX_MODEL=gpt-5.6-luna
CODEX_API_KEY=
```

Leave `CODEX_API_KEY` blank for the bridge's default unauthenticated local listener. If the bridge was started with `--api-key`, enter that **local bridge key** here. The adapter never borrows or forwards `OPENAI_API_KEY`, reads Codex's login file, or accesses credentials from another application. [The bridge documents optional incoming authentication separately from Codex login](https://github.com/hotchpotch/openai-api-server-via-codex#protecting-the-local-api).

Keep the bridge running, restart the clipper yourself, reload the page, and select **Codex · gpt-5.6-luna** under Find moments with before starting the new video. A model override in `CODEX_MODEL` changes the displayed model. The endpoint must be loopback HTTP(S) with a `/v1` path, without credentials, query parameters or fragments. Existing process environment variables override `.env`.

No bridge installation, login, startup, shutdown or inference request is performed by this setup. The UI's configured status indicates configuration, not a live connectivity or account-access test.

## Requests and saved runs

The adapter sends non-streaming `POST <CODEX_BASE_URL>/responses`, with original transcript passages, instructions and a native JSON schema. Both discovery and verification retain explicit `reasoning.effort=low`. There are no tools or agent sessions created by the clipper; this is a text selection request. `store=false` is sent and no conversation history is threaded between windows. The bridge handles its upstream streaming internally. [Compatibility documentation](https://github.com/hotchpotch/openai-api-server-via-codex#api-compatibility).

Endpoint and model are snapshotted in each new run's non-secret config. Resuming a run preserves them; changing `.env` affects new runs. Inference cache identity includes the Codex provider, model, endpoint, prompts, schema and reasoning effort. A rotated local bridge key does not invalidate completed inference. The existing semantic checks, one content-repair attempt, partial-coverage reporting and per-candidate rejection still apply.

HTTP access/quota/server failures stop for explicit retry, with no switch to a paid provider. Client timeout is 300 seconds and the polling deadline 330 seconds; requests remain sequential. Pausing stops the pipeline from accepting further work, but an already-sent request may still finish at the bridge and consume allowance.

The inspected [bridge backend](https://github.com/hotchpotch/openai-api-server-via-codex/blob/main/internal/app/backend.go) removes `max_output_tokens` before forwarding. Consequently the Codex adapter omits it and records `output_token_limit_enforced=false`. It also omits OpenAI's service-tier field. The clipper retains conservative input-size guards, but its usual 4,096-output-token planning allowance is not a hard Codex generation cap. Keep results concise through prompting; do not claim a dollar or output-token cap that the bridge cannot enforce.

## Token usage and costs

The normal durable request ledger records returned input, output, cached-input and reasoning counts, request count, model, requested effort and elapsed time. Cached/reasoning subtotals remain included in input/output totals. A response without usage remains marked unreported. Successful content caching avoids another request when reused.

Codex reports use `pricing_basis=codex_subscription`. Monetary estimates, reservations, USD-per-million rates and the dollar cap are `null`/unavailable in public reports, not a claim that subscription use costs zero. Internally, zero bookkeeping amounts keep this provider out of the paid-API spending ledger. The UI disables the API dollar-cap field for Codex and explains the distinction. Requests can consume the account's Codex allowance or credits, whose actual charges this app cannot calculate or cap. See [official Codex pricing and credit information](https://learn.chatgpt.com/docs/pricing).

## Verification and limits

Offline tests cover Responses shape, low reasoning, optional bearer auth, absence of Platform-key forwarding, missing usage, cache reuse, endpoint/model snapshots, HTTP failures without paid fallback, configuration validation and UI rendering of unavailable costs. Direct API tests remain in the suite. No new production dependency was added.

This integration was not tested against the running bridge with inference. The bridge's current published contract was inspected; the user's installed bridge version, account/model access and actual Finnish selection quality remain for the next manually initiated video run. No app or pipeline was started by the assistant.
