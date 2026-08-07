import json

from ollama import Client

from registry import AGENTS

from config import settings

client = Client(host="http://localhost:11434")


def route(
    message: str,
    candidates: set[str] | list[str] | None = None
) -> str:

    if candidates is not None:
        available_agents = [AGENTS[aid] for aid in candidates if aid in AGENTS]
    else:
        available_agents = list(AGENTS.values())

    if not available_agents:
        return None

    agents_text = "\n".join(
        f"- {agent.id}: {agent.description}"
        for agent in available_agents
    )

    prompt = f"""
    Ты роутер запросов.

    Доступные агенты:

    {agents_text}

    Верни только JSON без пояснений и markdown:

    {{"agent":"agent_id"}}

    Запрос:

    {message}
    """

    response = client.chat(
        model=settings.ollama_model,
        messages=[{"role": "user", "content": prompt}],
        format="json",
        options={"temperature": 0},
    )
    agent = json.loads(response["message"]["content"]).get("agent")

    valid_ids = {a.id for a in available_agents}

    return agent if agent in valid_ids else None
