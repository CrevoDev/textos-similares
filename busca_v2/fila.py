"""Busca v2 pelo CrevoMQ: o mesmo pacote do exportar.py, processado aqui
(CPU ou GPU local) em vez da máquina alugada.

Fila `busca_v2`: vetores -> importacao. Um job por parte do pacote
(`data/gpu_v2/entrada/`, 1.000 textos por parte no padrão). `vetores` grava
`saida/parte-NNNNN.npz` exatamente como o gerar_vetores.py; `importacao` faz
o upsert dessa parte no Qdrant (importar.importar_parte). Enfileire com
busca_v2/enfileirar.py."""

import json
import os
from pathlib import Path

import numpy as np
from crevomq import ErroPermanente

from config import DB_PATH
from busca_v2 import gerar_vetores

_modelo = None


def _obter_modelo(nome: str):
    global _modelo
    if _modelo is None:
        import torch
        from sentence_transformers import SentenceTransformer

        dispositivo = "cuda" if torch.cuda.is_available() else "cpu"
        _modelo = SentenceTransformer(nome, device=dispositivo)
        if dispositivo == "cuda":
            _modelo.half()
    return _modelo


def _manifesto(pasta: Path) -> dict:
    caminho = pasta / "entrada" / "manifesto.json"
    if not caminho.exists():
        raise ErroPermanente(f"pacote não encontrado: {caminho}")
    return json.loads(caminho.read_text(encoding="utf-8"))


def _destino(pasta: Path, parte: int) -> Path:
    return pasta / "saida" / f"parte-{parte:05d}.npz"


def vetores(job: dict) -> dict:
    """Estágio `vetores`: gera a parte do payload, se ainda não existe."""
    pasta, parte = Path(job["payload"]["pasta"]), job["payload"]["parte"]
    m = _manifesto(pasta)
    destino = _destino(pasta, parte)
    if destino.exists():
        return job["payload"]
    for indice, docs in gerar_vetores._partes(pasta / "entrada" / "contos.jsonl.gz", m["contos_por_parte"]):
        if indice == parte:
            break
    else:
        raise ErroPermanente(f"parte {parte} não existe no pacote ({m['n_partes']} partes)")
    dados = gerar_vetores._processar(_obter_modelo(m["modelo"]), docs, m, batch=64)
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_suffix(".tmp.npz")
    np.savez(tmp, **dados)
    os.replace(tmp, destino)
    return {**job["payload"], "trechos": len(dados["conto_id"])}


def importacao(job: dict, db_path: str = DB_PATH) -> dict:
    """Estágio `importacao`: upsert da parte na coleção do manifesto."""
    from busca_v2 import importar, trechos

    pasta, parte = Path(job["payload"]["pasta"]), job["payload"]["parte"]
    m = _manifesto(pasta)
    caminho = _destino(pasta, parte)
    if not caminho.exists():
        raise ErroPermanente(f"{caminho} não existe — o estágio vetores não rodou")
    importar.importar_parte(trechos.obter_client(), caminho, m["colecao"], 512, db_path)
    return job["payload"]


def doc(job: dict, db_path: str = DB_PATH) -> dict:
    """Fila `busca_v2_contos`, estágio `indexacao`: um texto direto do
    contos.db -> trechos -> Qdrant, como o indexar.py. Existe pra medir o
    ritmo texto a texto no painel."""
    from busca_v2 import fonte, indexar, trechos

    docs = fonte.carregar([job["payload"]["conto_id"]], db_path=db_path)
    if not docs:
        raise ErroPermanente("texto não está mais no banco")
    n = indexar.indexar_textos(trechos.obter_client(), job["payload"]["colecao"], docs)
    return {**job["payload"], "trechos": n}
