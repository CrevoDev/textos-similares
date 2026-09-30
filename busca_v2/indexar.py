"""Indexa os textos em trechos numa coleção própria do Qdrant (busca v2).

    DB_PATH=data/contos.db uv run python busca_v2/indexar.py                  # amostra (BUSCA_V2_LIMITE)
    DB_PATH=data/contos.db uv run python busca_v2/indexar.py --limite 500
    DB_PATH=data/contos.db uv run python busca_v2/indexar.py --modo completo  # só com a extração terminada

Retomável: o progresso fica em busca_v2.db (por coleção), então Ctrl+C e
rodar de novo continua de onde parou. Não mexe na coleção `textos` da v1 nem
nas filas do pipeline."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qdrant_client.http import models as qmodels  # noqa: E402

from config import BUSCA_V2_LIMITE, BUSCA_V2_MODO, DB_PATH, EMBEDDING_MODEL_V2  # noqa: E402
from busca_v2 import fonte, trechos  # noqa: E402
from embeddings.enviar_qdrant import _id_numerico  # noqa: E402


def indexar_textos(client, colecao: str, docs: list[dict], nome_modelo: str = EMBEDDING_MODEL_V2) -> int:
    """Divide, vetoriza, faz o upsert e registra no estado. Devolve os trechos
    enviados. Também é o handler por texto da fila (busca_v2/fila.py)."""
    registros: list[tuple[str, int, str | None]] = []
    pontos_textos: list[tuple[dict, dict]] = []
    for doc in docs:
        if fonte.excluido(doc.get("temas")):
            registros.append((doc["id"], 0, "excluido"))
            continue
        partes = trechos.dividir(doc, nome_modelo)
        if not partes:
            registros.append((doc["id"], 0, "sem_texto"))
            continue
        pontos_textos += [(doc, t) for t in partes]
        registros.append((doc["id"], len(partes), None))

    if pontos_textos:
        vetores = trechos.vetores_trechos([t["texto_embedding"] for _, t in pontos_textos], nome_modelo)
        trechos.garantir_colecao(client, colecao, dimensao=vetores.shape[1])
        client.upsert(
            collection_name=colecao,
            points=[
                qmodels.PointStruct(
                    id=_id_numerico(f"{texto['id']}#{t['ordem']}"),
                    vector=vetor.tolist(),
                    payload=trechos.payload(doc, t),
                )
                for (doc, t), vetor in zip(pontos_textos, vetores)
            ],
        )
    # Só marca como feito depois do upsert: se cair no meio, o lote é refeito.
    fonte.registrar_indexados(colecao, registros)
    return len(pontos_textos)


def indexar(modo: str, limite: int, lote: int, nome_modelo: str, reindexar: bool, db_path: str) -> None:
    ids = fonte.selecionar(modo, limite, db_path=db_path)
    colecao = trechos.nome_colecao(modo, nome_modelo)
    client = trechos.obter_client()
    if reindexar:
        if client.collection_exists(colecao):
            client.delete_collection(colecao)
        with fonte.conectar_estado() as estado:
            estado.execute("DELETE FROM trechos_indexados WHERE colecao = ?", (colecao,))
    feitos = fonte.ja_indexados(colecao)
    ids = [i for i in ids if i not in feitos]

    print(f"[v2] modo={modo} coleção={colecao} modelo={nome_modelo}")
    print(f"[v2] {len(feitos)} já indexados, {len(ids)} a indexar")
    if modo == "completo" and trechos.modo_local():
        print("[v2] AVISO: Qdrant local não aguenta a base inteira — defina QDRANT_URL=http://localhost:6333")
    if not ids:
        return

    cronometro = trechos.Cronometro(len(ids))
    total_trechos = 0
    for i in range(0, len(ids), lote):
        docs = fonte.carregar(ids[i : i + lote], db_path=db_path)
        total_trechos += indexar_textos(client, colecao, docs, nome_modelo)
        print(f"[v2] textos {cronometro.avancar(len(textos))} · {total_trechos} trechos", flush=True)

    print(f"[v2] concluído: {fonte.resumo_indexacao(colecao)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Indexação v2 (trechos do texto completo)")
    parser.add_argument("--modo", default=BUSCA_V2_MODO, choices=fonte.MODOS)
    parser.add_argument("--limite", type=int, default=BUSCA_V2_LIMITE, help="só no modo amostra")
    parser.add_argument("--lote", type=int, default=32, help="textos por lote")
    parser.add_argument("--modelo", default=EMBEDDING_MODEL_V2)
    parser.add_argument("--reindexar", action="store_true", help="apaga a coleção deste modo/modelo e refaz")
    parser.add_argument("--db-path", default=DB_PATH)
    args = parser.parse_args()
    try:
        indexar(args.modo, args.limite, args.lote, args.modelo, args.reindexar, args.db_path)
    except fonte.ExtracaoIncompleta as e:
        sys.exit(f"[v2] {e}")
    except KeyboardInterrupt:
        print("\n[v2] interrompido — rode de novo pra continuar de onde parou")
    finally:
        trechos.fechar_client()


if __name__ == "__main__":
    main()
