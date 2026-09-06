from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path


def load_env(path: Path) -> None:
    if not path.is_file():
        return
    values = {}
    for number, line in enumerate(path.read_text("utf-8-sig").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)", line)
        if not match:
            raise ValueError(f"Invalid .env entry on line {number}. Use NAME=value.")
        key, value = match.groups()
        if value.startswith(("'", '"')):
            end = value.find(value[0], 1)
            if end < 0 or (value[end + 1 :].strip() and not value[end + 1 :].strip().startswith("#")):
                raise ValueError(f"Invalid .env quotes on line {number}. Keep each value on one line.")
            value = value[1:end]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
            if value.startswith("#"):
                value = ""
        values[key] = value
    # Parse completely before changing the environment. Values are literal, never shell code.
    for key, value in values.items():
        os.environ.setdefault(key, value)


@dataclass(frozen=True)
class Settings:
    root: Path
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    llama_server: str = "llama-server"
    port: int = 8765
    min_free_gb: float = 30
    max_download_gb: float = 3

    @property
    def work(self) -> Path:
        return self.root / "workdir"

    @property
    def cache(self) -> Path:
        return self.root / ".cache"

    @property
    def models(self) -> Path:
        return self.cache / "models"

    @property
    def ready(self) -> Path:
        return self.work / "ready"

    def environment(self) -> dict[str, str]:
        env = dict(os.environ)
        # Override inherited global cache settings in every owned model process.
        for key, folder in {
            "HF_HOME": "huggingface",
            "HF_HUB_CACHE": "huggingface/hub",
            "HUGGINGFACE_HUB_CACHE": "huggingface/hub",
            "TRANSFORMERS_CACHE": "huggingface/transformers",
            "TORCH_HOME": "torch",
            "XDG_CACHE_HOME": "runtime",
            "CUDA_CACHE_PATH": "cuda",
            "TORCH_EXTENSIONS_DIR": "torch/extensions",
            "UV_CACHE_DIR": "uv",
        }.items():
            env[key] = str(self.cache / folder)
        env["HF_HUB_DISABLE_TELEMETRY"] = "1"
        env["HF_HUB_OFFLINE"] = "1"
        return env

    def initialize(self) -> None:
        for path in (self.work, self.models, self.ready, self.work / "runs", self.work / "logs"):
            path.mkdir(parents=True, exist_ok=True)


def load_settings(root: Path | None = None) -> Settings:
    root = (root or Path(os.environ.get("VAARATTU_PROJECT", Path(__file__).resolve().parents[2]))).resolve()
    load_env(root / ".env")
    path = root / "config.toml"
    data = tomllib.loads(path.read_text("utf-8")) if path.exists() else {}
    allowed = {"ffmpeg", "ffprobe", "llama_server", "port", "min_free_gb", "max_download_gb"}
    if set(data) - allowed:
        raise ValueError("Unknown config.toml settings: " + ", ".join(sorted(set(data) - allowed)))
    for name in ("ffmpeg", "ffprobe", "llama_server"):
        value = data.get(name, "")
        if "/" in value or "\\" in value:
            data[name] = str((root / value).resolve())
    settings = Settings(root=root, **data)
    if not 1024 <= settings.port <= 65535 or settings.min_free_gb < 0 or settings.max_download_gb <= 0:
        raise ValueError("Invalid port or disk limit in config.toml")
    return settings
