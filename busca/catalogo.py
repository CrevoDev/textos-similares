"""Catálogo navegável dos textos já indexados — listagem paginada direto no
SQLite (LIMIT/OFFSET), com os mesmos filtros da busca vetorial (temas, autor,
período, estrelas) e palavra-chave opcional via FTS5. Não depende do Qdrant,
então funciona mesmo quando o worker de indexação está segurando o lock local.

Formato de `filtros` (compartilhado com `busca.filtros.montar_filtro`):
    {"temas": [str], "autor": str | None, "ano_min": int | None,
     "ano_max": int | None, "estrelas_min": int | None, "so_series": bool}
`serie_id` também é aceito aqui (perfil do autor, página da série), mas não
no Qdrant.

Séries (series.py) moram em `series`/`serie_contos`; os itens listados trazem
`serie_id`, `serie_nome`, `serie_parte` e `serie_total` (None fora de série).
"""

import re

from config import DB_PATH
from db import conectar

# Só textos que já atravessaram o pipeline inteiro: são os únicos que também
# existem no Qdrant (então "mais parecidos" funciona pra qualquer item listado).
BASE_WHERE = "c.estagio = 'concluido'"

ORDENS = {
    "recentes": "c.data_pub DESC",
    "estrelas": "c.estrelas DESC, c.data_pub DESC",
    "titulo": "c.titulo COLLATE NOCASE ASC",
    "antigos": "c.data_pub ASC",
    "partes": "sc.parte ASC, c.data_pub ASC",  # dentro de uma série: ordem de leitura
}

CAMPOS = ("c.id, c.url, c.titulo, c.autor, c.temas, c.trecho, c.estrelas, c.data_pub, "
          "sc.serie_id, s.nome AS serie_nome, sc.parte AS serie_parte, s.n_partes AS serie_total")
# Toda leitura de textos que devolve CAMPOS precisa deste JOIN.
JOIN_SERIE = "LEFT JOIN serie_contos sc ON sc.conto_id = c.id LEFT JOIN series s ON s.id = sc.serie_id"
SO_SERIES = "EXISTS (SELECT 1 FROM serie_contos x WHERE x.conto_id = c.id)"


def consulta_fts(texto: str) -> str | None:
    """Converte texto livre numa consulta FTS5 segura: cada palavra vira um
    termo entre aspas com prefixo (`"palavr"*`), unidos por AND implícito.
    Sem isso, hífen, aspas ou palavras como NOT/OR quebram a sintaxe do MATCH."""
    palavras = re.findall(r"\w+", texto or "", flags=re.UNICODE)
    if not palavras:
        return None
    return " ".join(f'"{p}"*' for p in palavras)


def where_filtros(filtros: dict | None, texto: str | None = None) -> tuple[str, list]:
    filtros = filtros or {}
    clausulas = [BASE_WHERE]
    params: list = []
    temas = filtros.get("temas") or ([filtros["tema"]] if filtros.get("tema") else [])
    if temas:
        marcadores = ",".join("?" * len(temas))
        clausulas.append(f"EXISTS (SELECT 1 FROM json_each(c.temas) WHERE json_each.value IN ({marcadores}))")
        params.extend(temas)
    if filtros.get("autor"):
        clausulas.append("c.autor = ?")
        params.append(filtros["autor"])
    if filtros.get("serie_id"):
        clausulas.append("c.id IN (SELECT conto_id FROM serie_contos WHERE serie_id = ?)")
        params.append(filtros["serie_id"])
    if filtros.get("so_series"):
        clausulas.append(SO_SERIES)
    if filtros.get("so_avulsos"):  # perfil do autor: textos fora de série
        clausulas.append(f"NOT {SO_SERIES}")
    if filtros.get("ano_min"):
        clausulas.append("CAST(substr(c.data_pub, 1, 4) AS INTEGER) >= ?")
        params.append(int(filtros["ano_min"]))
    if filtros.get("ano_max"):
        clausulas.append("CAST(substr(c.data_pub, 1, 4) AS INTEGER) <= ?")
        params.append(int(filtros["ano_max"]))
    if filtros.get("estrelas_min"):
        clausulas.append("c.estrelas >= ?")
        params.append(int(filtros["estrelas_min"]))
    fts = consulta_fts(texto) if texto else None
    if fts:
        clausulas.append("c.rowid IN (SELECT rowid FROM contos_fts WHERE contos_fts MATCH ?)")
        params.append(fts)
    return " AND ".join(clausulas), params


def contar(filtros: dict | None = None, texto: str | None = None, db_path: str = DB_PATH) -> int:
    where, params = where_filtros(filtros, texto)
    with conectar(db_path) as conn:
        (n,) = conn.execute(f"SELECT COUNT(*) FROM contos c WHERE {where}", params).fetchone()
    return n


def listar(
    filtros: dict | None = None,
    texto: str | None = None,
    ordem: str = "recentes",
    limite: int = 24,
    offset: int = 0,
    db_path: str = DB_PATH,
) -> list[dict]:
    where, params = where_filtros(filtros, texto)
    ordem_sql = ORDENS.get(ordem, ORDENS["recentes"])
    with conectar(db_path) as conn:
        linhas = conn.execute(
            f"SELECT {CAMPOS} FROM contos c {JOIN_SERIE} WHERE {where} ORDER BY {ordem_sql} LIMIT ? OFFSET ?",
            [*params, limite, offset],
        ).fetchall()
    return [dict(linha) for linha in linhas]


def buscar_fts(texto: str, filtros: dict | None = None, limite: int = 50, db_path: str = DB_PATH) -> list[str]:
    """Slugs ranqueados por bm25 (menor = melhor), respeitando os filtros —
    usado pela busca híbrida."""
    fts = consulta_fts(texto)
    if not fts:
        return []
    where, params = where_filtros(filtros)
    with conectar(db_path) as conn:
        linhas = conn.execute(
            f"SELECT c.id FROM contos_fts JOIN contos c ON c.rowid = contos_fts.rowid "
            f"WHERE contos_fts MATCH ? AND {where} ORDER BY bm25(contos_fts) LIMIT ?",
            [fts, *params, limite],
        ).fetchall()
    return [linha["id"] for linha in linhas]


def temas_populares(limite: int = 120, db_path: str = DB_PATH) -> list[tuple[str, int]]:
    with conectar(db_path) as conn:
        linhas = conn.execute(
            f"SELECT j.value AS tema, COUNT(*) AS n FROM contos c, json_each(c.temas) j "
            f"WHERE {BASE_WHERE} GROUP BY j.value ORDER BY n DESC LIMIT ?",
            (limite,),
        ).fetchall()
    return [(linha["tema"], linha["n"]) for linha in linhas]


def autores(db_path: str = DB_PATH) -> list[str]:
    with conectar(db_path) as conn:
        linhas = conn.execute(
            f"SELECT DISTINCT c.autor FROM contos c WHERE {BASE_WHERE} AND c.autor IS NOT NULL "
            f"ORDER BY c.autor COLLATE NOCASE"
        ).fetchall()
    return [linha["autor"] for linha in linhas]


def resumo(db_path: str = DB_PATH) -> dict:
    """Números do cabeçalho: total indexado, autores, faixa de anos, máx. de estrelas."""
    with conectar(db_path) as conn:
        linha = conn.execute(
            f"SELECT COUNT(*) AS total, COUNT(DISTINCT c.autor) AS autores, "
            f"MIN(substr(c.data_pub, 1, 4)) AS ano_min, MAX(substr(c.data_pub, 1, 4)) AS ano_max, "
            f"MAX(c.estrelas) AS estrelas_max FROM contos c WHERE {BASE_WHERE}"
        ).fetchone()
    return dict(linha)


def obter(texto_id: str, db_path: str = DB_PATH) -> dict | None:
    with conectar(db_path) as conn:
        linha = conn.execute(f"SELECT {CAMPOS} FROM contos c {JOIN_SERIE} WHERE c.id = ?", (texto_id,)).fetchone()
    return dict(linha) if linha else None


def obter_completo(texto_id: str, db_path: str = DB_PATH) -> dict | None:
    """Tudo o que a página de leitura precisa, incluindo o texto inteiro."""
    with conectar(db_path) as conn:
        linha = conn.execute(
            f"SELECT {CAMPOS}, c.texto_completo FROM contos c {JOIN_SERIE} WHERE c.id = ?", (texto_id,)
        ).fetchone()
    return dict(linha) if linha else None


def perfil_autor(autor: str, db_path: str = DB_PATH) -> dict | None:
    """Números do perfil, temas mais frequentes e séries do autor (só
    textos concluídos — os mesmos que o catálogo lista)."""
    with conectar(db_path) as conn:
        linha = conn.execute(
            f"SELECT COUNT(*) AS total, SUM(c.estrelas) AS estrelas, "
            f"MIN(substr(c.data_pub, 1, 4)) AS ano_min, MAX(substr(c.data_pub, 1, 4)) AS ano_max "
            f"FROM contos c WHERE {BASE_WHERE} AND c.autor = ?",
            (autor,),
        ).fetchone()
        if not linha or not linha["total"]:
            return None
        temas = conn.execute(
            f"SELECT j.value AS tema, COUNT(*) AS n FROM contos c, json_each(c.temas) j "
            f"WHERE {BASE_WHERE} AND c.autor = ? GROUP BY j.value ORDER BY n DESC LIMIT 12",
            (autor,),
        ).fetchall()
        # Pelas partes, não por series.autor: a série guarda uma grafia só do
        # nome, e as partes podem estar em outra ('2H2O'/'2H20').
        series = conn.execute(
            f"SELECT {CAMPOS_SERIE} FROM series s WHERE s.id IN ("
            f"SELECT sc.serie_id FROM serie_contos sc JOIN contos c ON c.id = sc.conto_id "
            f"WHERE {BASE_WHERE} AND c.autor = ?) ORDER BY s.ultima_pub DESC",
            (autor,),
        ).fetchall()
        por_ano = conn.execute(
            f"SELECT substr(c.data_pub, 1, 4) AS ano, COUNT(*) AS n FROM contos c "
            f"WHERE {BASE_WHERE} AND c.autor = ? AND c.data_pub IS NOT NULL GROUP BY ano ORDER BY ano",
            (autor,),
        ).fetchall()
        (em_series,) = conn.execute(
            f"SELECT COUNT(*) FROM contos c WHERE {BASE_WHERE} AND c.autor = ? AND {SO_SERIES}", (autor,)
        ).fetchone()
        grafias = _outras_grafias(conn, autor)
    return {
        **dict(linha),
        "temas": [(t["tema"], t["n"]) for t in temas],
        "series": [dict(s) for s in series],
        "n_series": len(series),
        "por_ano": [(a["ano"], a["n"]) for a in por_ano],
        "em_series": em_series,
        "avulsos": linha["total"] - em_series,
        "grafias": grafias,
    }


def _outras_grafias(conn, autor: str) -> list[tuple[str, int]]:
    """O mesmo nome escrito de outro jeito ('Biel'/'BIEL', '2H2O'/'2H20' —
    series.chave_autor), com quantos textos cada grafia tem no catálogo."""
    from series import chave_autor, grafias

    outras = [g for g in grafias(conn, [autor]) if g != autor and chave_autor(g) == chave_autor(autor)]
    if not outras:
        return []
    marcadores = ",".join("?" * len(outras))
    return [(l["autor"], l["n"]) for l in conn.execute(
        f"SELECT c.autor, COUNT(*) AS n FROM contos c WHERE {BASE_WHERE} AND c.autor IN ({marcadores}) "
        f"GROUP BY c.autor ORDER BY n DESC", outras)]


def capitulos_serie(serie_id: str, db_path: str = DB_PATH) -> list[dict]:
    """Partes de uma série na ordem de leitura, com o começo do texto e o
    número de palavras (contado no SQL — espaços + 1 — pra não trazer o
    texto inteiro de cada parte)."""
    with conectar(db_path) as conn:
        linhas = conn.execute(
            f"SELECT c.id, c.titulo, c.autor, c.data_pub, c.estrelas, c.trecho, sc.parte, "
            f"CASE WHEN c.texto_completo IS NULL THEN NULL ELSE "
            f"length(c.texto_completo) - length(replace(c.texto_completo, ' ', '')) + 1 END AS palavras "
            f"FROM serie_contos sc JOIN contos c ON c.id = sc.conto_id "
            f"WHERE sc.serie_id = ? AND {BASE_WHERE} ORDER BY sc.parte",
            (serie_id,),
        ).fetchall()
    return [dict(linha) for linha in linhas]


# ---------------------------------------------------------------- séries

ORDENS_SERIES = {
    "recentes": "s.ultima_pub DESC",
    "partes": "s.n_partes DESC, s.ultima_pub DESC",
    "estrelas": "s.estrelas DESC, s.ultima_pub DESC",
    "titulo": "s.nome COLLATE NOCASE ASC",
}
CAMPOS_SERIE = (
    "s.id, s.autor, s.nome, s.origem, s.n_partes, s.primeiro_texto, s.primeira_pub, s.ultima_pub, s.estrelas, s.temas, "
    # Pro card: começo da parte 1 (sinopse), palavras somadas (tempo de
    # leitura; espaços + 1, sem trazer os textos) e quantas partes têm 👍.
    "(SELECT c.trecho FROM contos c WHERE c.id = s.primeiro_conto) AS trecho, "
    "(SELECT SUM(length(c.texto_completo) - length(replace(c.texto_completo, ' ', '')) + 1) "
    " FROM serie_contos sc JOIN contos c ON c.id = sc.conto_id WHERE sc.serie_id = s.id) AS palavras, "
    "(SELECT COUNT(*) FROM serie_contos sc JOIN favoritos f ON f.conto_id = sc.conto_id "
    " WHERE sc.serie_id = s.id AND f.tipo = 'gostei') AS gostei"
)


def _where_series(filtros: dict | None, texto: str | None) -> tuple[str, list]:
    """Filtros da barra lateral que fazem sentido pra uma série: autor, temas
    (entre os mais frequentes das partes), período (a série cruza o intervalo)
    e estrelas (somadas)."""
    filtros = filtros or {}
    clausulas, params = ["1=1"], []
    if filtros.get("autor"):
        clausulas.append("s.autor = ?")
        params.append(filtros["autor"])
    if filtros.get("temas"):
        marcadores = ",".join("?" * len(filtros["temas"]))
        clausulas.append(f"EXISTS (SELECT 1 FROM json_each(s.temas) WHERE json_each.value IN ({marcadores}))")
        params.extend(filtros["temas"])
    if filtros.get("ano_min"):
        clausulas.append("CAST(substr(s.ultima_pub, 1, 4) AS INTEGER) >= ?")
        params.append(int(filtros["ano_min"]))
    if filtros.get("ano_max"):
        clausulas.append("CAST(substr(s.primeira_pub, 1, 4) AS INTEGER) <= ?")
        params.append(int(filtros["ano_max"]))
    if filtros.get("estrelas_min"):
        clausulas.append("s.estrelas >= ?")
        params.append(int(filtros["estrelas_min"]))
    for palavra in re.findall(r"\w+", texto or ""):
        clausulas.append("(s.nome LIKE ? OR s.autor LIKE ?)")
        params.extend([f"%{palavra}%"] * 2)
    return " AND ".join(clausulas), params


def contar_series(filtros: dict | None = None, texto: str | None = None, db_path: str = DB_PATH) -> int:
    where, params = _where_series(filtros, texto)
    with conectar(db_path) as conn:
        (n,) = conn.execute(f"SELECT COUNT(*) FROM series s WHERE {where}", params).fetchone()
    return n


def listar_series(filtros: dict | None = None, texto: str | None = None, ordem: str = "recentes",
                  limite: int = 24, offset: int = 0, db_path: str = DB_PATH) -> list[dict]:
    where, params = _where_series(filtros, texto)
    with conectar(db_path) as conn:
        linhas = conn.execute(
            f"SELECT {CAMPOS_SERIE} FROM series s WHERE {where} "
            f"ORDER BY {ORDENS_SERIES.get(ordem, ORDENS_SERIES['recentes'])} LIMIT ? OFFSET ?",
            [*params, limite, offset],
        ).fetchall()
    return [dict(linha) for linha in linhas]


def obter_series(ids: list[str], db_path: str = DB_PATH) -> dict[str, dict]:
    if not ids:
        return {}
    with conectar(db_path) as conn:
        linhas = conn.execute(
            f"SELECT {CAMPOS_SERIE} FROM series s WHERE s.id IN ({','.join('?' * len(ids))})", ids
        ).fetchall()
    return {linha["id"]: dict(linha) for linha in linhas}


def resumo_series(db_path: str = DB_PATH) -> dict:
    with conectar(db_path) as conn:
        linha = conn.execute(
            "SELECT COUNT(*) AS series, COALESCE(SUM(n_partes), 0) AS contos, "
            "COALESCE(SUM(origem = 'site'), 0) AS do_site, COALESCE(SUM(origem = 'titulo'), 0) AS do_titulo "
            "FROM series"
        ).fetchone()
    return dict(linha)
