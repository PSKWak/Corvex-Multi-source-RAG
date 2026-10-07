#!/usr/bin/env bash
# Bootstrap on Linux/macOS.
#   ./scripts/bootstrap.sh                 # offline path: local + hashing + mock
#   BGE=1 ELASTIC=1 ./scripts/bootstrap.sh # full path
set -euo pipefail

python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt
if [[ "${OPTIONAL:-}" || "${BGE:-}" || "${ELASTIC:-}" ]]; then
  ./.venv/bin/pip install -r requirements-optional.txt
fi

[[ -f .env || ! -f .env.example ]] || cp .env.example .env

if [[ "${ELASTIC:-}" ]]; then
  docker compose up -d
  echo "Waiting for Elasticsearch..."
  until curl -sf http://localhost:9200/_cluster/health >/dev/null; do sleep 3; done
  sed -i.bak 's/backend: local/backend: elastic/' config.yaml
fi
if [[ "${BGE:-}" ]]; then
  sed -i.bak 's/provider: hashing/provider: bge/' config.yaml
fi

./.venv/bin/python -m corvex_rag.cli ingest
./.venv/bin/python -m corvex_rag.cli ask "What is the default retention period for completed jobs?"
