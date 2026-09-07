from __future__ import annotations

import json
import hashlib
import os
import re
import secrets
import socket
import time
import queue
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx

from .models import model_path
from .processes import OwnedProcess, ToolError, waiting_lock
from .storage import atomic_json

PROVIDERS = {
    "local": {"model": "selected GGUF", "key": None},
    "gemini": {"model": "gemini-3.8-flash", "key": "GEMINI_API_KEY"},
    "openai": {"model": "gpt-5.6-luna", "key": "OPENAI_API_KEY"},
    "codex": {"model": "gpt-5.6-luna", "key": None},
    "zai": {"model": "glm-5.3-flash", "key": "ZAI_API_KEY"},
    "deepseek": {"model": "deepseek-v4-flash", "key": "DEEPSEEK_API_KEY"},
    "meta": {
        "model": "Muse Spark 1.3",
        "key": "META_API_KEY",
        "unavailable": "Meta's API contract is not yet verified; select another provider.",
    },
}
MAX_OUTPUT = 4096


class ModelOutputError(ValueError):
    """A completed model response cannot be used; other work may continue."""


class ModelAnchorError(ValueError):
    """A fixed, safe explanation of an invalid source reference."""


def codex_settings():
    base_url = os.environ.get("CODEX_BASE_URL", "http://127.0.0.1:18080/v1").strip().rstrip("/")
    model = os.environ.get("CODEX_MODEL", PROVIDERS["codex"]["model"]).strip()
    try:
        url = urlsplit(base_url)
        valid = (
            url.scheme in {"http", "https"}
            and url.hostname in {"127.0.0.1", "localhost", "::1"}
            and url.path == "/v1"
            and url.username is None
            and url.password is None
            and not url.query
            and not url.fragment
            and (url.port is None or 1 <= url.port <= 65535)
        )
    except ValueError:
        valid = False
    if not valid:
        raise ValueError(
            "Set CODEX_BASE_URL to the local bridge's base URL, such as http://127.0.0.1:18080/v1."
        )
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,119}", model):
        raise ValueError("Set CODEX_MODEL to a model name supported by your bridge.")
    return {"base_url": base_url, "model": model}


def rates(provider, now=None):
    now = now or datetime.now(timezone.utc)
    if provider == "gemini":
        return (0.75, 3.75) if now.year == 2026 else (1.50, 7.50)
    if provider == "openai":
        return 0.20, 1.20
    if provider == "zai":
        return (0.075, 0.25) if now < datetime(2026, 9, 9, 16, tzinfo=timezone.utc) else (0.15, 0.50)
    if provider == "deepseek":
        # Use peak rates for both reservations and conservative cost estimates.
        return 0.44, 1.32
    return 0, 0


def token_usage(provider, result):
    def count(value):
        return value if type(value) is int and value >= 0 else None

    usage = result.get("usageMetadata" if provider == "gemini" else "usage") or {}
    if provider == "gemini":
        input_tokens = count(usage.get("promptTokenCount"))
        output_tokens = count(usage.get("candidatesTokenCount"))
        reasoning = count(usage.get("thoughtsTokenCount"))
        total = count(usage.get("totalTokenCount"))
        if total is not None and input_tokens is not None:
            output_tokens = count(total - input_tokens)
        elif output_tokens is not None:
            output_tokens += reasoning or 0
        cached = count(usage.get("cachedContentTokenCount"))
    else:
        responses = provider in {"openai", "codex"}
        input_tokens = count(usage.get("input_tokens" if responses else "prompt_tokens"))
        output_tokens = count(usage.get("output_tokens" if responses else "completion_tokens"))
        cached = count(
            (usage.get("input_tokens_details" if responses else "prompt_tokens_details") or {}).get(
                "cached_tokens"
            )
        )
        if cached is None and provider == "deepseek":
            cached = count(usage.get("prompt_cache_hit_tokens"))
        reasoning = count(
            (usage.get("output_tokens_details" if responses else "completion_tokens_details") or {}).get(
                "reasoning_tokens"
            )
        )
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_input_tokens": cached,
        "reasoning_tokens": reasoning,
    }


def check_provider(provider):
    spec = PROVIDERS[provider]
    if spec.get("unavailable"):
        raise ValueError(spec["unavailable"])
    if spec["key"] and not os.environ.get(spec["key"]):
        raise ValueError(f"Configure {spec['key']} in the backend environment first.")
    if provider == "codex":
        codex_settings()


def full_offload(log: str) -> bool:
    reports = re.findall(r"offloaded\s+(\d+)/(\d+)\s+layers to GPU", log)
    return bool(reports) and all(int(a) == int(b) and int(b) > 0 for a, b in reports)


@contextmanager
def local_server(settings, config, folder, check):
    model = model_path(settings, config["local_model"], verify=True)
    with waiting_lock(settings.work / "gpu.lock", "Local\\VaarattuShortsGpu", check):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        token = secrets.token_urlsafe(32)
        url = f"http://127.0.0.1:{port}"
        args = [
            settings.llama_server,
            "--model",
            model,
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--ctx-size",
            str(config["context_size"]),
            "--parallel",
            "1",
            "--n-gpu-layers",
            "all",
            "--fit",
            "off",
            "--flash-attn",
            "on",
            "--cache-type-k",
            "q8_0",
            "--cache-type-v",
            "q8_0",
            "--no-mmproj",
            "--offline",
            "--verbosity",
            "4",
            "--api-key",
            token,
        ]
        with OwnedProcess(args, cwd=folder, env=settings.environment(), log=folder / "llama.log") as process:
            with httpx.Client(
                base_url=url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=httpx.Timeout(900, connect=5),
                trust_env=False,
            ) as client:
                deadline = time.monotonic() + 600
                while True:
                    check()
                    if process.proc.poll() is not None or time.monotonic() > deadline:
                        raise ToolError("The selected LLM did not become ready. Check its local runtime log.")
                    try:
                        if client.get("/health", timeout=2).status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.5)
                log = (folder / "llama.log").read_text("utf-8", errors="replace")
                if not full_offload(log):
                    raise ValueError("Full GPU residency was not confirmed. No CPU fallback was used.")
                props = client.get("/props").raise_for_status().json()
                actual = props.get("default_generation_settings", {}).get("n_ctx", props.get("n_ctx", 0))
                if int(actual) < config["context_size"]:
                    raise ValueError("The model server did not allocate the requested context.")
                atomic_json(
                    folder / "runtime.json",
                    {"context": actual, "model": model.name, "full_gpu": True, "props": props},
                )
                yield client


class Evaluator:
    def __init__(
        self,
        provider,
        store,
        run_id,
        folder,
        cap,
        check,
        client=None,
        context_size=16384,
        *,
        codex_config=None,
    ):
        self.provider, self.store, self.run_id = provider, store, run_id
        self.folder, self.cap, self.check, self.client = folder, cap, check, client
        self.context_size = context_size
        self.codex = (codex_config or codex_settings()) if provider == "codex" else None
        self.model = self.codex["model"] if self.codex else PROVIDERS[provider]["model"]
        # API requests use a conservative 64K envelope, independent of local GPU allocation.
        # API input_size is a UTF-8 byte bound; local input_size uses llama.cpp's tokenizer.
        self.discovery_budget = context_size - MAX_OUTPUT - 2048 if provider == "local" else 48000
        self.verification_budget = self.discovery_budget

    def post(self, client, url, headers, body):
        mailbox = queue.Queue(maxsize=1)

        def send():
            try:
                mailbox.put((True, client.post(url, headers=headers, json=body)))
            except Exception as exc:
                mailbox.put((False, exc))

        thread = threading.Thread(target=send, daemon=True)
        thread.start()
        deadline = time.monotonic() + (
            900 if self.provider == "local" else 330 if self.provider == "codex" else 180
        )
        while True:
            self.check()
            try:
                ok, result = mailbox.get(timeout=0.2)
                if not ok:
                    raise result
                return result
            except queue.Empty:
                if time.monotonic() > deadline:
                    raise ValueError("The model request exceeded its time limit. Completed work is saved.")

    def input_size(self, system, prompt):
        messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        if self.provider == "local":
            template = (
                self.client.post("/apply-template", json={"messages": messages}).raise_for_status().json()
            )
            result = self.client.post("/tokenize", json={"content": template["prompt"], "add_special": True})
            return len(result.raise_for_status().json()["tokens"])
        # UTF-8 bytes give a deliberately conservative request bound, not a measured token count.
        return len((system + prompt).encode("utf-8")) + 2048

    def request_system(self, system, schema):
        if self.provider in {"zai", "deepseek"}:
            return (
                system + "\nReturn JSON only matching this schema: " + json.dumps(schema, ensure_ascii=False)
            )
        return system

    def request_size(self, system, prompt, response_type):
        schema = response_type.model_json_schema()
        prepared = self.request_system(system, schema)
        # Include the native structured-output schema in the bound without duplicating it in the prompt.
        if self.provider not in {"zai", "deepseek"}:
            prepared += "\n" + json.dumps(schema, ensure_ascii=False)
        return self.input_size(prepared, prompt)

    def report(self, message):
        self.store.update(self.run_id, message=message)

    def _request(self, system, prompt, schema, reasoning_effort="none"):
        p = self.provider
        model = self.model
        messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        shape = {
            "type": "json_schema",
            "json_schema": {"name": "selection", "strict": True, "schema": schema},
        }
        if p == "local":
            return (
                "/v1/chat/completions",
                {},
                {
                    "model": "local",
                    "messages": messages,
                    "response_format": shape,
                    "max_tokens": MAX_OUTPUT,
                    "temperature": 1,
                    "top_p": 0.95,
                    "top_k": 64,
                    "stream": False,
                },
            )
        key = os.environ.get("CODEX_API_KEY", "") if p == "codex" else os.environ[PROVIDERS[p]["key"]]
        if p == "gemini":
            return (
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                {"x-goog-api-key": key},
                {
                    "systemInstruction": {"parts": [{"text": system}]},
                    "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "responseMimeType": "application/json",
                        "responseJsonSchema": schema,
                        "maxOutputTokens": MAX_OUTPUT,
                        "thinkingConfig": {"thinkingLevel": "low"},
                    },
                },
            )
        if p in {"openai", "codex"}:
            body = {
                "model": model,
                "instructions": system,
                "input": prompt,
                "store": False,
                "service_tier": "default",
                "max_output_tokens": MAX_OUTPUT,
                "reasoning": {"effort": reasoning_effort},
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "selection",
                        "strict": True,
                        "schema": schema,
                    }
                },
            }
            if p == "codex":
                # The bridge removes max_output_tokens upstream; it cannot enforce that cap.
                body.pop("max_output_tokens")
                body.pop("service_tier")
                body["stream"] = False
                return (
                    self.codex["base_url"] + "/responses",
                    {"Authorization": f"Bearer {key}"} if key else {},
                    body,
                )
            return "https://api.openai.com/v1/responses", {"Authorization": f"Bearer {key}"}, body
        endpoints = {
            "zai": "https://api.z.ai/api/paas/v4/chat/completions",
            "deepseek": "https://api.deepseek.com/chat/completions",
        }
        body = {
            "model": model,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "max_tokens": MAX_OUTPUT,
            "stream": False,
            "thinking": {"type": "enabled" if p == "zai" else "disabled"},
        }
        return endpoints[p], {"Authorization": f"Bearer {key}"}, body

    def _parse(self, result):
        if self.provider == "gemini":
            candidate = (result.get("candidates") or [{}])[0]
            if candidate.get("finishReason") != "STOP":
                raise ValueError("The API refused or truncated the selection response.")
            text = "".join(
                p.get("text", "")
                for p in candidate.get("content", {}).get("parts", [])
                if not p.get("thought")
            )
            return text
        if self.provider in {"openai", "codex"}:
            if result.get("status") != "completed":
                raise ValueError("The API refused or truncated the selection response.")
            text = "".join(
                part.get("text", "")
                for item in result.get("output", [])
                for part in item.get("content", [])
                if part.get("type") == "output_text"
            )
            return text
        choice = (result.get("choices") or [{}])[0]
        if choice.get("finish_reason") != "stop":
            raise ValueError("The model refused or truncated the selection response.")
        return choice.get("message", {}).get("content", "")

    def call(self, system, prompt, response_type, key, *, validate=None, reasoning_effort="none"):
        self.check()
        schema = response_type.model_json_schema()
        fingerprint = hashlib.sha256(
            json.dumps(
                [
                    self.provider,
                    self.model,
                    system,
                    prompt,
                    schema,
                    reasoning_effort,
                    MAX_OUTPUT,
                    self.context_size,
                    *([self.codex["base_url"]] if self.codex else []),
                ],
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        cache = self.folder / f"{key}-{fingerprint[:16]}.json"
        if cache.exists():
            parsed = response_type.model_validate_json(cache.read_text("utf-8"))
            if validate:
                try:
                    validate(parsed)
                except ValueError as exc:
                    raise ModelOutputError("The model's suggestion could not be verified.") from exc
            return parsed
        for attempt in range(2):
            self.check()
            size = self.request_size(system, prompt, response_type)
            limit = self.context_size - MAX_OUTPUT - 256 if self.provider == "local" else 60000
            if size > limit:
                raise ValueError(
                    "The request exceeds the safe context budget. No transcript text was truncated."
                )
            url, headers, body = self._request(
                self.request_system(system, schema), prompt, schema, reasoning_effort
            )
            input_rate, output_rate = rates(self.provider)
            reserve = 0
            if self.provider != "local":
                reserve = (size * input_rate + MAX_OUTPUT * output_rate) / 1e6
            model = self.model
            if self.provider == "local":
                model = self.store.get(self.run_id)["config"].get("local_model", "local")
            request_id = self.store.reserve(
                self.run_id,
                reserve,
                self.cap,
                {
                    "provider": self.provider,
                    "model": model,
                    "step": key,
                    "attempt": attempt + 1,
                    "input_usd_per_million": None if self.codex else input_rate,
                    "output_usd_per_million": None if self.codex else output_rate,
                    "pricing_checked": "2026-09-06",
                    "prompt_sha256": fingerprint,
                    "request_sha256": hashlib.sha256(
                        json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")
                    ).hexdigest(),
                    "reasoning_effort": reasoning_effort if self.provider in {"openai", "codex"} else None,
                    **(
                        {"pricing_basis": "codex_subscription", "output_token_limit_enforced": False}
                        if self.codex
                        else {}
                    ),
                },
            )
            response = None
            started = time.monotonic()
            try:
                if self.client:
                    response = self.post(self.client, url, headers, body)
                else:
                    with httpx.Client(
                        timeout=httpx.Timeout(300 if self.codex else 120, connect=15), trust_env=False
                    ) as client:
                        response = self.post(client, url, headers, body)
                if response.status_code >= 400:
                    # Keep reservation when billed status is uncertain. No hidden retry or provider switch.
                    raise ValueError(
                        f"The model service returned HTTP {response.status_code}. Retry after checking access or quota."
                    )
                try:
                    raw = response.json()
                except ValueError as exc:
                    raise ModelOutputError("The model returned an unreadable response.") from exc
                atomic_json(self.folder / f"{key}-{request_id}.response.json", raw)
                measured = token_usage(self.provider, raw)
                measured["elapsed_seconds"] = round(time.monotonic() - started, 3)
                input_tokens, output_tokens = measured["input_tokens"], measured["output_tokens"]
                actual = 0 if self.provider in {"local", "codex"} else None
                if input_tokens is not None and output_tokens is not None:
                    actual = (input_tokens * input_rate + output_tokens * output_rate) / 1e6
                # Record billed work even when its content is truncated, refused or invalid JSON.
                self.store.settle(request_id, actual, measured)
                atomic_json(
                    self.folder / f"{key}-{request_id}.usage.json",
                    {
                        "usage": raw.get("usageMetadata" if self.provider == "gemini" else "usage"),
                        **measured,
                        "rates": None if self.codex else [input_rate, output_rate],
                        "provider": self.provider,
                        "request_id": request_id,
                        "estimated_cost_usd": None if self.codex else actual,
                    },
                )
                try:
                    text = self._parse(raw)
                    if not text:
                        raise ValueError("The model returned an empty response.")
                    parsed = response_type.model_validate_json(text)
                    if validate:
                        validate(parsed)
                except ValueError as exc:
                    raise ModelOutputError(
                        str(exc)
                        if isinstance(exc, ModelAnchorError)
                        else "The model's response could not be validated."
                    ) from exc
                atomic_json(cache, parsed.model_dump())
                return parsed
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                raise ValueError(
                    "The model service could not complete the request. Completed work is saved."
                ) from exc
            except ModelOutputError as exc:
                if attempt or response is None or response.status_code >= 400:
                    raise
                prompt += (
                    f"\nValidation problem: {exc} Return valid JSON matching the schema. "
                    "Use only supplied anchors in their original order and keep the proposed idea inside the clip."
                )
        raise AssertionError("Unreachable")
