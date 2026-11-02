"""Pydantic request/response models and input normalisation."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_EXCESS_NEWLINES = re.compile(r"\n{4,}")


def _clean_text(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = _CONTROL_CHARS.sub("", value)
    value = _EXCESS_NEWLINES.sub("\n\n\n", value)
    return value.strip()


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=16_000)

    @field_validator("content")
    @classmethod
    def normalise_content(cls, value: str) -> str:
        cleaned = _clean_text(value)
        if not cleaned:
            raise ValueError("Message content cannot be empty")
        return cleaned


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=16_000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=40)
    mode: Literal["fast", "smart"] = "fast"

    @field_validator("message")
    @classmethod
    def normalise_message(cls, value: str) -> str:
        cleaned = _clean_text(value)
        if not cleaned:
            raise ValueError("Message cannot be empty")
        return cleaned
