"""Descarte de textos fora do interesse, por tag.

As regras ficam no .env (DESCARTE_TAGS / DESCARTE_PROTEGIDAS): uma tag que case
com DESCARTE_TAGS descarta o texto, a menos que outra tag case com
DESCARTE_PROTEGIDAS. Cada tag é normalizada (minúsculas, sem acento) antes.

Quem decide é `motivo()`; quem apaga é o handler `remover` do estágio
`remocao` da fila `delecao` (ver pipeline.py). Um texto removido vai para a
tabela `descartados`, que a descoberta consulta para não baixá-lo de novo."""

import json
import re
import unicodedata
from datetime import datetime, timezone
from functools import cache

from config import DESCARTE_PROTEGIDAS, DESCARTE_TAGS
from db import conectar



def normalizar(tag: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", tag).encode("ascii", "ignore").decode()
    return sem_acento.lower().strip()


@cache
def _regras() -> tuple[re.Pattern, re.Pattern]:
    if not DESCARTE_TAGS:
        raise RuntimeError("Defina DESCARTE_TAGS no .env (ver .env.example)")
    # (?!) nunca casa: sem regra de proteção, nada é protegido.
    return re.compile(DESCARTE_TAGS), re.compile(DESCARTE_PROTEGIDAS or "(?!)")


def classificar(temas: str | list[str] | None) -> tuple[str, str] | None:
    """('descarta', tag que descarta) | ('protegido', tag que salvou) | None
    se nenhuma tag chama o descarte. As tags voltam como vieram do site.
    `temas` pode ser a lista ou o JSON gravado em `textos.temas`."""
    if isinstance(temas, str):
        temas = json.loads(temas)
    if not temas:
        return None
    descarta, protege = _regras()
    normalizadas = [normalizar(t) for t in temas]
    gatilho = next((original for original, t in zip(temas, normalizadas) if descarta.search(t)), None)
    if gatilho is None:
        return None
    protecao = next((original for original, t in zip(temas, normalizadas) if protege.search(t)), None)
    return ("protegido", protecao) if protecao else ("descarta", gatilho)


def motivo(temas: str | list[str] | None) -> str | None:
    """A tag que descarta o texto, ou None se ele fica."""
    c = classificar(temas)
    return c[1] if c and c[0] == "descarta" else None


# ---------------------------------------------------------------- handler

def remover(job: dict) -> dict:
    """Estágio `remocao`: tira os textos do Qdrant (v1 e trechos da v2), do
    SQLite (texto, embedding, favorito, estado da busca v2) e os registra em
    `descartados`. O job traz um texto (desvio na extração: `texto_id`) ou um
    lote (base existente: `textos`, ver pipeline.enfileirar_delecao_lote) —
    em lote é uma chamada ao Qdrant e uma transação no SQLite pra centenas de
    textos, em vez de uma por texto.

    Idempotente — rodar de novo em textos já removidos só confirma a
    remoção. O Qdrant vem antes do SQLite: se ele falhar, os textos
    continuam inteiros e o retry recomeça do zero."""
    from busca_v2 import fonte as fonte_v2
    from busca_v2 import trechos as trechos_v2
    from embeddings import enviar_qdrant

    itens = job["payload"].get("contos") or [job["payload"]]
    ids = [item["conto_id"] for item in itens]
    marcadores = ",".join("?" * len(ids))
    with conectar() as conn:
        info = {linha["id"]: dict(linha) for linha in conn.execute(
            f"SELECT id, url, titulo, autor, temas FROM contos WHERE id IN ({marcadores})", ids)}

    enviar_qdrant.remover(ids)
    trechos_v2.remover_textos(ids)

    agora = datetime.now(timezone.utc).isoformat()
    por_id = [(texto_id,) for texto_id in ids]
    with conectar() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO descartados (id, url, titulo, autor, temas, motivo, descartado_em) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(item["conto_id"], info.get(item["conto_id"], {}).get("url") or item.get("url"),
              info.get(item["conto_id"], {}).get("titulo"), info.get(item["conto_id"], {}).get("autor"),
              info.get(item["conto_id"], {}).get("temas"), item.get("motivo"), agora) for item in itens],
        )
        # favoritos/embeddings/serie_contos referenciam textos (foreign_keys = ON): antes dele.
        conn.executemany("DELETE FROM favoritos WHERE conto_id = ?", por_id)
        conn.executemany("DELETE FROM embeddings WHERE conto_id = ?", por_id)
        conn.executemany("DELETE FROM serie_contos WHERE conto_id = ?", por_id)
        conn.executemany("DELETE FROM contos WHERE id = ?", por_id)

    with fonte_v2.conectar_estado() as estado:
        for tabela in ("amostra", "trechos_indexados", "resumos"):
            estado.executemany(f"DELETE FROM {tabela} WHERE conto_id = ?", por_id)

    # Sem a parte removida, a série do autor muda (ou deixa de existir).
    import pipeline

    pipeline.enfileirar_series([c.get("autor") for c in info.values()])
    return job["payload"]
