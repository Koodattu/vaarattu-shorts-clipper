from __future__ import annotations

import json
import os
from pathlib import Path

from .storage import atomic_json, digest

CATALOG = {
    "gemma4-31b": {
        "repo": "unsloth/gemma-4-31B-it-qat-GGUF",
        "revision": "1f1e54258d4a2cf7522856a5789045d9f2ea6d16",
        "filename": "gemma-4-31B-it-qat-UD-Q4_K_XL.gguf",
    },
    "gemma4-26b-a4b": {
        "repo": "unsloth/gemma-4-26B-A4B-it-qat-GGUF",
        "revision": "9e8946010e8234901f15b8c10e74b51723c26832",
        "filename": "gemma-4-26B-A4B-it-qat-UD-Q4_K_XL.gguf",
        "sha256": "dcf179a91153e3a7ece792e48ef872180d9d6ef9b7677f0a0bd3e83cfe624d5e",
    },
    "turbo": {
        "repo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
        "revision": "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf",
    },
    "large-v3": {
        "repo": "Systran/faster-whisper-large-v3",
        "revision": "edaa852ec7e145841d8ffdb056a99866b5f0a478",
    },
}


def model_path(settings, key, verify=False) -> Path:
    folder = settings.models / key
    manifest_path = folder / "manifest.json"
    if not manifest_path.exists():
        raise ValueError(f"Model {key} is not prepared. Use the model setup command first.")
    manifest = json.loads(manifest_path.read_text("utf-8"))
    if manifest["key"] != key or manifest.get("source") != CATALOG[key] or not manifest.get("files"):
        raise ValueError("Model manifest does not match the selected model.")
    if manifest.get("revision") != CATALOG[key]["revision"]:
        raise ValueError("Model revision differs from the pinned catalogue.")
    names = {item["path"] for item in manifest["files"]}
    required = (
        {CATALOG[key]["filename"]}
        if "filename" in CATALOG[key]
        else {"model.bin", "config.json", "tokenizer.json"}
    )
    if not required <= names:
        raise ValueError("Model manifest is missing required weights or tokenizer files.")
    for item in manifest["files"]:
        path = (folder / item["path"]).resolve()
        if (
            not path.is_relative_to(folder.resolve())
            or not path.is_file()
            or path.stat().st_size != item["bytes"]
        ):
            raise ValueError(f"Model {key} is incomplete. Prepare it again.")
        if verify and digest(path) != item["sha256"]:
            raise ValueError(f"Model {key} failed its checksum check.")
    return folder / CATALOG[key]["filename"] if "filename" in CATALOG[key] else folder


def prepare(settings, key):
    """Explicit user command only. Never called by app startup or inference."""
    spec = CATALOG[key]
    settings.initialize()
    env = settings.environment()
    env.pop("HF_HUB_OFFLINE", None)
    os.environ.update(env)
    os.environ.pop("HF_HUB_OFFLINE", None)
    folder = settings.models / key
    folder.mkdir(parents=True, exist_ok=True)
    from huggingface_hub import HfApi, hf_hub_download, snapshot_download

    revision = HfApi().model_info(spec["repo"], revision=spec["revision"]).sha
    options = {
        "repo_id": spec["repo"],
        "revision": revision,
        "cache_dir": str(settings.cache / "huggingface/hub"),
        "local_dir": str(folder),
    }
    if "filename" in spec:
        target = Path(hf_hub_download(filename=spec["filename"], **options))
        if spec.get("sha256") and digest(target) != spec["sha256"]:
            raise ValueError("Downloaded model checksum differs from the pinned reference.")
    else:
        snapshot_download(allow_patterns=["*.json", "*.bin", "*.txt", "*.model"], **options)
    files = [
        {"path": str(p.relative_to(folder)), "bytes": p.stat().st_size, "sha256": digest(p)}
        for p in folder.rglob("*")
        if p.is_file() and ".cache" not in p.relative_to(folder).parts and p.name != "manifest.json"
    ]
    if not files:
        raise ValueError("No model files were prepared.")
    atomic_json(folder / "manifest.json", {"key": key, "source": spec, "revision": revision, "files": files})
    return folder
