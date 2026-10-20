import numpy as np
from sentence_transformers import SentenceTransformer

from registry import AGENTS
from config import settings

model = SentenceTransformer(
    settings.embedd_model,
    local_files_only=True
)


THRESHOLD = settings.routing_threshold
AMBIGUITY_DELTA = settings.routing_ambiguity_delta
LOW_THRESHOLD = settings.routing_low_threshold


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


index = EmbeddingIndex(model)

for _agent in AGENTS.values():
    passages = _agent.passage_texts
    if passages:
        index.upsert(_agent.id, index.encode_passages(passages))


def route(message: str, candidates: set[str] | None = None) -> dict:
    return index.route(message, candidates)
