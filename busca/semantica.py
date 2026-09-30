"""Busca vetorial: texto livre -> embedding -> busca por similaridade no Qdrant
(textos e, na coleção de séries, séries inteiras)."""

from qdrant_client import models

from config import DB_PATH, QDRANT_COLLECTION, QDRANT_COLLECTION_SERIES
from db import conectar
from embeddings.enviar_qdrant import _id_numerico, _id_serie, obter_client
from embeddings.gerar import embedding_texto_livre
from busca.catalogo import CAMPOS, JOIN_SERIE, obter_series
from busca.filtros import mapa_ids, montar_filtro, montar_filtro_series


def _hidratar(resultados: list[tuple[str, float]], db_path: str) -> list[dict]:
    """Junta os resultados (slug, score) do Qdrant com os dados reais do SQLite,
    preservando a ordem do ranking."""
    slugs = [slug for slug, _ in resultados]
    if not slugs:
        return []
    marcadores = ",".join("?" * len(slugs))
    with conectar(db_path) as conn:
        linhas = {
            linha["id"]: dict(linha)
            for linha in conn.execute(
                f"SELECT {CAMPOS} FROM contos c {JOIN_SERIE} WHERE c.id IN ({marcadores})", slugs,
            )
        }
    saida = []
    for slug, score in resultados:
        doc = linhas.get(slug)
        if doc is None:
            continue
        saida.append({**doc, "score": score})
    return saida


def busca_semantica(texto: str, limite: int = 20, filtros: dict | None = None, db_path: str = DB_PATH) -> list[dict]:
    client = obter_client()
    vetor = embedding_texto_livre(texto)
    resposta = client.query_points(
        collection_name=QDRANT_COLLECTION,
        query=vetor,
        query_filter=montar_filtro(filtros),
        limit=limite,
    )
    ids = mapa_ids(db_path)
    resultados = [(ids[ponto.id], ponto.score) for ponto in resposta.points if ponto.id in ids]
    return _hidratar(resultados, db_path)


def mais_parecidos(texto_id: str, limite: int = 20, db_path: str = DB_PATH) -> list[dict]:
    client = obter_client()
    id_numerico = _id_numerico(texto_id)
    resposta = client.query_points(
        collection_name=QDRANT_COLLECTION,
        query=models.RecommendQuery(recommend=models.RecommendInput(positive=[id_numerico])),
        limit=limite,
    )
    ids = mapa_ids(db_path)
    resultados = [
        (ids[ponto.id], ponto.score)
        for ponto in resposta.points
        if ponto.id in ids and ponto.id != id_numerico
    ]
    return _hidratar(resultados, db_path)


def _hidratar_series(pontos, db_path: str) -> list[dict]:
    """Pontos da coleção de séries -> linhas de `series`, na ordem do ranking
    (séries que sumiram do SQLite desde a última sincronização ficam de fora)."""
    series = obter_series([p.payload["serie_id"] for p in pontos], db_path)
    return [{**series[p.payload["serie_id"]], "score": p.score} for p in pontos if p.payload["serie_id"] in series]


def busca_series(texto: str, limite: int = 20, filtros: dict | None = None, db_path: str = DB_PATH) -> list[dict]:
    """Séries pelo assunto: o vetor de cada série é a média das partes."""
    resposta = obter_client().query_points(
        collection_name=QDRANT_COLLECTION_SERIES,
        query=embedding_texto_livre(texto),
        query_filter=montar_filtro_series(filtros),
        limit=limite,
        with_payload=["serie_id"],
    )
    return _hidratar_series(resposta.points, db_path)


def series_parecidas(serie_id: str, limite: int = 12, db_path: str = DB_PATH) -> list[dict]:
    """Outras séries parecidas com esta (de outros autores ou do mesmo)."""
    resposta = obter_client().query_points(
        collection_name=QDRANT_COLLECTION_SERIES,
        query=models.RecommendQuery(recommend=models.RecommendInput(positive=[_id_serie(serie_id)])),
        limit=limite + 1,
        with_payload=["serie_id"],
    )
    return [s for s in _hidratar_series(resposta.points, db_path) if s["id"] != serie_id][:limite]
