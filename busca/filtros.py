"""Construção compartilhada do Filter do Qdrant a partir dos filtros de busca
(tema, ano, estrelas mínimas, autor) e o mapa reverso id_numerico -> slug."""

from qdrant_client import models

from config import DB_PATH
from db import conectar
from embeddings.enviar_qdrant import _id_numerico

_cache_mapa_ids: dict[int, str] | None = None


def montar_filtro(filtros: dict | None) -> models.Filter | None:
    if not filtros:
        return None
    condicoes = []
    if filtros.get("tema"):
        condicoes.append(models.FieldCondition(key="temas", match=models.MatchValue(value=filtros["tema"])))
    if filtros.get("temas"):
        # Qualquer um dos temas escolhidos (mesma semântica do catálogo SQL).
        condicoes.append(models.FieldCondition(key="temas", match=models.MatchAny(any=list(filtros["temas"]))))
    if filtros.get("ano"):
        condicoes.append(models.FieldCondition(key="ano", match=models.MatchValue(value=filtros["ano"])))
    if filtros.get("ano_min") or filtros.get("ano_max"):
        condicoes.append(
            models.FieldCondition(
                key="ano", range=models.Range(gte=filtros.get("ano_min") or None, lte=filtros.get("ano_max") or None)
            )
        )
    if filtros.get("estrelas_min") is not None:
        condicoes.append(models.FieldCondition(key="estrelas", range=models.Range(gte=filtros["estrelas_min"])))
    if filtros.get("autor"):
        condicoes.append(models.FieldCondition(key="autor", match=models.MatchValue(value=filtros["autor"])))
    # "Só séries": o ponto tem `serie_id` (gravado pela fila de séries).
    negacoes = [models.IsEmptyCondition(is_empty=models.PayloadField(key="serie_id"))] if filtros.get("so_series") else []
    if not condicoes and not negacoes:
        return None
    return models.Filter(must=condicoes or None, must_not=negacoes or None)


def montar_filtro_series(filtros: dict | None) -> models.Filter | None:
    """Os mesmos filtros, na coleção de séries: temas (entre os mais
    frequentes das partes), autor, período (a série cruza o intervalo) e
    estrelas somadas."""
    filtros = filtros or {}
    condicoes = []
    if filtros.get("temas"):
        condicoes.append(models.FieldCondition(key="temas", match=models.MatchAny(any=list(filtros["temas"]))))
    if filtros.get("autor"):
        condicoes.append(models.FieldCondition(key="autor", match=models.MatchValue(value=filtros["autor"])))
    if filtros.get("ano_min"):
        condicoes.append(models.FieldCondition(key="ano_max", range=models.Range(gte=filtros["ano_min"])))
    if filtros.get("ano_max"):
        condicoes.append(models.FieldCondition(key="ano_min", range=models.Range(lte=filtros["ano_max"])))
    if filtros.get("estrelas_min"):
        condicoes.append(models.FieldCondition(key="estrelas", range=models.Range(gte=filtros["estrelas_min"])))
    return models.Filter(must=condicoes) if condicoes else None


def mapa_ids(db_path: str = DB_PATH) -> dict[int, str]:
    """Cache em memória id_numerico -> slug, construído uma vez por processo.
    Se o dataset crescer muito, isso deveria virar uma tabela de índice no SQLite."""
    global _cache_mapa_ids
    if _cache_mapa_ids is not None:
        return _cache_mapa_ids
    with conectar(db_path) as conn:
        slugs = [linha["id"] for linha in conn.execute("SELECT id FROM contos")]
    _cache_mapa_ids = {_id_numerico(slug): slug for slug in slugs}
    return _cache_mapa_ids


def slugs_para_ids(slugs: list[str]) -> list[int]:
    return [_id_numerico(slug) for slug in slugs]


def invalidar_cache_ids() -> None:
    """O app de busca roda num processo de longa duração (Streamlit) enquanto
    o scraper continua descobrindo/indexando textos novos — chame isso pra
    forçar `mapa_ids()` a reler o SQLite na próxima busca."""
    global _cache_mapa_ids
    _cache_mapa_ids = None
