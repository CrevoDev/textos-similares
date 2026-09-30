"""Roda NA MÁQUINA ALUGADA (GPU): textos exportados -> trechos -> vetores.

    pip install sentence-transformers
    python entrada/gerar_vetores.py entrada saida

Autocontido: só precisa deste arquivo, de nucleo.py ao lado e da pasta
`entrada/` gerada por busca_v2/exportar.py (manifesto.json + textos.jsonl.gz).
Não usa SQLite, Qdrant nem o config.py do projeto — modelo e parâmetros de
divisão vêm do manifesto, então os trechos saem idênticos aos da indexação local.

Saída: uma `saida/parte-NNNNN.npz` por grupo de textos (tamanho definido no
manifesto), com os vetores em float16 e os offsets de cada trecho. Retomável:
parte que já existe é pulada, e cada uma é gravada num .tmp e renomeada no
fim, então uma queda no meio nunca deixa parte pela metade. Também roda em
CPU (mais devagar) — é assim que o fluxo é testado com a amostra."""

import argparse
import gzip
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402

import nucleo  # noqa: E402


def _partes(caminho: Path, tamanho: int):
    """(índice, textos) em grupos de `tamanho`, na ordem do arquivo — a mesma
    numeração que o importador espera."""
    grupo, indice = [], 0
    with gzip.open(caminho, "rt", encoding="utf-8") as f:
        for linha in f:
            grupo.append(json.loads(linha))
            if len(grupo) == tamanho:
                yield indice, grupo
                grupo, indice = [], indice + 1
    if grupo:
        yield indice, grupo


def _processar(modelo, docs: list[dict], m: dict, batch: int) -> dict:
    textos = [c["texto"] for c in docs]
    # tokenização em lote: o tokenizer "fast" paraleliza em Rust, e com a base
    # inteira isso é centenas de milhões de tokens — um por vez seria o gargalo
    offsets = modelo.tokenizer(textos, add_special_tokens=False, return_offsets_mapping=True)["offset_mapping"]
    texto_ids, ordens, inicios, fins, entradas = [], [], [], [], []
    for c, offs in zip(docs, offsets):
        for t in nucleo.montar(c["texto"], c["titulo"], c["temas"], offs, m["modelo"],
                               m["trecho_tokens"], m["trecho_sobreposicao"], m["trechos_max_por_conto"]):
            texto_ids.append(c["id"])
            ordens.append(t["ordem"])
            inicios.append(t["inicio"])
            fins.append(t["fim"])
            entradas.append(t["texto_embedding"])
    if entradas:
        vetores = modelo.encode(entradas, batch_size=batch, normalize_embeddings=True, show_progress_bar=False,
                                convert_to_numpy=True).astype(np.float16)
    else:
        vetores = np.zeros((0, modelo.get_sentence_embedding_dimension()), dtype=np.float16)
    return {
        "contos": np.array([c["id"] for c in docs]),  # todos da parte, inclusive os que não geraram trecho
        "conto_id": np.array(texto_ids),
        "ordem": np.array(ordens, dtype=np.int32),
        "inicio": np.array(inicios, dtype=np.int32),
        "fim": np.array(fins, dtype=np.int32),
        "vetores": vetores,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Gera os vetores dos trechos (roda na GPU alugada)")
    parser.add_argument("entrada", type=Path)
    parser.add_argument("saida", type=Path)
    parser.add_argument("--batch", type=int, default=256, help="trechos por lote na GPU")
    parser.add_argument("--dispositivo", default=None, help="cuda | cpu (padrão: cuda se houver)")
    args = parser.parse_args()

    m = json.loads((args.entrada / "manifesto.json").read_text(encoding="utf-8"))
    args.saida.mkdir(parents=True, exist_ok=True)
    (args.saida / "manifesto.json").write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")

    import torch
    from sentence_transformers import SentenceTransformer

    dispositivo = args.dispositivo or ("cuda" if torch.cuda.is_available() else "cpu")
    modelo = SentenceTransformer(m["modelo"], device=dispositivo)
    if dispositivo == "cuda":
        modelo.half()  # ~2x mais rápido; o vetor sai em float16 de qualquer jeito
    print(f"modelo={m['modelo']} dispositivo={dispositivo} textos={m['n_textos']} partes={m['n_partes']}", flush=True)

    t0, feitos, trechos = time.time(), 0, 0
    for indice, docs in _partes(args.entrada / "contos.jsonl.gz", m["contos_por_parte"]):
        destino = args.saida / f"parte-{indice:05d}.npz"
        feitos += len(docs)
        if destino.exists():
            continue
        dados = _processar(modelo, docs, m, args.batch)
        tmp = destino.with_suffix(".tmp.npz")
        np.savez(tmp, **dados)
        os.replace(tmp, destino)
        trechos += len(dados["conto_id"])
        decorrido = time.time() - t0
        taxa = trechos / decorrido if decorrido else 0
        print(f"parte {indice + 1}/{m['n_partes']} · {feitos}/{m['n_textos']} textos · "
              f"{trechos} trechos nesta rodada · {taxa:.0f} trechos/s", flush=True)
    (args.saida / "CONCLUIDO").write_text(f"{m['n_textos']} textos\n", encoding="utf-8")
    print(f"pronto em {(time.time() - t0) / 60:.1f} min — traga a pasta {args.saida}/ de volta e rode importar.py")


if __name__ == "__main__":
    main()
