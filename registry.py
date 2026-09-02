from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Literal

import yaml
from pydantic import (BaseModel, ConfigDict, Field, ValidationError,
                      field_validator, model_validator)

from config import settings

Transport = Literal["contract", "external", "ocr"]
ContractForm = Literal["chat_completions", "responses"]
Capability = Literal["chat", "ocr", "attachments", "ingest"]

FILE_VERSION = 1
ROOT = Path(__file__).resolve().parent
_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_MODEL_PREFIX_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


class RegistryFileError(Exception):
    """Файл реестра не читается, не парсится или нарушает инварианты."""


def normalize_description(text: str) -> str:
    """Описание участвует в эмбеддинге, поэтому сравнивать его нужно по смыслу,
    а не побайтово — иначе переформатирование YAML руками будет считаться
    изменением и триггерить пересчёт вектора."""
    return " ".join(text.split())


class AgentInfo(BaseModel):
    """Неизменяемый снимок агента. Запрос, уже взявший ссылку на объект,
    спокойно доработает на нём даже если агента в этот момент обновили —
    в словаре просто окажется другой объект."""
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    url: str = ""
    description: str = ""
    transport: Transport = "contract"
    config: dict = Field(default_factory=dict)
    enabled: bool = True
    capabilities: set[Capability] = Field(default_factory=lambda: {"chat"})
    routable: bool = True
    contract_forms: set[ContractForm] = Field(
        default_factory=lambda: {"chat_completions"})
    model_prefix: str | None = None

    @field_validator("id")
    @classmethod
    def _check_id(cls, v: str) -> str:
        if not _ID_RE.match(v):
            raise ValueError(
                "допустимы строчная латиница, цифры и подчёркивание, "
                "первый символ — буква, до 64 символов "
                "(id уходит в поле model OpenAI-контракта)")
        return v

    @field_validator("url")
    @classmethod
    def _strip_url(cls, v: str) -> str:
        return v.strip().rstrip("/")

    @field_validator("model_prefix")
    @classmethod
    def _check_model_prefix(cls, v: str | None) -> str | None:
        """Префикс уходит в неймспейс модели: `<prefix>/<...>` в поле `model`.
        Слэш внутри префикса запрещён — иначе partition по первому '/' даст
        не то, что задумано."""
        if v is None:
            return None
        if not _MODEL_PREFIX_RE.match(v):
            raise ValueError(
                "допустимы строчная латиница, цифры и подчёркивание, "
                "первый символ — буква, до 32 символов")
        return v

    @model_validator(mode="after")
    def _check_consistency(self) -> "AgentInfo":
        if not self.capabilities:
            raise ValueError("capabilities не может быть пустым")
        if self.transport in ("contract", "ocr") and not self.url:
            raise ValueError(f"url обязателен при transport={self.transport}")
        if "attachments" in self.capabilities and "chat" not in self.capabilities:
            raise ValueError("attachments требует chat в capabilities")
        if self.routable:
            if not normalize_description(self.description):
                raise ValueError("routable=true требует непустого description")
            if not self.contract_forms:
                raise ValueError(
                    "routable=true требует непустого contract_forms")
        return self

    @property
    def description_key(self) -> str:
        return normalize_description(self.description)

    @property
    def adapter_key(self) -> tuple:
        """Поля, при смене которых адаптер надо пересоздать. Правка description
        адаптер не трогает — иначе на каждом редактировании текста пересоздаётся
        httpx-клиент с живым пулом соединений."""
        return (self.url, self.transport, tuple(sorted(self.config.items(),
                                                       key=lambda kv: kv[0])))


def _short_errors(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(p) for p in e['loc']) or '<модель>'}: {e['msg']}"
        for e in exc.errors())


def build_agent(data: dict) -> AgentInfo:
    """Собрать агента из словаря, превратив pydantic-ошибку в читаемый текст."""
    try:
        return AgentInfo(**data)
    except ValidationError as e:
        raise RegistryFileError(_short_errors(e))


def parse_agents(items) -> dict[str, AgentInfo]:
    if not isinstance(items, list):
        raise RegistryFileError("'agents' должен быть списком")

    result: dict[str, AgentInfo] = {}
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise RegistryFileError(f"agents[{i}]: ожидается объект")
        ref = item.get("id") or f"#{i}"
        try:
            agent = build_agent(item)
        except RegistryFileError as e:
            raise RegistryFileError(f"агент {ref}: {e}")
        if agent.id in result:
            raise RegistryFileError(f"дубль id в файле реестра: {agent.id}")
        result[agent.id] = agent
    return result


def validate_state(agents: dict[str, AgentInfo]) -> None:
    """Инварианты уровня всего реестра, а не отдельного агента."""
    fallback = settings.fallback_agent
    agent = agents.get(fallback)
    if agent is None:
        raise RegistryFileError(
            f"fallback_agent '{fallback}' отсутствует в реестре")
    if not agent.enabled or not agent.routable:
        raise RegistryFileError(
            f"fallback_agent '{fallback}' должен быть enabled и routable")

    owners: dict[str, str] = {}
    for agent_id, a in agents.items():
        if a.model_prefix is None:
            continue
        existing = owners.get(a.model_prefix)
        if existing is not None:
            raise RegistryFileError(
                f"model_prefix '{a.model_prefix}' занят и агентом '{existing}', "
                f"и агентом '{agent_id}' — префикс должен быть уникален")
        owners[a.model_prefix] = agent_id


def resolve_path() -> Path:
    path = Path(settings.agents_file)
    return path if path.is_absolute() else ROOT / path


AGENTS_FILE = resolve_path()

_FIELD_ORDER = ("id", "name", "url", "transport", "enabled", "routable",
                "capabilities", "contract_forms", "model_prefix", "config",
                "description")


class _Block(str):
    """Строка, которую нужно сдампить блочным скаляром `|`."""


def _block_representer(dumper, data):
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(data), style="|")


yaml.add_representer(_Block, _block_representer)


def _to_item(agent: AgentInfo) -> dict:
    data = agent.model_dump()
    data["capabilities"] = sorted(data["capabilities"])
    data["contract_forms"] = sorted(data["contract_forms"])

    text = "\n".join(line.rstrip() for line in agent.description.splitlines())
    text = text.strip("\n")
    data["description"] = _Block(text + "\n") if text else ""

    return {key: data[key] for key in _FIELD_ORDER
            if not (key == "model_prefix" and data[key] is None)}


def read_file(path: Path | None = None) -> dict[str, AgentInfo]:
    path = path or AGENTS_FILE
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise RegistryFileError(f"файл реестра не найден: {path}")
    except yaml.YAMLError as e:
        raise RegistryFileError(f"некорректный YAML в {path}: {e}")

    if not isinstance(raw, dict):
        raise RegistryFileError(
            f"{path}: ожидается объект с ключами version и agents")
    if raw.get("version") != FILE_VERSION:
        raise RegistryFileError(
            f"{path}: version={raw.get('version')!r}, "
            f"поддерживается только {FILE_VERSION}")

    return parse_agents(raw.get("agents"))


def write_file(agents: dict[str, AgentInfo], path: Path | None = None) -> None:
    """Атомарная запись: временный файл в той же директории плюс os.replace.
    Иначе оборванная запись оставит битый реестр, который не переживёт рестарт."""
    path = path or AGENTS_FILE
    payload = {"version": FILE_VERSION,
               "agents": [_to_item(a) for a in agents.values()]}
    text = yaml.dump(payload, allow_unicode=True, sort_keys=False,
                     default_flow_style=False, width=100)

    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name,
                               suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


AGENTS: dict[str, AgentInfo] = {}
AGENTS.update(read_file())
validate_state(AGENTS)
