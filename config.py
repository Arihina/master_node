from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Относительные пути в настройках резолвятся от корня проекта. Для секретов
# это важно: иначе `python main.py` из другой директории найдёт другой файл.
_CONFIG_DIR = Path(__file__).resolve().parent

_AGENT_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
# HTTP-заголовок не терпит пробелов/переводов строк и не-ASCII.
_TOKEN_VALUE_RE = re.compile(r"^[\x21-\x7e]+$")


def _normalize_token_map(raw) -> dict[str, str]:
    """Проверить и нормализовать маппинг agent_id -> internal token."""
    if raw is None or raw == "":
        return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(
                "AGENT_INTERNAL_TOKENS должен быть JSON-объектом "
                "{agent_id: token}") from e
    if not isinstance(raw, dict):
        raise ValueError(
            "AGENT_INTERNAL_TOKENS должен быть JSON-объектом "
            "{agent_id: token}")

    result: dict[str, str] = {}
    for key, value in raw.items():
        agent_id = str(key).strip()
        if not _AGENT_ID_RE.match(agent_id):
            raise ValueError(
                f"AGENT_INTERNAL_TOKENS: некорректный agent_id {key!r}")
        if not isinstance(value, str):
            raise ValueError(
                f"AGENT_INTERNAL_TOKENS[{agent_id}]: токен должен быть строкой")
        token = value.strip()
        if not token:
            raise ValueError(
                f"AGENT_INTERNAL_TOKENS[{agent_id}]: пустой токен")
        if not _TOKEN_VALUE_RE.match(token):
            raise ValueError(
                f"AGENT_INTERNAL_TOKENS[{agent_id}]: токен содержит "
                "пробелы или непечатаемые символы")
        result[agent_id] = token
    return result


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    env: str = "dev"

    log_level: str = "WARNING"

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

    # Слой правил: словарь маршрутизации (routing_vocab.json) — взвешенные
    # термины домена закупок. Относительный путь резолвится от корня проекта
    # (как agents_file). Пустой путь выключает слой.
    routing_vocab_file: str = ""
    routing_vocab_agent_id: str = "epoz"

    # аутентификация
    # keycloak — валидация JWT по JWKS (prod); dev_header — доверенный
    # X-User-Id для локальной разработки и STUB-тестов.
    auth_mode: str = "keycloak"
    keycloak_issuer: str = ""
    keycloak_jwks_url: str = ""
    keycloak_audience: str = "master-node"
    keycloak_client_id: str = "master-node"
    keycloak_algorithms: list[str] = ["RS256"]
    keycloak_leeway: int = 30
    keycloak_jwks_cache_ttl: int = 300
    keycloak_http_timeout: float = 5.0
    keycloak_verify_tls: bool = True
    # Роли из realm_access.roles / resource_access.<client>.roles.
    admin_role: str = "platform-admin"
    user_role: str = "platform-user"

    # внутренняя аутентификация master -> агент
    # Per-agent секреты, которые master шлёт в X-Internal-Token. НЕ хранятся
    # в agents.yaml: реестр правится на лету и отдаётся через /admin/agents.
    # Можно задать JSON-объектом в env или отдельным secret-файлом.
    agent_internal_tokens: dict[str, str] = Field(
        default_factory=dict, repr=False)
    agent_internal_tokens_file: str = ""

    @field_validator("env", mode="before")
    @classmethod
    def _normalize_env(cls, v):
        value = str(v).strip().lower()
        if value in {"prod", "production"}:
            return "prod"
        if value in {"dev", "development", "local", ""}:
            return "dev"
        raise ValueError("ENV должен быть 'dev' или 'prod'")

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, v):
        value = str(v).strip().upper()
        if value not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
            raise ValueError(
                "LOG_LEVEL должен быть CRITICAL/ERROR/WARNING/INFO/DEBUG")
        return value

    @field_validator("agent_internal_tokens", mode="before")
    @classmethod
    def _check_agent_tokens(cls, v):
        return _normalize_token_map(v)

    @field_validator("agent_internal_tokens_file", mode="before")
    @classmethod
    def _strip_tokens_file(cls, v):
        return str(v).strip() if v is not None else ""

    @model_validator(mode="after")
    def _load_agent_tokens_file(self) -> "Settings":
        """Догрузить токены из secret-файла. Значения файла важнее env:
        так ротация делается заменой файла, не трогая остальной конфиг."""
        if not self.agent_internal_tokens_file:
            return self

        path = Path(self.agent_internal_tokens_file).expanduser()
        if not path.is_absolute():
            path = _CONFIG_DIR / path
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as e:
            raise ValueError(
                f"AGENT_INTERNAL_TOKENS_FILE не читается ({path}): {e}") from e
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"AGENT_INTERNAL_TOKENS_FILE ({path}) — некорректный JSON: "
                f"{e}") from e
        if not isinstance(data, dict):
            raise ValueError(
                f"AGENT_INTERNAL_TOKENS_FILE ({path}) должен содержать "
                "JSON-объект {agent_id: token}")

        merged = dict(self.agent_internal_tokens)
        merged.update(_normalize_token_map(data))
        self.agent_internal_tokens = merged
        return self

    @field_validator("keycloak_algorithms", mode="before")
    @classmethod
    def _split_algorithms(cls, v):
        """Разрешить и JSON-список, и «RS256,ES256» через запятую."""
        if isinstance(v, str):
            return [a.strip() for a in v.split(",") if a.strip()]
        return v

    agents_file: str = "agents.yaml"

    ollama_model: str
    ollama_host: str = "http://localhost:11434"
    embedd_model: str
    # Устройство эмбеддера: cpu | cuda | cuda:0 | mps. По умолчанию CPU
    embedd_device: str = Field(
        default="cpu",
        validation_alias=AliasChoices("embedd_device", "embed_device"),
    )

    @field_validator("embedd_device", mode="before")
    @classmethod
    def _normalize_embed_device(cls, v):
        value = str(v).strip() if v is not None else ""
        return value or "cpu"


settings = Settings()
