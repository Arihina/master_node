from sentence_transformers import SentenceTransformer

from registry import AGENTS

model = SentenceTransformer(
    "intfloat/multilingual-e5-small"
)

THRESHOLD = 0.69
AMBIGUITY_DELTA = 0.04

agent_vectors = {
    a.id: model.encode(f"passage: {a.description}", normalize_embeddings=True)
    for a in AGENTS.values()
}


def route(message: str) -> dict:
    q = model.encode(f"query: {message}", normalize_embeddings=True)

    scores = []
    for agent_id, vec in agent_vectors.items():
        score = float(q @ vec)
        scores.append((agent_id, score))

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
