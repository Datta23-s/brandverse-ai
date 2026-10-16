"""A minimal parser for the SSE wire format, for asserting on what clients actually receive."""

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SseMessage:
    event: str | None
    id: str | None
    data: Any
    retry: int | None


def parse_block(block: str) -> SseMessage | None:
    """One `\\n\\n`-terminated block; None for comment-only blocks (keep-alive pings)."""
    fields: dict[str, list[str]] = {}
    for line in block.splitlines():
        if not line or line.startswith(":"):
            continue
        name, _, value = line.partition(":")
        fields.setdefault(name, []).append(value.removeprefix(" "))
    if not fields:
        return None
    data = "\n".join(fields["data"]) if "data" in fields else None
    return SseMessage(
        event=fields.get("event", [None])[-1],
        id=fields.get("id", [None])[-1],
        data=json.loads(data) if data is not None else None,
        retry=int(fields["retry"][-1]) if "retry" in fields else None,
    )


def parse_stream(body: str) -> list[SseMessage]:
    messages = (parse_block(b) for b in body.replace("\r\n", "\n").split("\n\n"))
    return [m for m in messages if m is not None]
