import sqlite3
from contextlib import contextmanager

from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS contos (
    id            TEXT PRIMARY KEY,      -- slug da URL
    url           TEXT UNIQUE NOT NULL,
    titulo        TEXT,
    autor         TEXT,
    data_pub      TEXT,                  -- ISO 8601
    temas         TEXT,                  -- JSON: ["tema1", "tema2"]
    serie         TEXT,
    trecho        TEXT,                  -- início do texto, ~1.500 caracteres (usado no embedding e no preview)
    texto_completo TEXT,                 -- conto inteiro, só pra leitura offline própria — nunca exposto/redistribuído
    estrelas      INTEGER,
    leituras      INTEGER,
    coletado_em   TEXT,
    origem        TEXT DEFAULT 'real',   -- real | mock

    -- O estado do pipeline mora nas filas do CrevoMQ (FILA_DB_PATH, ver
    -- pipeline.py). Aqui só: 'concluido' (no catálogo do app, status 'ok') ou
    -- 'descoberta' (ainda atravessando as filas). tentativas/ultimo_erro são
    -- da fila antiga e não são mais atualizados.
    estagio       TEXT DEFAULT 'descoberta',
    status        TEXT DEFAULT 'pendente',
    tentativas    INTEGER DEFAULT 0,
    embedding_ok  INTEGER DEFAULT 0,
    ultimo_erro   TEXT
);

CREATE INDEX IF NOT EXISTS idx_contos_fila ON contos (estagio, status);
CREATE INDEX IF NOT EXISTS idx_contos_autor ON contos (autor, serie);  -- perfil do autor e séries no app
-- Índices "cobrindo" pro app: com o texto inline, a tabela tem GBs e varrê-la
-- a cada abertura da página levava minutos. Com estes, resumo/temas/autores e
-- as ordenações do catálogo leem só o índice.
CREATE INDEX IF NOT EXISTS idx_contos_catalogo ON contos (estagio, autor, data_pub, estrelas, temas);
CREATE INDEX IF NOT EXISTS idx_contos_data ON contos (estagio, data_pub);
CREATE INDEX IF NOT EXISTS idx_contos_estrelas ON contos (estagio, estrelas, data_pub);

CREATE TABLE IF NOT EXISTS embeddings (
    conto_id TEXT PRIMARY KEY REFERENCES contos(id),
    vetor    BLOB NOT NULL  -- float32, formato bruto (numpy .tobytes())
);

-- Contos removidos pela regra de descarte (descarte.py). Guarda o mínimo pra
-- auditar o que saiu e pra descoberta não baixá-los de novo.
CREATE TABLE IF NOT EXISTS descartados (
    id            TEXT PRIMARY KEY,
    url           TEXT,
    titulo        TEXT,
    autor         TEXT,
    temas         TEXT,  -- JSON, como estava no conto
    motivo        TEXT,  -- a tag que descartou
    descartado_em TEXT
);

-- Séries (series.py): as do site e as detectadas pelo título, por autor.
-- id = hash estável de (autor, origem, nome normalizado).
CREATE TABLE IF NOT EXISTS series (
    id             TEXT PRIMARY KEY,
    autor          TEXT NOT NULL,
    nome           TEXT NOT NULL,
    origem         TEXT NOT NULL,     -- site | titulo
    n_partes       INTEGER NOT NULL,
    primeiro_conto TEXT,              -- parte 1
    primeira_pub   TEXT,
    ultima_pub     TEXT,
    estrelas       INTEGER,           -- soma das partes
    temas          TEXT,              -- JSON: tags mais frequentes nas partes
    atualizado_em  TEXT
);
CREATE INDEX IF NOT EXISTS idx_series_autor ON series (autor);

CREATE TABLE IF NOT EXISTS serie_contos (
    conto_id TEXT PRIMARY KEY REFERENCES contos(id),
    serie_id TEXT NOT NULL,
    parte    INTEGER NOT NULL,        -- posição na ordem de leitura (1..n)
    numero   INTEGER                  -- número que o título trazia, se trazia
);
CREATE INDEX IF NOT EXISTS idx_serie_contos_serie ON serie_contos (serie_id, parte);

CREATE TABLE IF NOT EXISTS favoritos (
    conto_id   TEXT REFERENCES contos(id),
    tipo       TEXT CHECK (tipo IN ('gostei', 'nao_gostei')),
    marcado_em TEXT,
    PRIMARY KEY (conto_id)
);

CREATE VIRTUAL TABLE IF NOT EXISTS contos_fts USING fts5(
    titulo, trecho, temas, content='contos', content_rowid='rowid'
);

-- contos_fts é uma tabela "external content": não se popula sozinha, precisa
-- desses gatilhos pra ficar em sincronia com INSERT/UPDATE/DELETE em contos.
CREATE TRIGGER IF NOT EXISTS contos_fts_ai AFTER INSERT ON contos BEGIN
    INSERT INTO contos_fts (rowid, titulo, trecho, temas)
    VALUES (new.rowid, new.titulo, new.trecho, new.temas);
END;

CREATE TRIGGER IF NOT EXISTS contos_fts_ad AFTER DELETE ON contos BEGIN
    INSERT INTO contos_fts (contos_fts, rowid, titulo, trecho, temas)
    VALUES ('delete', old.rowid, old.titulo, old.trecho, old.temas);
END;

CREATE TRIGGER IF NOT EXISTS contos_fts_au AFTER UPDATE ON contos BEGIN
    INSERT INTO contos_fts (contos_fts, rowid, titulo, trecho, temas)
    VALUES ('delete', old.rowid, old.titulo, old.trecho, old.temas);
    INSERT INTO contos_fts (rowid, titulo, trecho, temas)
    VALUES (new.rowid, new.titulo, new.trecho, new.temas);
END;
"""


@contextmanager
def conectar(db_path: str = DB_PATH):
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# Migrações simples: colunas adicionadas depois que a tabela já existia em
# bancos antigos. `CREATE TABLE IF NOT EXISTS` não altera tabelas existentes,
# então colunas novas precisam ser adicionadas manualmente aqui.
MIGRACOES = [
    ("contos", "serie", "TEXT"),
    ("contos", "ultimo_erro", "TEXT"),
    ("contos", "texto_completo", "TEXT"),
]

# Tabelas da fila antiga, substituídas pelas do CrevoMQ (FILA_DB_PATH).
OBSOLETAS = ["worker_status", "worker_eventos"]


def _aplicar_migracoes(conn: sqlite3.Connection) -> None:
    for tabela, coluna, tipo in MIGRACOES:
        existe = any(
            linha[1] == coluna for linha in conn.execute(f"PRAGMA table_info({tabela})")
        )
        if not existe:
            conn.execute(f"ALTER TABLE {tabela} ADD COLUMN {coluna} {tipo}")


def _sincronizar_fts_se_vazio(conn: sqlite3.Connection) -> None:
    """Os gatilhos só cobrem escritas novas; linhas que já existiam em `contos`
    antes dos gatilhos existirem (bancos antigos) precisam de um rebuild
    único. `SELECT COUNT(*) FROM contos_fts` NÃO serve pra detectar isso —
    numa tabela external-content ela sempre espelha `contos`, esteja o índice
    construído ou não — por isso usamos um marcador próprio."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS _meta (chave TEXT PRIMARY KEY, valor TEXT)"
    )
    ja_rebuilt = conn.execute(
        "SELECT 1 FROM _meta WHERE chave = 'fts_rebuilt'"
    ).fetchone()
    (tem_textos,) = conn.execute("SELECT COUNT(*) FROM contos").fetchone()
    if not ja_rebuilt and tem_textos > 0:
        conn.execute("INSERT INTO contos_fts (contos_fts) VALUES ('rebuild')")
        conn.execute(
            "INSERT INTO _meta (chave, valor) VALUES ('fts_rebuilt', '1')"
        )


def iniciar_schema(db_path: str = DB_PATH) -> None:
    with conectar(db_path) as conn:
        conn.executescript(SCHEMA)
        _aplicar_migracoes(conn)
        for tabela in OBSOLETAS:
            conn.execute(f"DROP TABLE IF EXISTS {tabela}")
        _sincronizar_fts_se_vazio(conn)
