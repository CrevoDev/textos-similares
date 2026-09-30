"""Busca v2: consulta -> trechos mais próximos -> agrupados por texto
(o melhor trecho de cada texto define a posição) -> reranker opcional.

    DB_PATH=data/contos.db uv run python busca_v2/buscar.py "casal viaja pra praia e reencontra ex"
    DB_PATH=data/contos.db uv run python busca_v2/buscar.py "..." --rerank --comparar

Devolve o mesmo formato de `busca.semantica.busca_semantica` (id, titulo,
autor, temas, url, trecho, estrelas, data_pub, score) mais `trecho_encontrado`,
o pedaço do texto que casou com a consulta — dá pra plugar no app sem mudar
os cards."""

import argparse
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from config import BUSCA_V2_MODO, DB_PATH, EMBEDDING_MODEL, EMBEDDING_MODEL_V2, RERANKER_MODEL  # noqa: E402
from busca.filtros import montar_filtro  # noqa: E402
from busca_v2 import fonte, trechos  # noqa: E402
from busca_v2.tags import canonizar  # noqa: E402

_reranker = None
# Quanto do trecho encontrado vai pro cross-encoder: o modelo padrão é
# treinado com passagens curtas (MS MARCO), e texto demais só deixa mais lento.
RERANK_MAX_CHARS = 1200


def _obter_reranker():
    global _reranker
    if _reranker is None:
        from sentence_transformers import CrossEncoder

        _reranker = CrossEncoder(RERANKER_MODEL, max_length=512)
    return _reranker


def _hidratar(grupos: list[tuple[str, float, dict]], db_path: str) -> list[dict]:
    docs = {c["id"]: c for c in fonte.carregar([texto_id for texto_id, _, _ in grupos], db_path=db_path)}
    saida = []
    for texto_id, score, trecho in grupos:
        doc = docs.get(texto_id)
        if doc is None:
            continue
        texto = doc.pop("texto_completo") or doc.get("trecho") or ""
        saida.append({
            **doc,
            "score": score,
            "trecho_encontrado": texto[trecho["inicio"] : trecho["fim"]],
            "trecho_ordem": trecho["ordem"],
        })
    return saida


def buscar(texto: str, limite: int = 20, filtros: dict | None = None, modo: str = BUSCA_V2_MODO,
           rerank: bool = False, candidatos: int = 50, nome_modelo: str = EMBEDDING_MODEL_V2,
           db_path: str = DB_PATH) -> list[dict]:
    client = trechos.obter_client()
    if filtros:
        # o payload guarda tags canônicas; o app ainda manda a grafia original
        filtros = {**filtros}
        for campo in ("tema", "temas"):
            if filtros.get(campo):
                valor = canonizar([filtros[campo]] if campo == "tema" else filtros[campo])
                filtros[campo] = valor[0] if campo == "tema" else valor
    resposta = client.query_points_groups(
        collection_name=trechos.nome_colecao(modo, nome_modelo),
        query=trechos.vetor_consulta(texto, nome_modelo),
        group_by="conto_id",
        group_size=1,
        limit=max(limite, candidatos) if rerank else limite,
        query_filter=montar_filtro(filtros),
        with_payload=["inicio", "fim", "ordem"],
    )
    grupos = [(g.id, g.hits[0].score, g.hits[0].payload) for g in resposta.groups]
    resultados = _hidratar(grupos, db_path)
    if not rerank or not resultados:
        return resultados[:limite]

    pares = [(texto, f"{r['titulo'] or ''}\n{r['trecho_encontrado'][:RERANK_MAX_CHARS]}") for r in resultados]
    notas = _obter_reranker().predict(pares, batch_size=16, show_progress_bar=False)
    for r, nota in zip(resultados, notas):
        r["score_vetor"] = r["score"]
        r["score"] = float(nota)
    return sorted(resultados, key=lambda r: r["score"], reverse=True)[:limite]


# --- Linha de base v1 restrita ao mesmo conjunto de textos ------------------
# Comparar a v2 da amostra (2 mil textos) com a v1 da coleção inteira seria
# injusto: a v1 teria 40x mais concorrentes pra cada resultado. Aqui a v1 roda
# em memória sobre os vetores que o pipeline já gravou em `embeddings`, só
# para os textos do conjunto avaliado.

_cache_v1: dict[tuple[str, ...], tuple[list[str], np.ndarray]] = {}


def _vetores_v1(ids: list[str], db_path: str) -> tuple[list[str], np.ndarray]:
    chave = tuple(ids)
    if chave not in _cache_v1:
        _cache_v1.clear()
        encontrados, vetores = [], []
        with fonte.conectar_fonte(db_path) as conn:
            for i in range(0, len(ids), 500):
                parte = ids[i : i + 500]
                marcadores = ",".join("?" * len(parte))
                for l in conn.execute(
                    f"SELECT conto_id, vetor FROM embeddings WHERE conto_id IN ({marcadores})", parte
                ):
                    encontrados.append(l["conto_id"])
                    vetores.append(np.frombuffer(l["vetor"], dtype=np.float32))
        matriz = np.vstack(vetores)
        matriz /= np.linalg.norm(matriz, axis=1, keepdims=True)
        _cache_v1[chave] = (encontrados, matriz)
    return _cache_v1[chave]


def buscar_v1(texto: str, ids: list[str], limite: int = 20, db_path: str = DB_PATH) -> list[tuple[str, float]]:
    encontrados, matriz = _vetores_v1(ids, db_path)
    consulta = trechos.modelo(EMBEDDING_MODEL).encode(texto, normalize_embeddings=True, show_progress_bar=False)
    notas = matriz @ np.asarray(consulta, dtype=np.float32)
    ordem = np.argsort(-notas)[:limite]
    return [(encontrados[i], float(notas[i])) for i in ordem]


def _imprimir(titulo: str, itens: list[tuple[str, float, str]]) -> None:
    print(f"\n=== {titulo} ===")
    for pos, (rotulo, score, trecho) in enumerate(itens, 1):
        print(f"{pos:2d}. [{score:.3f}] {rotulo}")
        if trecho:
            print(textwrap.indent(textwrap.shorten(trecho, 220, placeholder=" …"), "      "))


def main() -> None:
    parser = argparse.ArgumentParser(description="Busca v2 (trechos) pela linha de comando")
    parser.add_argument("consulta")
    parser.add_argument("--modo", default=BUSCA_V2_MODO, choices=fonte.MODOS)
    parser.add_argument("--limite", type=int, default=10)
    parser.add_argument("--rerank", action="store_true")
    parser.add_argument("--comparar", action="store_true", help="mostra também a v1 sobre os mesmos textos")
    parser.add_argument("--db-path", default=DB_PATH)
    args = parser.parse_args()
    try:
        resultados = buscar(args.consulta, limite=args.limite, modo=args.modo, rerank=args.rerank, db_path=args.db_path)
        _imprimir(f"v2 trechos{' + rerank' if args.rerank else ''}",
                  [(f"{r['titulo']} — {r['autor']} ({r['id']})", r["score"], r["trecho_encontrado"]) for r in resultados])
        if args.comparar:
            ids = fonte.indexados(trechos.nome_colecao(args.modo))
            v1 = buscar_v1(args.consulta, ids, limite=args.limite, db_path=args.db_path)
            docs = {c["id"]: c for c in fonte.carregar([i for i, _ in v1], db_path=args.db_path)}
            _imprimir("v1 (título + tags + começo)", [
                (f"{textos[i]['titulo']} — {textos[i]['autor']} ({i})", s, "") for i, s in v1 if i in docs
            ])
    except fonte.ExtracaoIncompleta as e:
        sys.exit(f"[v2] {e}")
    finally:
        trechos.fechar_client()


if __name__ == "__main__":
    main()
