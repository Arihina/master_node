"""Семантический слой роутинга на эмбеддингах.

Модель и индекс пассажей создаются **лениво**, при первом обращении к
`route()`.
"""

from __future__ import annotations

import logging
import threading
import time

import numpy as np
from sentence_transformers import SentenceTransformer

from registry import AGENTS
from config import settings

logger = logging.getLogger(__name__)

_model: SentenceTransformer | None = None
_model_lock = threading.Lock()


def get_embed_model() -> SentenceTransformer:
    """Ленивая потокобезопасная загрузка"""
    global _model

    if _model is not None:
        return _model

    with _model_lock:
        if _model is None:
            logger.info("Загрузка эмбеддера %s на %s",
                        settings.embedd_model, settings.embedd_device)
            _model = SentenceTransformer(
                settings.embedd_model,
                device=settings.embedd_device,
                local_files_only=True,
            )
    return _model


def is_embedder_loaded() -> bool:
    """Загружена ли модель прямо сейчас. Не триггерит загрузку — можно
    отдавать в health/status."""
    return _model is not None


class EmbeddingIndex:
    def __init__(self, encoder):
        self._encoder = encoder
        self._vectors: dict = {}

    def encode_passages(self, texts: list[str]):
        """Векторы пассажей (по строке на текст) для multi-vector индекса.
        Возвращает матрицу (n_пассажей, dim)."""
        return self._encoder.encode([f"passage: {t}" for t in texts],
                                    normalize_embeddings=True)

    @staticmethod
    def _max_score(q, vecs) -> float:
        """Близость запроса к агенту — максимум по векторам его пассажей."""
        arr = np.asarray(vecs)
        if arr.ndim == 1:
            return float(q @ arr)
        return float((q @ arr.T).max())

    def upsert(self, agent_id: str, vector) -> None:
        self._vectors[agent_id] = vector

    def remove(self, agent_id: str) -> None:
        self._vectors.pop(agent_id, None)

    def snapshot(self) -> dict:
        """Копия текущего набора векторов — база для copy-on-write подмены."""
        return dict(self._vectors)

    def replace(self, vectors: dict) -> None:
        """Атомарная подмена индекса целиком: замена ссылки, а не мутация
        по ключам. Старый словарь после этого не меняется, поэтому
        параллельный route() видит либо старый, либо новый набор векторов
        целиком — никогда промежуточное состояние. Вызывается из
        registry_service._apply; upsert/remove остались только для
        стартового наполнения индекса."""
        self._vectors = vectors

    def ids(self) -> set[str]:
        return set(self._vectors)

    def route(self, message: str, candidates: set[str] | None = None) -> dict:
        q = self._encoder.encode(
            f"query: {message}", normalize_embeddings=True)

        pool = (
            self._vectors.items() if candidates is None
            else ((aid, vecs) for aid, vecs in self._vectors.items()
                  if aid in candidates)
        )
        # multi-vector: близость запроса к агенту — максимум по его пассажам
        scores = [(agent_id, self._max_score(q, vecs))
                  for agent_id, vecs in pool]

        if not scores:
            return {
                "decision": "fallback",
                "scores": [],
            }

        scores.sort(key=lambda x: x[1], reverse=True)
        best_agent, best_score = scores[0]

        if len(scores) > 1:
            second_agent, second_score = scores[1]
        else:
            second_agent, second_score = None, 0.0

        gap = best_score - second_score

        if best_score < LOW_THRESHOLD:
            return {
                "decision": "no_match",
                "scores": scores,
            }

        if best_score < THRESHOLD:
            shortlist = [agent_id for agent_id, _ in
                         scores[:settings.routing_llm_shortlist]]
            return {
                "decision": "fallback",
                "candidates": shortlist,
                "scores": scores,
            }

        if gap <= AMBIGUITY_DELTA:
            return {
                "decision": "ambiguous",
                "candidates": [agent_id for agent_id, score in scores
                               if best_score - score <= AMBIGUITY_DELTA],
                "scores": scores,
                "gap": gap,
            }

        return {
            "decision": "direct",
            "agent": best_agent,
            "score": best_score,
            "scores": scores,
        }


THRESHOLD = settings.routing_threshold
AMBIGUITY_DELTA = settings.routing_ambiguity_delta
LOW_THRESHOLD = settings.routing_low_threshold


_index: EmbeddingIndex | None = None
_index_lock = threading.Lock()


def get_index() -> EmbeddingIndex:
    """Ленивый индекс: модель и векторы описаний агентов создаются при
    первом обращении. Пока индекс не нужен (health, /v1/models, работа
    только с явным model=<agent_id>), модель не загружается."""
    global _index

    if _index is not None:
        return _index

    with _index_lock:
        if _index is None:
            started = time.monotonic()
            index = EmbeddingIndex(get_embed_model())
            for agent in AGENTS.values():
                passages = agent.passage_texts
                if passages:
                    index.upsert(agent.id, index.encode_passages(passages))
            _index = index
            logger.info("Индекс эмбеддингов собран: %d агент(ов) за %.2f с",
                        len(index.ids()), time.monotonic() - started)
    return _index


def is_index_loaded() -> bool:
    return _index is not None


def index_ids() -> set[str]:
    """Состав индекса без его создания — для admin/status и health."""
    index = _index
    return index.ids() if index is not None else set()


def encode_passages(texts: list[str]):
    """Векторы пассажей через уже созданный/ленивый индекс. Используется
    registry_service при пересчёте описания изменённого агента."""
    return get_index().encode_passages(texts)


def apply_registry_update(
    new_state: dict,
    *,
    vectors: dict,
    removed: set[str],
    needs_rebuild: bool = False,
) -> None:
    """Атомарно подменить `AGENTS` и (если он уже создан) индекс.

    `registry_service._apply` вызывает эту функцию из event loop, а
    `get_index()` может параллельно собирать индекс в threadpool. Общий
    `_index_lock` гарантирует, что читатель увидит либо старые `AGENTS` +
    старый индекс, либо новые `AGENTS` + новый индекс.

    `needs_rebuild=True` — индекс успели создать в параллельном потоке уже
    после того, как registry_service посчитал векторы. Тогда индекс
    сбрасывается и будет собран лениво из новых `AGENTS`: кодировать
    описания прямо здесь, в event loop, нельзя.
    """
    global _index

    with _index_lock:
        if _index is not None:
            if needs_rebuild:
                logger.info("Индекс эмбеддингов пересоберётся: реестр "
                            "изменился во время ленивой инициализации")
                _index = None
            else:
                index = _index
                new_vectors = index.snapshot()
                for agent_id in removed:
                    new_vectors.pop(agent_id, None)
                for agent_id, vector in vectors.items():
                    new_vectors[agent_id] = vector
                index.replace(new_vectors)

        # AGENTS мутируется на месте: модули держат ссылку на словарь.
        AGENTS.clear()
        AGENTS.update(new_state)


def route(message: str, candidates: set[str] | None = None) -> dict:
    return get_index().route(message, candidates)
