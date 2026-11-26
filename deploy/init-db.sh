#!/bin/bash
set -e

# Базы данных агентов + keycloak. Выполняется один раз при первом старте
# общего Postgres (порт 5433). Дальнейшие рестарты базы не пересоздают.

DATABASES=(
  epoz_db
  techdocs_rag_db
  chat_db
  ocr_llm_db
  agentic_rag
  rag_ingest
  keycloak
)

for db in "${DATABASES[@]}"; do
  echo "Creating database: $db"
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
    -c "CREATE DATABASE \"$db\";"
done
