from sentence_transformers import SentenceTransformer

from registry import AGENTS
from config import settings

model = SentenceTransformer(
    settings.embedd_model,
    local_files_only=True
)

THRESHOLD = 0.69
AMBIGUITY_DELTA = 0.04


class EmbeddingIndex:
    def __init__(self, encoder):
        self._encoder = encoder
        self._vectors: dict = {}

    def encode_passage(self, description: str):
        return self._encoder.encode(f"passage: {description}",
                                    normalize_embeddings=True)

    def upsert(self, agent_id: str, vector) -> None:
        self._vectors[agent_id] = vector

    def remove(self, agent_id: str) -> None:
        self._vectors.pop(agent_id, None)

    def ids(self) -> set[str]:
        return set(self._vectors)

    def route(self, message: str, candidates: set[str] | None = None) -> dict:
        q = self._encoder.encode(
            f"query: {message}", normalize_embeddings=True)

        pool = (
            self._vectors.items() if candidates is None
            else ((aid, vec) for aid, vec in self._vectors.items()
                  if aid in candidates)
        )
        scores = [(agent_id, float(q @ vec)) for agent_id, vec in pool]

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

        if best_score < THRESHOLD:
            return {
                "decision": "fallback",
                "scores": scores,
            }

        if gap <= AMBIGUITY_DELTA:
            return {
                "decision": "ambiguous",
                "candidates": [best_agent, second_agent],
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
    if _agent.description_key:
        index.upsert(_agent.id, index.encode_passage(_agent.description_key))


def route(message: str, candidates: set[str] | None = None) -> dict:
    return index.route(message, candidates)
