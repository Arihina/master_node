# Deploy: единая инфраструктура платформы

Один `docker compose` поднимает общую инфраструктуру для master и всех агентов:

- **Postgres** — один инстанс, базы всех агентов + keycloak;
- **Qdrant** — один инстанс (epoz и tech_rag);
- **Keycloak** — IdP (realm `tidi`).

Всё слушает только `127.0.0.1`, наружу ходит только nginx.

## Запуск

```bash
cd deploy
cp .env.example .env
# задайте POSTGRES_PASSWORD, KC_ADMIN_PASSWORD, PUBLIC_IP
docker compose up -d
docker compose ps
```

При первом старте `init-db.sh` создаёт базы (дальше рестарты их не трогают).

## Порты

| Сервис | Хост-порт | Контейнер | Комментарий |
|---|---|---|---|
| Postgres | `127.0.0.1:5433` | 5432 | общий для агентов + keycloak |
| Qdrant | `127.0.0.1:6336` | 6333 | общий для epoz + tech_rag |
| Keycloak | `127.0.0.1:8081` | 8080 | путь `/auth`, наружу через nginx |

## Агент → база / Qdrant

| Агент | БД (в Postgres 5433) | Qdrant |
|---|---|---|
| `epoz` | `epoz_db` | 6336 |
| `chat` (slave_chat) | `chat_db` | — |
| `tech_rag` | `techdocs_rag_db` | 6336, коллекция `techdocs_hybrid` |
| `document_chat` | `ocr_llm_db` | — |
| `agentic_rag` | `agentic_rag` | — |
| `rag_ingestion` | `rag_ingest` | — |

### Что поправить в конфигах агентов при переезде на общий Postgres

Агенты сейчас держат свои отдельные порты. После консолидации в их `.env`
должно быть:

- всем: `DB_HOST=localhost`, `DB_PORT=5433` (вместо 5434/5435/5436/5437/5438);
- `rag_ingestion`: `database_url` → `postgresql+asyncpg://rag:rag@localhost:5433/rag_ingest` (либо собрать из `DB_*`);
- `agentic_rag`: `database_url` → `...@localhost:5433/agentic_rag`;
- `tech_rag`: `QDRANT_PORT=6336` (было 6335), коллекция остаётся `techdocs_hybrid`;
- `epoz`: `QDRANT_PORT=6336` (уже так).

## Keycloak

Realm и пользователи — [keycloak/README.md](keycloak/README.md). Кратко:

- админ-консоль: `http://127.0.0.1:8081/auth/admin/`;
- OIDC authority наружу: `http://<PUBLIC_IP>:8005/auth/realms/tidi`.

## Остановка и данные

```bash
docker compose down          # контейнеры останавливаются, данные сохраняются
docker compose down -v       # удалить и данные (БД, Qdrant, realm)
```

## Резервное копирование

```bash
docker compose exec postgres pg_dumpall -U postgres > dump.sql
```

Qdrant — снапшот коллекций через его API или копия volume `qdrant_data`.
