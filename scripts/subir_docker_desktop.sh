#!/usr/bin/env bash
# Importa os backups de volume (se houver) no Docker Desktop e sobe a stack.
set -euo pipefail
cd "$(dirname "$0")/.."
docker info --format '{{.OperatingSystem}}' | grep -q "Docker Desktop" \
  || { echo "docker não aponta pro Docker Desktop"; exit 1; }
P=$(basename "$PWD")
mkdir -p data flags
for f in backup_volumes/*.tgz; do
  [ -e "$f" ] || continue
  v="${P}_$(basename "$f" .tgz | sed -E 's/^.*(qdrant_storage|hf_cache)$/\1/')"
  echo "importando $f -> $v"
  docker volume create "$v" >/dev/null
  docker run --rm -v "$v":/v -v "$PWD/backup_volumes":/b alpine sh -c "cd /v && tar xzf /b/$(basename "$f")"
done
docker compose up -d --build
docker compose ps
