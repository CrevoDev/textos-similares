"""Passo 3 de 3 da indexação na GPU alugada: vetores gerados lá -> Qdrant daqui.

    DB_PATH=data/contos.db uv run python busca_v2/importar.py            # lê data/gpu_v2/
    DB_PATH=data/contos.db QDRANT_URL=http://localhost:6333 uv run python busca_v2/importar.py  # base inteira

Lê `saida/parte-*.npz` (trazidas da máquina alugada), monta o payload com os
dados atuais do SQLite (tags, ano, estrelas, autor) e faz o upsert na coleção
do manifesto, com os mesmos IDs de ponto da indexação local — dá pra misturar
as duas formas na mesma coleção. Retomável: cada parte só é marcada no estado
depois do upsert, e partes já registradas são puladas. Também registra os
textos que o exportar.py deixou de fora (excluídos/sem texto)."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
from qdrant_client.http import models as qmodels  # noqa: E402

from config import DB_PATH, EMBEDDING_MODEL_V2  # noqa: E402
from busca_v2 import fonte, trechos  # noqa: E402
from busca_v2.exportar import PASTA_PADRAO  # noqa: E402
from embeddings.enviar_qdrant import _id_numerico  # noqa: E402

CAMPOS_PAYLOAD = "id, temas, data_pub, estrelas, autor"


def _registrar_ignorados(pasta: Path, colecao: str) -> int:
    arquivo = pasta / "ignorados.jsonl"
    if not arquivo.exists():
        return 0
    linhas = [json.loads(l) for l in arquivo.read_text(encoding="utf-8").splitlines() if l.strip()]
    fonte.registrar_indexados(colecao, [(l["id"], 0, l["motivo"]) for l in linhas])
    return len(linhas)


def importar_parte(client, caminho: Path, colecao: str, lote: int, db_path: str,
                   feitos: set | None = None) -> int | None:
    """Upsert de uma parte-NNNNN.npz e registro no estado. Devolve os trechos
    enviados, ou None se todos os textos da parte já estavam indexados."""
    dados = np.load(caminho)
    textos_parte = [str(c) for c in dados["contos"]]
    if feitos is None:
        feitos = fonte.ja_indexados(colecao)
    if all(c in feitos for c in textos_parte):
        return None
    texto_ids = [str(c) for c in dados["conto_id"]]
    vetores = dados["vetores"].astype(np.float32)
    if len(vetores):
        trechos.garantir_colecao(client, colecao, dimensao=vetores.shape[1])
    meta = {c["id"]: c for c in fonte.carregar(sorted(set(texto_ids)), db_path=db_path, campos=CAMPOS_PAYLOAD)}

    pontos = []
    for j, texto_id in enumerate(texto_ids):
        doc = meta.get(texto_id)
        if doc is None:  # sumiu do banco depois da exportação
            continue
        trecho = {"ordem": int(dados["ordem"][j]), "inicio": int(dados["inicio"][j]), "fim": int(dados["fim"][j])}
        pontos.append(qmodels.PointStruct(
            id=_id_numerico(f"{texto_id}#{trecho['ordem']}"),
            vector=vetores[j].tolist(),
            payload=trechos.payload(doc, trecho),
        ))
    for k in range(0, len(pontos), lote):
        client.upsert(collection_name=colecao, points=pontos[k : k + lote])

    por_texto: dict[str, int] = {}
    for texto_id in texto_ids:
        por_texto[texto_id] = por_texto.get(texto_id, 0) + 1
    fonte.registrar_indexados(colecao, [
        (c, por_texto.get(c, 0), None if por_texto.get(c) else "sem_texto") for c in textos_parte
    ])
    return len(pontos)


def importar(pasta: Path, lote: int, db_path: str) -> None:
    saida = pasta / "saida"
    manifesto_path = saida / "manifesto.json" if (saida / "manifesto.json").exists() else pasta / "entrada" / "manifesto.json"
    if not manifesto_path.exists():
        sys.exit(f"[importar] manifesto não encontrado em {pasta}/ — rode exportar.py e traga a pasta saida/")
    m = json.loads(manifesto_path.read_text(encoding="utf-8"))
    colecao = m["colecao"]
    if m["modelo"] != EMBEDDING_MODEL_V2:
        print(f"[importar] AVISO: pacote gerado com {m['modelo']}, config atual usa {EMBEDDING_MODEL_V2} — "
              "a busca precisa usar o mesmo modelo da coleção")
    if m["modo"] == "completo" and trechos.modo_local():
        print("[importar] AVISO: Qdrant local não aguenta a base inteira — defina QDRANT_URL=http://localhost:6333")

    partes = sorted(saida.glob("parte-*.npz")) if saida.exists() else []
    partes = [p for p in partes if ".tmp" not in p.name]
    print(f"[importar] coleção={colecao} · {len(partes)}/{m['n_partes']} partes em {saida}/")
    if not (saida / "CONCLUIDO").exists():
        print("[importar] AVISO: saida/CONCLUIDO não existe — a geração não terminou; importando só as partes prontas")

    n_ign = _registrar_ignorados(pasta, colecao)
    if n_ign:
        print(f"[importar] {n_ign} textos excluídos/sem texto registrados")

    client = trechos.obter_client()
    feitos = fonte.ja_indexados(colecao)
    cronometro = trechos.Cronometro(len(partes))
    for caminho in partes:
        n = importar_parte(client, caminho, colecao, lote, db_path, feitos)
        if n is None:
            cronometro.avancar(1)
            continue
        print(f"[importar] {caminho.name}: {n} trechos · partes {cronometro.avancar(1)}", flush=True)

    print(f"[importar] concluído: {fonte.resumo_indexacao(colecao)}")
    if (saida / "CONCLUIDO").exists():
        print(f"[importar] tudo importado — pode apagar {pasta}/ (tem o texto dos textos) antes da próxima exportação")


def main() -> None:
    parser = argparse.ArgumentParser(description="Importa os vetores gerados na GPU alugada pro Qdrant")
    parser.add_argument("--pasta", type=Path, default=PASTA_PADRAO)
    parser.add_argument("--lote", type=int, default=512, help="pontos por upsert")
    parser.add_argument("--db-path", default=DB_PATH)
    args = parser.parse_args()
    try:
        importar(args.pasta, args.lote, args.db_path)
    finally:
        trechos.fechar_client()


if __name__ == "__main__":
    main()
