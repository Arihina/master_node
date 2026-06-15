import json

from ollama import Client

from registry import AGENTS

client = Client(host="http://localhost:11434")


def route(
    message: str,
    candidates: list[str] | None = None
) -> str:

    if candidates:
        available_agents = [AGENTS[agent_id] for agent_id in candidates]
    else:
        available_agents = AGENTS.values()

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
        model="gemma2:2b",
        messages=[{"role": "user", "content": prompt}],
        format="json",
        options={"temperature": 0},
    )
    agent = json.loads(response["message"]["content"]).get("agent")

    return agent if agent in AGENTS else None
