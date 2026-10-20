from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    host: str = "127.0.0.1"
    port: int = 8111
    timeout_keep_alive: int = 300

    agent_timeout: float = 300.0
    agent_verify_tls: bool = False
    agent_max_connections: int = 200
    agent_max_keepalive: int = 40

    fallback_agent: str = "chat"

    # Пороги эмбеддинг-слоя авто-роутинга. Зависят от EMBEDD_MODEL и
    # калибруются прогоном eval_routing.py на tests/test_data/golden_routing.yaml.
    # Рекомендованные значения для BAAI/bge-m3 (текущая модель) — 0.30/0.40/0.04:
    # ~63% запросов решаются сразу (точность direct ~97%), ~29% уходят в
    # LLM-слой как ambiguous, остальное — fallback. Прежние 0.69/0.04 были
    # подобраны под intfloat/multilingual-e5-small.
    routing_threshold: float = 0.40
    routing_ambiguity_delta: float = 0.04
    # Нижний порог: ниже него запрос считается вне тематик всех агентов —
    # LLM-слой не вызывается, сразу fallback-агент. Откалиброван под bge-m3.
    routing_low_threshold: float = 0.30

    routing_llm_shortlist: int = 3

    router_llm_timeout: float = 5.0

    router_llm_max_query_chars: int = 1000

    routing_cache_size: int = 256

    agents_file: str = "agents.yaml"

    ollama_model: str
    ollama_host: str = "http://localhost:11434"
    embedd_model: str


settings = Settings()
