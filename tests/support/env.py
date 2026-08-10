"""Режим запуска тестов и общие константы.

Тесты работают в двух режимах, одним и тем же кодом:

* **STUB** (по умолчанию) — мастер поднимается в процессе, а вместо реальных
  агентов подставляется эталонная реализация контракта из `fake_agent.py`.
  Ничего внешнего не нужно: ни Postgres, ни Ollama, ни Qdrant, ни MinerU.
  Проверяется всё, за что отвечает САМ мастер: разбор запроса, выбор агента,
  подмена `model`, сквозной проброс статуса/тела/стрима, формат ошибок, auth.

* **LIVE** — задайте `MASTER_URL`, и те же тесты пойдут по HTTP в реально
  поднятый мастер с реальными агентами. Здесь дополнительно включаются
  проверки, которые в STUB бессмысленны (семантика роутинга, содержательность
  ответа модели) — они помечены `@live_only`.

    python -m unittest discover -s tests -t .                      # STUB
    MASTER_URL=http://127.0.0.1:8000 python -m unittest discover -s tests -t .   # LIVE

Один тест-класс на агента, общий контракт — в миксине `AgentContractTests`:
в curl-файлах один и тот же набор из восьми проверок был скопирован для
каждого агента, и копии успевали разойтись. Теперь расхождение невозможно.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

# Корень репозитория — чтобы `python -m unittest discover -s tests` работал
# независимо от текущей директории.
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


MASTER_URL: str | None = os.getenv("MASTER_URL") or None
LIVE: bool = MASTER_URL is not None

USER_ID = os.getenv("TEST_USER_ID", "11111111-1111-1111-1111-111111111111")
OTHER_USER_ID = "22222222-2222-2222-2222-222222222222"

AUTH = {"X-User-Id": USER_ID}
OTHER_AUTH = {"X-User-Id": OTHER_USER_ID}


def _master_agent_timeout(default: float = 300.0) -> float:
    """Сколько мастер сам готов ждать агента. Тест не должен сдаваться раньше
    мастера: иначе вместо осмысленного ответа приходит httpx.ReadTimeout, и
    непонятно, агент медленный или что-то сломано."""
    try:
        from config import settings
        return float(settings.agent_timeout)
    except Exception:
        return default


#: Таймаут HTTP-клиента в LIVE-режиме. По умолчанию — таймаут мастера плюс
#: запас на сеть; переопределяется переменной TEST_TIMEOUT (в секундах).
TIMEOUT = float(os.getenv("TEST_TIMEOUT") or (_master_agent_timeout() + 30))

#: Ограничение длины ответа, подставляемое в КАЖДЫЙ запрос генерации.
#: Проверкам формы содержание ответа не важно, а на живом стеке короткий
#: ответ сокращает прогон в разы. Пусто — ограничение не подставляется.
MAX_TOKENS = int(os.getenv("TEST_MAX_TOKENS") or 0) or None

# Несуществующий, но синтаксически корректный id — для проверок 404.
MISSING_COMPLETION_ID = "chatcmpl-00000000-0000-0000-0000-000000000000"
MISSING_RESPONSE_ID = "resp_00000000-0000-0000-0000-000000000000"
MISSING_UUID = "00000000-0000-0000-0000-000000000000"


live_only = unittest.skipUnless(
    LIVE, "нужен реально поднятый стек: задайте MASTER_URL")

stub_only = unittest.skipIf(
    LIVE, "проверка мастера в изоляции, в LIVE-режиме не имеет смысла")


try:  # официальный SDK — не обязателен для запуска, но с ним проверки строже
    import openai  # noqa: F401
    HAS_OPENAI_SDK = True
except ImportError:  # pragma: no cover
    HAS_OPENAI_SDK = False

needs_openai_sdk = unittest.skipUnless(
    HAS_OPENAI_SDK,
    "нужен пакет openai: pip install openai (валидация формы ответов по SDK)")
