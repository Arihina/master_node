from dataclasses import dataclass


@dataclass
class AgentInfo:
    id: str
    name: str
    url: str
    description: str
    enabled: bool = True

# extend descriptions and add examples for query 

AGENTS: dict[str, AgentInfo] = {
    "epoz": AgentInfo(
        id="epoz",
        name="ЕПоЗ",
        url="https://127.0.0.1:8443",
        description="Закупки, ЕПоЗ"
    ),
    "cfd": AgentInfo(
        id="cfd",
        name="CFD",
        url="",
        description="Вычислительная газовая динамика"
    ),
    "lawyer": AgentInfo(
        id="lawyer",
        name="Юрист",
        url="",
        description="Правовые вопросы"
    ),
    "chat": AgentInfo(
        id="chat",
        name="Общий чат",
        url="",
        description="Общая болталка"
    ),
}
