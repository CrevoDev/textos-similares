"""Fila `textos`, estágio `indexacao`: upsert do vetor + payload enxuto no
Qdrant. Em modo mock/desenvolvimento, usa sempre o Qdrant local (`path=`),
nunca o servidor — ver config.MOCK_MODE."""

import hashlib
import json
import time

import numpy as np
from crevomq import ErroPermanente
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from config import (
    DB_PATH,
    MOCK_MODE,
    QDRANT_API_KEY,
    QDRANT_COLLECTION,
    QDRANT_COLLECTION_SERIES,
    QDRANT_LOCAL_PATH,
    QDRANT_URL,
)
from db import conectar
from embeddings.gerar import obter_vetor

_client = None
_colecao_ok = False

# QdrantClient(path=...) local é exclusivo por processo — com o app.py (busca)
# e este worker abrindo/fechando o mesmo diretório o tempo todo, colisão
# transitória é esperada, não excepcional. `obter_client` insiste algumas
# vezes com um intervalo curto antes de desistir (e aí o job volta pra fila).
TENTATIVAS_LOCK = 5
ESPERA_LOCK_S = 1.0


def modo_local() -> bool:
    return MOCK_MODE or not QDRANT_URL


def obter_client() -> QdrantClient:
    global _client
    if _client is not None:
        return _client
    if modo_local():
        ultimo_erro = None
        for tentativa in range(1, TENTATIVAS_LOCK + 1):
            try:
                _client = QdrantClient(path=QDRANT_LOCAL_PATH)
                return _client
            except RuntimeError as e:
                ultimo_erro = e
                if tentativa < TENTATIVAS_LOCK:
                    time.sleep(ESPERA_LOCK_S)
        raise ultimo_erro
    _client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
    return _client


def fechar_client() -> None:
    """QdrantClient(path=...) local mantém um lock de arquivo exclusivo — só
    um processo pode ter o Qdrant local aberto por vez. No modo local o
    handler fecha depois de cada job pra liberar o app de busca (outro
    processo). No modo servidor não há lock e o client fica aberto."""
    global _client, _colecao_ok
    if _client is not None:
        _client.close()
        _client = None
        _colecao_ok = False


def garantir_colecao(client: QdrantClient, dimensao: int) -> None:
    global _colecao_ok
    if _colecao_ok:
        return
    colecoes = {c.name for c in client.get_collections().collections}
    if QDRANT_COLLECTION not in colecoes:
        client.create_collection(
            collection_name=QDRANT_COLLECTION,
            vectors_config=qmodels.VectorParams(size=dimensao, distance=qmodels.Distance.COSINE),
        )
    _colecao_ok = True


def _payload(item: dict) -> dict:
    temas = json.loads(item["temas"]) if item.get("temas") else []
    ano = None
    if item.get("data_pub"):
        ano = int(item["data_pub"][:4])
    return {
        "temas": temas,
        "ano": ano,
        "estrelas": item.get("estrelas"),
        "autor": item.get("autor"),
    }


def indexar(job: dict, db_path: str = DB_PATH) -> dict:
    """Handler do estágio `indexacao`. É o último: publica o texto no
    catálogo do app (textos.estagio = 'concluido')."""
    if job["payload"].get("descartado"):
        return job["payload"]
    texto_id = job["id"]
    with conectar(db_path) as conn:
        linha = conn.execute(
            "SELECT temas, data_pub, estrelas, autor FROM contos WHERE id = ?", (texto_id,)
        ).fetchone()
    if linha is None:
        raise ErroPermanente("texto não está mais no banco")
    vetor = obter_vetor(texto_id, db_path=db_path)
    if vetor is None:
        raise ErroPermanente("texto sem embedding gerado")

    client = obter_client()
    try:
        garantir_colecao(client, dimensao=len(vetor))
        client.upsert(
            collection_name=QDRANT_COLLECTION,
            points=[qmodels.PointStruct(id=_id_numerico(texto_id), vector=vetor, payload=_payload(dict(linha)))],
        )
    finally:
        if modo_local():
            fechar_client()

    with conectar(db_path) as conn:
        conn.execute("UPDATE contos SET estagio = 'concluido', status = 'ok' WHERE id = ?", (texto_id,))
    # Parte nova pode completar (ou criar) uma série do autor. O upsert acima
    # também zerou o `serie_id` do ponto — a fila de séries regrava.
    if linha["autor"]:
        import pipeline

        pipeline.enfileirar_series([linha["autor"]], id=texto_id)
    return job["payload"]


def remover(texto_ids: list[str]) -> None:
    """Tira os pontos dos textos da coleção, numa chamada só (sem erro para
    os que não estiverem lá)."""
    client = obter_client()
    try:
        if client.collection_exists(QDRANT_COLLECTION):
            client.delete(
                collection_name=QDRANT_COLLECTION,
                points_selector=qmodels.PointIdsList(points=[_id_numerico(c) for c in texto_ids]),
            )
    finally:
        if modo_local():
            fechar_client()


# ---------------------------------------------------------------- séries

_series_ok = False


def _id_serie(serie_id: str) -> int:
    return int(serie_id[:15], 16)


def _garantir_series(client: QdrantClient, dimensao: int) -> None:
    """Coleção de séries e os índices de payload que o filtro "só séries" e
    a troca por autor usam (índice só existe no modo servidor)."""
    global _series_ok
    if _series_ok:
        return
    if not client.collection_exists(QDRANT_COLLECTION_SERIES):
        client.create_collection(
            collection_name=QDRANT_COLLECTION_SERIES,
            vectors_config=qmodels.VectorParams(size=dimensao, distance=qmodels.Distance.COSINE),
        )
    if not modo_local():
        keyword = qmodels.PayloadSchemaType.KEYWORD
        client.create_payload_index(QDRANT_COLLECTION_SERIES, "autor", keyword)
        client.create_payload_index(QDRANT_COLLECTION, "autor", keyword)
        client.create_payload_index(QDRANT_COLLECTION, "serie_id", keyword)
    _series_ok = True


def _filtro_autores(autores: list[str]) -> qmodels.Filter:
    return qmodels.Filter(must=[qmodels.FieldCondition(key="autor", match=qmodels.MatchAny(any=autores))])


def sincronizar_series(autores: list[str], db_path: str = DB_PATH) -> int:
    """Leva ao Qdrant as séries destes autores (já gravadas no SQLite por
    series.agrupar_autores): troca os pontos deles na coleção de séries
    (vetor = média normalizada dos vetores das partes) e regrava `serie_id`
    no payload dos pontos dos textos. Retorna quantas séries enviou."""
    marcadores = ",".join("?" * len(autores))
    with conectar(db_path) as conn:
        series = [dict(l) for l in conn.execute(
            f"SELECT id, autor, nome, origem, n_partes, primeira_pub, ultima_pub, estrelas, temas "
            f"FROM series WHERE autor IN ({marcadores})", autores)]
        membros: dict[str, list[str]] = {}
        for l in conn.execute(
            f"SELECT sc.serie_id, sc.conto_id FROM serie_contos sc JOIN series s ON s.id = sc.serie_id "
            f"WHERE s.autor IN ({marcadores}) ORDER BY sc.parte", autores
        ):
            membros.setdefault(l["serie_id"], []).append(l["conto_id"])
        todos = [c for ids in membros.values() for c in ids]
        vetores: dict[str, np.ndarray] = {}
        for i in range(0, len(todos), 500):
            parte = todos[i:i + 500]
            for l in conn.execute(
                f"SELECT conto_id, vetor FROM embeddings WHERE conto_id IN ({','.join('?' * len(parte))})", parte
            ):
                vetores[l["conto_id"]] = np.frombuffer(l["vetor"], dtype=np.float32)

    pontos = []
    for s in series:
        vs = [vetores[c] for c in membros.get(s["id"], []) if c in vetores]
        if not vs:
            continue
        media = np.mean(vs, axis=0)
        media /= np.linalg.norm(media) or 1.0
        pontos.append(qmodels.PointStruct(
            id=_id_serie(s["id"]), vector=media.tolist(),
            payload={"serie_id": s["id"], "autor": s["autor"], "nome": s["nome"], "origem": s["origem"],
                     "n_partes": s["n_partes"], "estrelas": s["estrelas"], "temas": json.loads(s["temas"] or "[]"),
                     "ano_min": int(s["primeira_pub"][:4]) if s["primeira_pub"] else None,
                     "ano_max": int(s["ultima_pub"][:4]) if s["ultima_pub"] else None},
        ))

    client = obter_client()
    try:
        if pontos or client.collection_exists(QDRANT_COLLECTION_SERIES):
            _garantir_series(client, dimensao=len(pontos[0].vector) if pontos else 384)
            client.delete(QDRANT_COLLECTION_SERIES,
                          points_selector=qmodels.FilterSelector(filter=_filtro_autores(autores)))
            if pontos:
                client.upsert(QDRANT_COLLECTION_SERIES, points=pontos)
        # Uma requisição pros textos: tira o serie_id de todos os textos dos
        # autores e grava de novo nos que estão em série. Por filtro de autor
        # (não por id), pra não depender de todo texto do SQLite estar no Qdrant.
        operacoes: list = [qmodels.DeletePayloadOperation(
            delete_payload=qmodels.DeletePayload(keys=["serie_id"], filter=_filtro_autores(autores)))]
        operacoes += [
            qmodels.SetPayloadOperation(set_payload=qmodels.SetPayload(
                payload={"serie_id": serie_id}, points=[_id_numerico(c) for c in ids]))
            for serie_id, ids in membros.items()
        ]
        client.batch_update_points(QDRANT_COLLECTION, update_operations=operacoes)
    finally:
        if modo_local():
            fechar_client()
    return len(pontos)


def _id_numerico(texto_id: str) -> int:
    """Qdrant exige ID inteiro ou UUID; deriva um inteiro estável do slug."""
    return int(hashlib.sha1(texto_id.encode()).hexdigest()[:12], 16)
