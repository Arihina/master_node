from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity

from registry import AGENTS

model = SentenceTransformer(
    "intfloat/multilingual-e5-small"
)

agent_vectors = {}

for agent in AGENTS.values():
    vec = model.encode(agent.description)
    agent_vectors[agent.id] = vec


def route(message: str) -> tuple[str | None, float]:

    query_vec = model.encode(message)

    best_agent = None
    best_score = 0.0

    for agent_id, vec in agent_vectors.items():

        score = cosine_similarity(
            [query_vec],
            [vec]
        )[0][0]

        if score > best_score:
            best_score = score
            best_agent = agent_id

    if best_score < 0.60:
        return None, best_score

    return best_agent, best_score
