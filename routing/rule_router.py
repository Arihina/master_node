"""Слой правил на основе словаря маршрутизации (routing_vocab.json).

Быстрый детерминированный сигнал: взвешенная сумма совпавших терминов.
Если сумма >= порога из файла — запрос уверенно относится к целевому
агенту (ROUTING_VOCAB_AGENT_ID), и каскад возвращает
его сразу, не гоняя эмбеддинги и LLM.

Пустой ROUTING_VOCAB_FILE выключает слой; битый/отсутствующий файл —
предупреждение в лог один раз, слой молча отключается.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from config import settings

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent

_loaded: tuple[str, dict | None] | None = None


def _resolve_path(path: str) -> Path:
    """Относительный путь словаря — от корня проекта (как agents.yaml),
    абсолютный используется как есть."""
    p = Path(path)
    return p if p.is_absolute() else ROOT / p


def _word_pattern(term: str) -> re.Pattern:
    return re.compile(rf"\b{re.escape(term.lower())}\b")


def _sub_pattern(term: str) -> re.Pattern:
    return re.compile(re.escape(term.lower()))


def _load() -> dict | None:
    """Загрузить словарь один раз (кэш по пути)."""
    global _loaded
    if not settings.routing_vocab_file:
        return None
    path = _resolve_path(settings.routing_vocab_file)
    cache_key = str(path)
    if _loaded is not None and _loaded[0] == cache_key:
        return _loaded[1]
    try:
        with open(path, encoding="utf-8") as f:
            vocab = json.load(f)
    except (OSError, ValueError) as e:
        logger.warning("routing_vocab не загружен (%s): %s", path, e)
        _loaded = (cache_key, None)
        return None
    _loaded = (cache_key, vocab)
    return vocab


def clear_cache() -> None:
    """Сбросить кэш загрузки — нужно тестам, подменяющим путь/файл."""
    global _loaded
    _loaded = None


def score(message: str) -> tuple[int, list[tuple[str, str]]]:
    """Взвешенная сумма совпавших терминов + список (тип, термин)."""
    vocab = _load()
    if vocab is None:
        return 0, []

    rule = vocab.get("routing_rule", {})
    weights = rule.get("weights", {})

    text = (message or "").lower()
    total = 0
    hits: list[tuple[str, str]] = []

    for term in vocab.get("strong_abbreviations", []):
        if _word_pattern(term).search(text):
            total += weights.get("strong_abbreviation", 3)
            hits.append(("abbr", term))

    for term in vocab.get("rostec_markers", []):
        if _sub_pattern(term).search(text):
            total += weights.get("rostec_marker", 3)
            hits.append(("marker", term))

    for term in vocab.get("domain_phrases", []):
        if _sub_pattern(term).search(text):
            total += weights.get("domain_phrase", 2)
            hits.append(("domain", term))

    for term in vocab.get("generic_terms", []):
        if _sub_pattern(term).search(text):
            total += weights.get("generic_term", 1)
            hits.append(("generic", term))

    for term in vocab.get("exclude", []):
        if _sub_pattern(term).search(text):
            total += weights.get("exclude", -4)
            hits.append(("exclude", term))

    return total, hits


def route(message: str, candidates: set[str] | None = None) -> str | None:
    """Вернуть id агента, если словарь уверенно относит запрос к нему."""
    vocab = _load()
    if vocab is None:
        return None
    
    total, _ = score(message)
    threshold = vocab.get("routing_rule", {}).get("threshold", 2)
    if total < threshold:
        return None
    
    target = settings.routing_vocab_agent_id
    if candidates is not None and target not in candidates:
        return None
    return target
