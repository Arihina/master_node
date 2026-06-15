from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    host: str = "127.0.0.1"
    port: int = 8000
    timeout_keep_alive: int = 300

    agent_timeout: float = 300.0
    agent_verify_tls: bool = False
    agent_max_connections: int = 200
    agent_max_keepalive: int = 40

    fallback_agent: str = "chat"


settings = Settings()
