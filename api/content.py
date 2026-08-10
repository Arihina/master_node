from __future__ import annotations

from typing import Iterable


CHAT_TEXT_TYPES = frozenset({"text"})
CHAT_ATTACHMENT_TYPES = frozenset({"image_url", "input_audio", "file"})

RESPONSES_TEXT_TYPES = frozenset({"input_text"})
RESPONSES_ATTACHMENT_TYPES = frozenset(
    {"input_image", "input_file", "input_audio"})


def _parts(content) -> list[dict]:
    if not isinstance(content, list):
        return []
    return [p for p in content if isinstance(p, dict)]


def extract_text(content, text_types: Iterable[str]) -> str:
    if isinstance(content, str):
        return content

    types = frozenset(text_types)
    return "\n".join(
        str(p.get("text", "")) for p in _parts(content) if p.get("type") in types
    )


def has_attachment(content, attachment_types: Iterable[str]) -> bool:
    types = frozenset(attachment_types)
    return any(p.get("type") in types for p in _parts(content))
