from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity

from registry import AGENTS

model = SentenceTransformer(
    "intfloat/multilingual-e5-small"
)

THRESHOLD = 0.3

agent_vectors = {
    a.id: model.encode(f"passage: {a.description}", normalize_embeddings=True)
    for a in AGENTS.values() if a.enabled and a.url
}


def route(message: str) -> tuple[str | None, float]:
    q = model.encode(f"query: {message}", normalize_embeddings=True)
    best_agent, best_score = None, 0.0

    for agent_id, vec in agent_vectors.items():
        score = float(q @ vec)
        if score > best_score:
            best_score, best_agent = score, agent_id
            
    return (best_agent, best_score) if best_score >= THRESHOLD else (None, best_score)
