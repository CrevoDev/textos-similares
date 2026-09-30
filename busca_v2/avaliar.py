"""Compara v1 x v2 (x v2 + reranker) sobre os mesmos textos indexados.

    DB_PATH=data/contos.db uv run python busca_v2/avaliar.py                 # consultas automáticas
    DB_PATH=data/contos.db uv run python busca_v2/avaliar.py --consultas minhas.json --sem-rerank

Dois tipos de consulta:

1. Automáticas ("lembro de um detalhe"): pra cada texto sorteado, 8 palavras
   distintivas (maior IDF na amostra, sem nomes próprios) de um trecho da
   segunda metade do texto, bem depois dos 1.500 caracteres que a v1 indexa.
   O alvo é o próprio texto. Mede se a busca acha uma história por algo que
   acontece no meio dela — é exatamente a limitação da v1, então a v2 tende
   a sair bem na frente; não confunda isso com qualidade geral.
2. Manuais (`--consultas`): JSON com [{"consulta": "...", "relevantes": ["slug", ...]}].
   É a medida que importa de verdade; consultas cujos relevantes não estejam
   no conjunto indexado são ignoradas (e contadas no relatório).

Métricas: acerto@1, acerto@10 (algum relevante no top-k) e MRR@50."""

import argparse
import json
import math
import random
import re
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import BUSCA_V2_MODO, DB_PATH  # noqa: E402
from busca_v2 import buscar, fonte, trechos  # noqa: E402

PROFUNDIDADE = 50
PALAVRAS_POR_CONSULTA = 8
MIN_CHARS = 4000  # sem isso o "meio do texto" ainda cairia dentro do trecho da v1

STOPWORDS = set("""
a o e é de do da dos das em no na nos nas um uma uns umas que se por para pra com sem sua seu suas seus
ele ela eles elas eu tu você vocês nós me te lhe nos vos mim ti meu minha meus minhas teu tua isso isto
aquilo esse essa este esta aquele aquela mais menos muito muita muitos muitas mas ou como quando onde
então já ainda também só porque pois qual quais quem até sobre entre depois antes agora aqui ali lá
estava estavam estar está estão era eram foi foram ser sido tinha tinham tem têm ter havia fazer fez
disse falou olhou ficou vez coisa tudo nada todo toda todos todas outro outra outros outras mesmo mesma
""".split())


def _palavras(texto: str) -> list[str]:
    # \b dos dois lados: sem isso "Mariana" casaria "ariana"
    return re.findall(r"\b[a-zà-ÿ]{5,}\b", texto)


def _consultas_automaticas(docs: list[dict], n: int, semente: int) -> list[dict]:
    rng = random.Random(semente)
    df = Counter()
    for c in docs:
        df.update(set(_palavras((c["texto_completo"] or "").lower())))
    total = len(docs)

    elegiveis = [c for c in docs if len(c["texto_completo"] or "") >= MIN_CHARS]
    consultas = []
    for c in rng.sample(elegiveis, min(n, len(elegiveis))):
        texto = c["texto_completo"]
        ini = rng.randint(int(len(texto) * 0.45), int(len(texto) * 0.8))
        # descarta as pontas: a janela quase sempre começa/termina no meio de uma palavra
        janela = texto[ini : ini + 900].split(" ", 1)[-1].rsplit(" ", 1)[0]
        # só palavras que aparecem minúsculas na janela: corta nomes próprios,
        # que tornariam a consulta trivial (e ninguém lembra do nome do personagem)
        # e sem onomatopeias/alongamentos ("gozaaa"), que têm IDF alto mas ninguém digita
        candidatas = {p for p in _palavras(janela) if not re.search(r"(.)\1\1", p)} - STOPWORDS
        titulo = set(_palavras((c["titulo"] or "").lower()))
        candidatas -= titulo
        ordenadas = sorted(candidatas, key=lambda p: (-math.log(total / (1 + df[p])), p))
        escolhidas = [p for p in _palavras(janela) if p in set(ordenadas[:PALAVRAS_POR_CONSULTA])]
        escolhidas = list(dict.fromkeys(escolhidas))
        if len(escolhidas) >= 4:
            consultas.append({"consulta": " ".join(escolhidas), "relevantes": [c["id"]]})
    return consultas


def _metricas(posicoes: list[int | None]) -> dict:
    n = len(posicoes) or 1
    return {
        "acerto@1": sum(1 for p in posicoes if p is not None and p < 1) / n,
        "acerto@10": sum(1 for p in posicoes if p is not None and p < 10) / n,
        "mrr@50": sum(1 / (p + 1) for p in posicoes if p is not None) / n,
    }


def _posicao(ranking: list[str], relevantes: set[str]) -> int | None:
    return next((i for i, r in enumerate(ranking) if r in relevantes), None)


def avaliar(consultas: list[dict], modo: str, ids: list[str], rerank: bool, db_path: str) -> dict:
    sistemas = {"v1": [], "v2": []}
    if rerank:
        sistemas["v2+rerank"] = []
    tempos = {k: 0.0 for k in sistemas}
    for i, q in enumerate(consultas, 1):
        relevantes = set(q["relevantes"])
        t = time.time()
        v1 = [r for r, _ in buscar.buscar_v1(q["consulta"], ids, limite=PROFUNDIDADE, db_path=db_path)]
        tempos["v1"] += time.time() - t
        t = time.time()
        v2 = [r["id"] for r in buscar.buscar(q["consulta"], limite=PROFUNDIDADE, modo=modo, db_path=db_path)]
        tempos["v2"] += time.time() - t
        sistemas["v1"].append(_posicao(v1, relevantes))
        sistemas["v2"].append(_posicao(v2, relevantes))
        if rerank:
            t = time.time()
            rr = [r["id"] for r in buscar.buscar(q["consulta"], limite=PROFUNDIDADE, modo=modo, rerank=True,
                                                 candidatos=30, db_path=db_path)]
            tempos["v2+rerank"] += time.time() - t
            sistemas["v2+rerank"].append(_posicao(rr, relevantes))
        if i % 10 == 0:
            print(f"  {i}/{len(consultas)} consultas", flush=True)
    n = len(consultas) or 1
    return {k: {**_metricas(v), "ms/consulta": 1000 * tempos[k] / n} for k, v in sistemas.items()}


def _imprimir(titulo: str, resultado: dict, n: int) -> None:
    print(f"\n{titulo} ({n} consultas)")
    print(f"  {'sistema':<11} {'acerto@1':>9} {'acerto@10':>10} {'mrr@50':>8} {'ms/consulta':>12}")
    for sistema, m in resultado.items():
        print(f"  {sistema:<11} {m['acerto@1']:>9.1%} {m['acerto@10']:>10.1%} {m['mrr@50']:>8.3f} {m['ms/consulta']:>12.0f}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Avaliação v1 x v2")
    parser.add_argument("--modo", default=BUSCA_V2_MODO, choices=fonte.MODOS)
    parser.add_argument("-n", type=int, default=100, help="nº de consultas automáticas (0 = nenhuma)")
    parser.add_argument("--consultas", help="JSON com consultas manuais")
    parser.add_argument("--sem-rerank", action="store_true")
    parser.add_argument("--semente", type=int, default=42)
    parser.add_argument("--mostrar", type=int, default=5, help="imprime N consultas automáticas de exemplo")
    parser.add_argument("--db-path", default=DB_PATH)
    args = parser.parse_args()

    ids = fonte.indexados(trechos.nome_colecao(args.modo))
    if not ids:
        sys.exit("[v2] nada indexado nesse modo ainda — rode busca_v2/indexar.py primeiro")
    print(f"[v2] conjunto avaliado: {len(ids)} textos indexados ({args.modo})")
    rerank = not args.sem_rerank
    try:
        if args.n:
            consultas = _consultas_automaticas(fonte.carregar(ids, db_path=args.db_path), args.n, args.semente)
            for q in consultas[: args.mostrar]:
                print(f"  ex.: {q['consulta']!r} -> {q['relevantes'][0]}")
            _imprimir("Automáticas (detalhe do meio do texto)",
                      avaliar(consultas, args.modo, ids, rerank, args.db_path), len(consultas))
        if args.consultas:
            todas = json.loads(Path(args.consultas).read_text(encoding="utf-8"))
            conjunto = set(ids)
            validas = [q for q in todas if conjunto & set(q["relevantes"])]
            if len(validas) < len(todas):
                print(f"\n[v2] {len(todas) - len(validas)} consultas manuais ignoradas (relevantes fora do conjunto)")
            if validas:
                _imprimir("Manuais", avaliar(validas, args.modo, ids, rerank, args.db_path), len(validas))
    finally:
        trechos.fechar_client()


if __name__ == "__main__":
    main()
