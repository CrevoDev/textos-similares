"""Fila `textos`, estágio `embedding`: texto (título + temas + trecho) ->
vetor. Roda sempre local via sentence-transformers, mesmo em modo mock (não
faz chamada de rede)."""

import json

import numpy as np
from crevomq import ErroPermanente

from config import DB_PATH, EMBEDDING_MODEL
from db import conectar

_modelo = None


def _obter_modelo():
    global _modelo
    if _modelo is None:
        from sentence_transformers import SentenceTransformer

        _modelo = SentenceTransformer(EMBEDDING_MODEL)
    return _modelo


def _texto_para_embedding(item: dict) -> str:
    temas = item.get("temas")
    temas_lista = json.loads(temas) if temas else []
    partes = [item.get("titulo") or "", " ".join(temas_lista), item.get("trecho") or ""]
    return "\n".join(p for p in partes if p)


def gerar(job: dict, db_path: str = DB_PATH) -> dict:
    """Handler do estágio `embedding`. Texto descartado na extração só
    atravessa o estágio (a fila `delecao` cuida dele)."""
    if job["payload"].get("descartado"):
        return job["payload"]
    with conectar(db_path) as conn:
        linha = conn.execute("SELECT titulo, temas, trecho FROM contos WHERE id = ?", (job["id"],)).fetchone()
    if linha is None:
        raise ErroPermanente("texto não está mais no banco")
    texto = _texto_para_embedding(dict(linha))
    if not texto.strip():
        raise ErroPermanente("texto sem título, temas nem trecho para gerar embedding")

    vetor = np.asarray(_obter_modelo().encode(texto, show_progress_bar=False), dtype=np.float32)
    with conectar(db_path) as conn:
        conn.execute("UPDATE contos SET embedding_ok = 1 WHERE id = ?", (job["id"],))
        conn.execute(
            "INSERT OR REPLACE INTO embeddings (conto_id, vetor) VALUES (?, ?)",
            (job["id"], vetor.tobytes()),
        )
    return job["payload"]


def embedding_texto_livre(texto: str) -> list[float]:
    """Gera embedding de uma query de busca livre, usando o mesmo modelo da indexação."""
    modelo = _obter_modelo()
    vetor = modelo.encode(texto, show_progress_bar=False)
    return np.asarray(vetor, dtype=np.float32).tolist()


def obter_vetor(item_id: str, db_path: str = DB_PATH) -> list[float] | None:
    with conectar(db_path) as conn:
        linha = conn.execute(
            "SELECT vetor FROM embeddings WHERE conto_id = ?", (item_id,)
        ).fetchone()
    if linha is None:
        return None
    return np.frombuffer(linha["vetor"], dtype=np.float32).tolist()
