"""Roda uma vez antes de qualquer worker subir: cria o schema do contos.db
(idempotente), registra as filas do CrevoMQ e enfileira textos que estejam
fora do catálogo e sem job (bancos da época da fila antiga, ou jobs apagados
no painel). Centralizado aqui porque DDL concorrente de N processos já
corrompeu o banco uma vez. Item preso em 'processando' não precisa mais de
reset: o lease do CrevoMQ vence e ele volta sozinho."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pipeline  # noqa: E402
from db import iniciar_schema  # noqa: E402

if __name__ == "__main__":
    iniciar_schema()
    pipeline.registrar()
    n = pipeline.migrar_pendentes()
    print(f"schema ok, filas registradas, {n} textos enfileirados")
