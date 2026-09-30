"""Texto completo -> trechos de ~TRECHO_TOKENS tokens -> vetores.

O modelo da v1 (MiniLM) corta a entrada em 128 tokens, então só enxerga
título, tags e as primeiras frases de cada texto. Aqui o texto inteiro vira
vários pontos no Qdrant (um por trecho, com `texto_id` no payload) e a busca
agrupa por texto — uma cena no meio da história já basta pro texto aparecer.

Cada trecho guarda só os offsets de caractere (`inicio`, `fim`) do
texto_completo, não o texto em si: o payload fica pequeno e a busca recorta
o trecho encontrado direto do SQLite na hora de mostrar."""

import json
import re
import time

import numpy as np
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from busca_v2 import nucleo
from busca_v2.tags import canonizar
from config import (
    EMBEDDING_MODEL_V2,
    QDRANT_API_KEY,
    QDRANT_COLLECTION_TRECHOS,
    QDRANT_URL,
    QDRANT_V2_LOCAL_PATH,
    TRECHO_SOBREPOSICAO,
    TRECHO_TOKENS,
    TRECHOS_MAX_POR_TEXTO,
)

_modelos: dict = {}
_client: QdrantClient | None = None


def modelo(nome: str = EMBEDDING_MODEL_V2):
    if nome not in _modelos:
        from sentence_transformers import SentenceTransformer

        _modelos[nome] = SentenceTransformer(nome)
    return _modelos[nome]


def nome_colecao(modo: str, nome_modelo: str = EMBEDDING_MODEL_V2) -> str:
    """Uma coleção por modo e por modelo: dá pra comparar modelos lado a lado
    e a amostra nunca se mistura com a indexação completa."""
    sufixo = re.sub(r"[^a-z0-9]+", "-", nome_modelo.split("/")[-1].lower()).strip("-")
    base = QDRANT_COLLECTION_TRECHOS if modo == "completo" else f"{QDRANT_COLLECTION_TRECHOS}_amostra"
    return f"{base}__{sufixo}"


def obter_client() -> QdrantClient:
    """Servidor se QDRANT_URL estiver setado (no host: http://localhost:6333,
    o Qdrant do docker-compose); senão um Qdrant local num diretório próprio,
    separado do qdrant_local/ da v1 pra não disputar o lock de arquivo."""
    global _client
    if _client is None:
        if QDRANT_URL:
            _client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
        else:
            _client = QdrantClient(path=QDRANT_V2_LOCAL_PATH)
    return _client


def modo_local() -> bool:
    return not QDRANT_URL


def fechar_client() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None


_colecoes_trechos: list[str] | None = None


def remover_textos(texto_ids: list[str]) -> None:
    """Apaga os trechos dos textos de todas as coleções v2 do servidor.
    No modo local não faz nada: abrir o diretório local a cada texto
    removido carregaria a coleção inteira toda vez (e disputaria o lock com
    quem estiver buscando); lá a coleção de amostra se refaz com
    busca_v2/indexar.py."""
    global _colecoes_trechos
    if modo_local():
        return
    client = obter_client()
    if _colecoes_trechos is None:
        _colecoes_trechos = [c.name for c in client.get_collections().collections
                             if c.name.startswith(QDRANT_COLLECTION_TRECHOS)]
    filtro = qmodels.Filter(must=[qmodels.FieldCondition(key="conto_id", match=qmodels.MatchAny(any=texto_ids))])
    for colecao in _colecoes_trechos:
        client.delete(collection_name=colecao, points_selector=qmodels.FilterSelector(filter=filtro))


def garantir_colecao(client: QdrantClient, colecao: str, dimensao: int) -> None:
    if client.collection_exists(colecao):
        return
    # Vetores originais em disco e cópia int8 em RAM: com a base inteira são
    # ~1,5 mi de trechos, e 7 GB de RAM não comportam tudo em float32.
    client.create_collection(
        collection_name=colecao,
        vectors_config=qmodels.VectorParams(size=dimensao, distance=qmodels.Distance.COSINE, on_disk=True),
        quantization_config=qmodels.ScalarQuantization(
            scalar=qmodels.ScalarQuantizationConfig(type=qmodels.ScalarType.INT8, always_ram=True)
        ),
    )
    if not modo_local():
        # group_by precisa de índice no campo pra não varrer payloads.
        client.create_payload_index(colecao, "conto_id", qmodels.PayloadSchemaType.KEYWORD)
        for campo, tipo in (("temas", "KEYWORD"), ("autor", "KEYWORD"), ("ano", "INTEGER"), ("estrelas", "INTEGER")):
            client.create_payload_index(colecao, campo, getattr(qmodels.PayloadSchemaType, tipo))


def dividir(doc: dict, nome_modelo: str = EMBEDDING_MODEL_V2, tokens: int = TRECHO_TOKENS,
            sobreposicao: int = TRECHO_SOBREPOSICAO, maximo: int = TRECHOS_MAX_POR_TEXTO) -> list[dict]:
    """Trechos de um texto (regras em nucleo.montar — as mesmas da GPU alugada)."""
    texto = doc.get("texto_completo") or doc.get("trecho") or ""
    if not texto.strip():
        return []
    offsets = modelo(nome_modelo).tokenizer(texto, add_special_tokens=False, return_offsets_mapping=True)["offset_mapping"]
    temas = canonizar(doc.get("temas"))
    return nucleo.montar(texto, doc.get("titulo") or "", temas, offsets, nome_modelo, tokens, sobreposicao, maximo)


def vetores_trechos(textos: list[str], nome_modelo: str = EMBEDDING_MODEL_V2, batch_size: int = 16) -> np.ndarray:
    return modelo(nome_modelo).encode(textos, batch_size=batch_size, normalize_embeddings=True,
                                      show_progress_bar=False)


def vetor_consulta(texto: str, nome_modelo: str = EMBEDDING_MODEL_V2) -> list[float]:
    prefixo, _ = nucleo.prefixos(nome_modelo)
    vetor = modelo(nome_modelo).encode(f"{prefixo}{texto}", normalize_embeddings=True, show_progress_bar=False)
    return np.asarray(vetor, dtype=np.float32).tolist()


def payload(doc: dict, trecho: dict) -> dict:
    return {
        "conto_id": doc["id"],
        "ordem": trecho["ordem"],
        "inicio": trecho["inicio"],
        "fim": trecho["fim"],
        # mesmos campos da coleção v1 — busca.filtros.montar_filtro serve pras duas;
        # tags já normalizadas (busca_v2/tags.py), então o filtro também precisa canonizar
        "temas": canonizar(doc.get("temas")),
        "ano": int(doc["data_pub"][:4]) if doc.get("data_pub") else None,
        "estrelas": doc.get("estrelas"),
        "autor": doc.get("autor"),
    }


class Cronometro:
    """Taxa e ETA pro log de progresso."""

    def __init__(self, total: int):
        self.total = total
        self.feitos = 0
        self.t0 = time.time()

    def avancar(self, n: int) -> str:
        self.feitos += n
        decorrido = time.time() - self.t0
        taxa = self.feitos / decorrido if decorrido else 0
        restante = (self.total - self.feitos) / taxa if taxa else 0
        return f"{self.feitos}/{self.total} · {taxa:.2f}/s · faltam ~{_duracao(restante)}"


def _duracao(segundos: float) -> str:
    if segundos < 90:
        return f"{segundos:.0f}s"
    if segundos < 5400:
        return f"{segundos / 60:.0f}min"
    return f"{segundos / 3600:.1f}h"
