"""De onde a busca v2 tira os textos, e a trava do modo `completo`.

- `amostra` (padrão): até `limite` textos já extraídos, sorteados de forma
  estável (a lista fica gravada em busca_v2.db, então rodadas seguintes e a
  avaliação usam sempre os mesmos textos; aumentar o limite só acrescenta).
- `completo`: todos os textos concluídos — recusa rodar enquanto a extração
  ainda tiver itens pendentes/em processamento (`ExtracaoIncompleta`).

O contos.db é aberto só para leitura (`mode=ro`): os workers do Docker estão
escrevendo nele ao mesmo tempo, e a v2 guarda o próprio estado em
BUSCA_V2_DB_PATH. `mode=ro` também faz um DB_PATH errado falhar na hora em
vez de criar um banco vazio."""

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from config import BUSCA_V2_DB_PATH, BUSCA_V2_LIMITE, BUSCA_V2_MODO, DB_PATH, TEMAS_EXCLUIDOS

MODOS = ("amostra", "completo")

SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS amostra (
    conto_id TEXT PRIMARY KEY,
    ordem    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS trechos_indexados (
    conto_id    TEXT NOT NULL,
    colecao     TEXT NOT NULL,
    n_trechos   INTEGER NOT NULL,
    motivo      TEXT,              -- NULL = indexado; 'excluido' | 'sem_texto'
    indexado_em TEXT,
    PRIMARY KEY (conto_id, colecao)
);

CREATE TABLE IF NOT EXISTS resumos (
    conto_id    TEXT NOT NULL,
    modelo      TEXT NOT NULL,
    status      TEXT NOT NULL,     -- ok | recusa | erro
    sinopse     TEXT,
    personagens TEXT,              -- JSON
    cenario     TEXT,
    tom         TEXT,
    temas       TEXT,              -- JSON: tags normalizadas sugeridas pelo LLM
    bruto       TEXT,              -- resposta crua (diagnóstico de recusa/erro)
    segundos    REAL,
    gerado_em   TEXT,
    PRIMARY KEY (conto_id, modelo)
);
"""

CAMPOS_TEXTO = "id, url, titulo, autor, temas, trecho, texto_completo, estrelas, data_pub"


class ExtracaoIncompleta(RuntimeError):
    pass


def agora() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def conectar_fonte(db_path: str = DB_PATH):
    if not Path(db_path).exists():
        raise FileNotFoundError(
            f"banco de textos não encontrado em {db_path!r} — no host, rode com DB_PATH=data/contos.db"
        )
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def conectar_estado(db_path: str = BUSCA_V2_DB_PATH):
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA_V2)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def pendencias_extracao() -> int:
    """Textos que ainda vão passar pela extração (descobertos e não baixados),
    pela fila `textos` do CrevoMQ. Erro permanente não conta — esses não vão
    sair do lugar sozinhos."""
    from pipeline import fila_textos

    contagem = fila_textos().contagens().get("extracao", {})
    return contagem.get("pendente", 0) + contagem.get("processando", 0)


def validar_modo(modo: str) -> str:
    if modo not in MODOS:
        raise ValueError(f"modo inválido {modo!r} (use {' ou '.join(MODOS)})")
    return modo


def excluido(temas_json: str | None) -> bool:
    temas = json.loads(temas_json) if temas_json else []
    return any(padrao in tema.lower() for tema in temas for padrao in TEMAS_EXCLUIDOS)


def _ordem_estavel(texto_id: str) -> str:
    return hashlib.sha1(texto_id.encode()).hexdigest()


def _ids_amostra(limite: int, db_path: str) -> list[str]:
    with conectar_estado() as estado:
        atuais = [l["conto_id"] for l in estado.execute("SELECT conto_id FROM amostra ORDER BY ordem")]
        if len(atuais) < limite:
            with conectar_fonte(db_path) as conn:
                candidatos = [
                    l["id"] for l in conn.execute(
                        "SELECT id FROM contos WHERE estagio = 'concluido' AND origem = 'real' "
                        "AND texto_completo IS NOT NULL"
                    )
                ]
            ja = set(atuais)
            novos = sorted((c for c in candidatos if c not in ja), key=_ordem_estavel)[: limite - len(atuais)]
            estado.executemany(
                "INSERT INTO amostra (conto_id, ordem) VALUES (?, ?)",
                [(c, len(atuais) + i) for i, c in enumerate(novos)],
            )
            atuais += novos
    return atuais[:limite]


def selecionar(modo: str = BUSCA_V2_MODO, limite: int = BUSCA_V2_LIMITE, db_path: str = DB_PATH) -> list[str]:
    validar_modo(modo)
    if modo == "amostra":
        return _ids_amostra(limite, db_path)
    faltam = pendencias_extracao()
    if faltam:
        faltam_fmt = f"{faltam:,}".replace(",", ".")
        raise ExtracaoIncompleta(
            f"modo 'completo' só roda com a extração terminada — ainda há {faltam_fmt} textos "
            "pendentes/em processamento na extração. Use o modo 'amostra' até lá."
        )
    with conectar_fonte(db_path) as conn:
        return [l["id"] for l in conn.execute("SELECT id FROM contos WHERE estagio = 'concluido' ORDER BY rowid")]


def carregar(ids: list[str], db_path: str = DB_PATH, campos: str = CAMPOS_TEXTO) -> list[dict]:
    """Linhas na mesma ordem de `ids` (os que não existirem somem). `campos`
    precisa incluir `id`; sem `texto_completo` a leitura fica bem mais leve."""
    if not ids:
        return []
    linhas: dict[str, dict] = {}
    with conectar_fonte(db_path) as conn:
        for i in range(0, len(ids), 500):
            parte = ids[i : i + 500]
            marcadores = ",".join("?" * len(parte))
            for l in conn.execute(f"SELECT {campos} FROM contos WHERE id IN ({marcadores})", parte):
                linhas[l["id"]] = dict(l)
    return [linhas[i] for i in ids if i in linhas]


def indexados(colecao: str) -> list[str]:
    """Textos que de fato viraram trechos nessa coleção (sem excluídos/sem
    texto) — o conjunto justo pra comparar v1 e v2."""
    with conectar_estado() as estado:
        return [l["conto_id"] for l in estado.execute(
            "SELECT conto_id FROM trechos_indexados WHERE colecao = ? AND motivo IS NULL ORDER BY conto_id",
            (colecao,),
        )]


def ja_indexados(colecao: str) -> set[str]:
    """Tudo que já passou pela coleção, inclusive excluídos/sem texto (pra não reprocessar)."""
    with conectar_estado() as estado:
        return {l["conto_id"] for l in estado.execute(
            "SELECT conto_id FROM trechos_indexados WHERE colecao = ?", (colecao,)
        )}


def registrar_indexados(colecao: str, linhas: list[tuple[str, int, str | None]]) -> None:
    """(texto_id, n_trechos, motivo) — motivo None = indexado."""
    with conectar_estado() as estado:
        estado.executemany(
            "INSERT OR REPLACE INTO trechos_indexados (conto_id, colecao, n_trechos, motivo, indexado_em) "
            "VALUES (?, ?, ?, ?, ?)",
            [(texto_id, colecao, n, motivo, agora()) for texto_id, n, motivo in linhas],
        )


def resumo_indexacao(colecao: str) -> dict:
    with conectar_estado() as estado:
        return dict(estado.execute(
            "SELECT COUNT(*) AS contos, SUM(n_trechos) AS trechos, SUM(motivo = 'excluido') AS excluidos, "
            "SUM(motivo = 'sem_texto') AS sem_texto FROM trechos_indexados WHERE colecao = ?", (colecao,)
        ).fetchone())
