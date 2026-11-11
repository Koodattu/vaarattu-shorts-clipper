"""Workspace model choices shared by the studio, CLI and background workers."""

import json
from typing import get_args

from .contracts import ResponsesModel
from .llm import PROVIDERS, check_provider, codex_settings
from .models import CATALOG, model_path
from .storage import atomic_json


def profiles():
    result = {}
    for provider, spec in PROVIDERS.items():
        if provider in {"openai", "codex"}:
            for model in get_args(ResponsesModel):
                key = provider if model == spec["model"] else f"{provider}:{model}"
                result[key] = {"provider": provider, f"{provider}_model": model}
        elif provider == "local":
            for model, asset in CATALOG.items():
                if "filename" not in asset:
                    continue
                result[f"local:{model}"] = {"provider": provider, "local_model": model}
        elif not spec.get("unavailable"):
            result[provider] = {"provider": provider}
    return result


def preference(settings):
    try:
        return json.loads((settings.work / "llm-preference.json").read_text("utf-8"))
    except FileNotFoundError:
        return {"choice": "default", "budget_usd": None}


def save_preference(settings, choice, budget):
    limit = None
    if choice != "default":
        profile = profiles().get(choice)
        if not profile:
            raise ValueError("Choose a supported workspace model.")
        provider = profile["provider"]
        check_provider(provider)
        if provider == "local":
            model_path(settings, profile["local_model"])
        if provider not in {"local", "codex"}:
            if not budget:
                raise ValueError("Set a spending limit per recording before choosing an API model.")
            limit = budget
    value = {"choice": choice, "budget_usd": limit}
    atomic_json(settings.work / "llm-preference.json", value)
    return value


def with_override(config, pref):
    if pref["choice"] == "default":
        return config
    selected = profiles()[pref["choice"]]
    budget = pref["budget_usd"] or 0
    if config.get("budget_usd"):
        budget = min(budget, config["budget_usd"])
    return {**config, "codex_model": None, "openai_model": None, **selected, "budget_usd": budget}


def effective_config(config, pref):
    resolved = with_override(config, pref)
    if resolved is config:
        return config
    if resolved["provider"] == "codex":
        resolved["codex"] = codex_settings(resolved["codex_model"])
    # A resumed local run only needs its pinned GGUF if it will still load that model.
    old_local = config.get("local_model")
    if old_local in config.get("model_manifests", {}) and (
        resolved["provider"] != "local" or resolved["local_model"] != old_local
    ):
        resolved["model_manifests"] = {k: v for k, v in config["model_manifests"].items() if k != old_local}
    return resolved


def evaluator_options(config):
    model = config.get("local_model") if config["provider"] == "local" else config.get("openai_model")
    return {"model": model, "codex_config": config.get("codex")}
