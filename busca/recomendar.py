"""Recomendação por perfil (favoritos) e busca híbrida (vetorial + FTS5 com RRF)."""

from qdrant_client import models

from config import DB_PATH, QDRANT_COLLECTION
from db import conectar
from embeddings.enviar_qdrant import obter_client
from busca.catalogo import buscar_fts
from busca.filtros import mapa_ids, montar_filtro, slugs_para_ids
from busca.semantica import _hidratar, busca_semantica

RRF_K = 60


class SemFavoritos(Exception):
    pass


def recomendar_por_perfil(limite: int = 20, filtros: dict | None = None, db_path: str = DB_PATH) -> list[dict]:
    with conectar(db_path) as conn:
        gostei = [l["conto_id"] for l in conn.execute("SELECT conto_id FROM favoritos WHERE tipo = 'gostei'")]
        nao_gostei = [l["conto_id"] for l in conn.execute("SELECT conto_id FROM favoritos WHERE tipo = 'nao_gostei'")]

    if not gostei and not nao_gostei:
        raise SemFavoritos("nenhum favorito marcado ainda")

    client = obter_client()
    resposta = client.query_points(
        collection_name=QDRANT_COLLECTION,
        query=models.RecommendQuery(
            recommend=models.RecommendInput(
                positive=slugs_para_ids(gostei),
                negative=slugs_para_ids(nao_gostei),
            )
        ),
        query_filter=montar_filtro(filtros),
        limit=limite,
    )
    ids = mapa_ids(db_path)
    resultados = [(ids[ponto.id], ponto.score) for ponto in resposta.points if ponto.id in ids]
    return _hidratar(resultados, db_path)


def busca_hibrida(texto: str, limite: int = 20, filtros: dict | None = None, db_path: str = DB_PATH) -> list[dict]:
    resultado_semantico = busca_semantica(texto, limite=limite, filtros=filtros, db_path=db_path)
    slugs_semantico = [item["id"] for item in resultado_semantico]
    slugs_fts = buscar_fts(texto, filtros=filtros, limite=limite, db_path=db_path)

    # Reciprocal Rank Fusion: soma 1/(k+posição) em cada lista onde o item aparece
    pontuacao: dict[str, float] = {}
    for posicao, slug in enumerate(slugs_semantico):
        pontuacao[slug] = pontuacao.get(slug, 0.0) + 1 / (RRF_K + posicao)
    for posicao, slug in enumerate(slugs_fts):
        pontuacao[slug] = pontuacao.get(slug, 0.0) + 1 / (RRF_K + posicao)

    ordenados = sorted(pontuacao.items(), key=lambda x: x[1], reverse=True)[:limite]
    return _hidratar(ordenados, db_path)
