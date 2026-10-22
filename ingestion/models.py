"""Typed provider-neutral conversation interchange shape."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Literal, Self, TypeVar

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    model_validator,
)

T = TypeVar("T")


def _finite_json[T](value: T) -> T:
    # Rejects NaN/Infinity before they reach PostgreSQL JSONB.
    try:
        json.dumps(value, allow_nan=False)
    except ValueError as exc:
        raise ValueError("must contain only finite JSON values") from exc
    return value


def _not_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be blank")
    return value


Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
"""Non-empty text that may be normalized by trimming surrounding whitespace."""

VerbatimText = Annotated[str, AfterValidator(_not_blank)]
"""Non-blank text kept byte-for-byte, because evidence quotes must match it exactly."""

JsonObject = Annotated[dict[str, JsonValue], AfterValidator(_finite_json)]

Role = Literal["user", "assistant"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Turn(StrictModel):
    role: Role
    content: Annotated[VerbatimText, Field(max_length=50000)]
    created_at: Annotated[datetime | None, Field(strict=False)] = None


class ConversationTurn(Turn):
    seq: Annotated[int | None, Field(ge=0)] = None


class Conversation(StrictModel):
    id: Text
    source_uri: Text
    title: str | None = None
    revision: str | None = None
    actor: str | None = None
    metadata: JsonObject = Field(default_factory=dict)
    messages: list[ConversationTurn]

    @model_validator(mode="after")
    def _unique_sequences(self) -> Self:
        sequences = [
            turn.seq if turn.seq is not None else i for i, turn in enumerate(self.messages)
        ]
        if len(set(sequences)) != len(sequences):
            raise ValueError("message seq values must be unique within a session")
        return self

    def numbered_messages(self) -> list[tuple[int, ConversationTurn]]:
        return [
            (turn.seq if turn.seq is not None else index, turn)
            for index, turn in enumerate(self.messages)
        ]
