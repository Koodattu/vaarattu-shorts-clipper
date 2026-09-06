from __future__ import annotations

import re
from typing import Literal
from urllib.parse import parse_qs, urlparse

from pydantic import BaseModel, ConfigDict, Field, model_validator

CHANNEL_ID = "UCUCV40VqBZqt83afjbbICvw"
MIN_CLIP_US = 5000000
MAX_CLIP_US = 90000000  # Retain support for saved clips and manual edits.


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


def video_id(value: str) -> str:
    value = value.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
        return value
    url = urlparse(value)
    if url.scheme not in {"http", "https"} or url.username or url.password:
        raise ValueError("Enter a YouTube video URL or its 11-character ID.")
    if url.hostname == "youtu.be":
        found = url.path.strip("/")
    elif url.hostname in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        found = parse_qs(url.query).get("v", [""])[0] if url.path == "/watch" else ""
    else:
        found = ""
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", found):
        raise ValueError("Select one YouTube video, not a channel or playlist.")
    return found


class Rect(Contract):
    x: float = Field(ge=0, lt=1)
    y: float = Field(ge=0, lt=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def contained(self):
        if self.x + self.width > 1.000001 or self.y + self.height > 1.000001:
            raise ValueError("Crop must stay inside the source frame.")
        return self


class Layout(Contract):
    name: str = Field(min_length=1, max_length=80)
    camera: Rect
    gameplay: Rect
    calibrated: bool = False
    solo_host: bool = False


class RunRequest(Contract):
    video: str
    asr: Literal["turbo"] = "turbo"
    provider: Literal["local", "gemini", "openai", "codex", "zai", "deepseek", "meta"] = "local"
    local_model: Literal["gemma4-31b", "gemma4-26b-a4b"] = "gemma4-31b"
    context_size: Literal[16384, 32768] = 16384
    budget_usd: float = Field(default=0, ge=0, le=100)
    layout_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    stream_id: int | None = Field(default=None, gt=0)
    stream_offset_seconds: float | None = None
    alignment_confirmed: bool = False

    @model_validator(mode="after")
    def normalize(self):
        self.video = video_id(self.video)
        if self.provider not in {"local", "codex"} and self.budget_usd <= 0:
            raise ValueError("Set a spending limit for the selected API provider.")
        if self.provider == "codex":
            self.budget_usd = 0
        if self.alignment_confirmed and (self.stream_id is None or self.stream_offset_seconds is None):
            raise ValueError("A confirmed chat mapping needs a stream and measured offset.")
        return self


class Word(Contract):
    id: str
    start_us: int = Field(ge=0)
    end_us: int = Field(ge=0)
    text: str
    probability: float | None = None

    @model_validator(mode="after")
    def ordered(self):
        if self.end_us <= self.start_us:
            raise ValueError("Word interval must have positive duration.")
        return self


class Scores(Contract):
    standalone: int = Field(ge=0, le=4)
    opening: int = Field(ge=0, le=4)
    substance: int = Field(ge=0, le=4)
    payoff: int = Field(ge=0, le=4)
    fidelity: int = Field(ge=0, le=4)

    def total(self) -> int:
        return sum(self.model_dump().values())


class Candidate(Contract):
    outcome: Literal["accept", "reject", "needs_context"]
    start_word_id: str
    end_word_id: str
    idea_word_id: str
    category: Literal["opinion", "story", "observation", "joke", "explanation", "conversation"]
    summary_fi: str
    title_fi: str
    scores: Scores
    flags: list[str]
    reason: str


class Proposals(Contract):
    candidates: list[Candidate]


class EditRequest(Contract):
    expected_revision: int = Field(ge=1)
    start_us: int = Field(ge=0)
    end_us: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=200)
    words: list[Word] = Field(max_length=1000)
    layout_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    reviewed: bool = False
