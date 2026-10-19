import json

from ollama import Client

from registry import AGENTS

from config import settings

client = Client(host=settings.ollama_host,
                timeout=settings.router_llm_timeout)


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
    Ты роутер запросов. Выбери агента из списка ниже или ответь null.

    Доступные агенты:

    {agents_text}

    Верни только JSON без пояснений и markdown:

    {{"agent":"agent_id"}}

    Если ни один из агентов не подходит для запроса, верни {{"agent": null}}.

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

    # null (или любой другой мусор) не проходит валидацию — наверх уходит
    # None, и каскад уходит в fallback-агента.
    return agent if agent in valid_ids else None
