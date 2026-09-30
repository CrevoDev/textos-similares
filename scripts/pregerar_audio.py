"""Enfileira na fila `audio` os textos já marcados com "Gostei" (os novos o
app enfileira sozinho, ao marcar). O worker é o do supervisor.toml:

    uv run python scripts/pregerar_audio.py
    uv run crevomq-supervisor supervisor.toml
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pipeline  # noqa: E402
from db import conectar  # noqa: E402

if __name__ == "__main__":
    pipeline.registrar()
    with conectar() as conn:
        ids = [l["conto_id"] for l in conn.execute(
            "SELECT f.conto_id FROM favoritos f JOIN contos c ON c.id = f.conto_id "
            "WHERE f.tipo = 'gostei' AND c.texto_completo IS NOT NULL ORDER BY f.marcado_em DESC")]
    n = sum(pipeline.enfileirar_audio(i) for i in ids)
    print(f"{len(ids)} textos com Gostei, {n} enfileirados (o resto já tem áudio ou já está na fila)")
