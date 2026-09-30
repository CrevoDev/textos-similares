"""Detecta as séries do catálogo inteiro (series.py). Sem argumentos só
simula: quantas séries (do site e pelo título), quantos textos entram,
exemplos com as partes em ordem — nada é gravado.

    uv run python scripts/series.py                  # simulação
    uv run python scripts/series.py --autor "Carol 27"   # um autor, com todas as partes
    uv run python scripts/series.py --executar       # enfileira na fila `series`

O processamento é dos workers dos estágios `agrupar` e `vetorizar`
(supervisor ou `--drenar`). Os jobs são por lote de autores."""

import argparse
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pipeline  # noqa: E402
import series  # noqa: E402
from db import conectar  # noqa: E402


def _fmt(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def _mostrar(s: series.Serie, maximo: int = 8) -> None:
    print(f"  [{s.origem}] {s.nome}  —  {s.autor} · {len(s.partes)} partes")
    for i, c in enumerate(s.partes[:maximo], 1):
        t = c["_titulo"]
        marca = "".join([f"T{t.temporada} " if t.temporada else "",
                         f"#{'.'.join(map(str, t.numeros))} " if t.numeros else "",
                         "final " if t.final else ""]) or "—"
        print(f"      {i:>2}. {marca:<10} {c.get('data_pub', '')[:10]}  {c['titulo']}")
    if len(s.partes) > maximo:
        print(f"      ... e mais {len(s.partes) - maximo}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--executar", action="store_true", help="enfileira os autores na fila de séries")
    parser.add_argument("--drenar", action="store_true", help="com --executar: já processa aqui, sem esperar workers")
    parser.add_argument("--autor", help="simula um autor só, mostrando todas as partes")
    parser.add_argument("--exemplos", type=int, default=12)
    parser.add_argument("--lote", type=int, default=500, help="autores por job")
    parser.add_argument("--semente", type=int, default=1, help="sorteio dos exemplos")
    args = parser.parse_args()

    # Por chave de autor: grafias diferentes do mesmo nome ('Biel'/'BIEL',
    # '2H2O'/'2H20') são o mesmo autor pra efeito de séries.
    por_autor: dict[str, list[dict]] = defaultdict(list)
    with conectar() as conn:
        sql = ("SELECT id, titulo, autor, serie, data_pub FROM contos "
               "WHERE estagio = 'concluido' AND autor IS NOT NULL AND autor <> ''")
        for linha in conn.execute(sql):
            if not args.autor or series.chave_autor(linha["autor"]) == series.chave_autor(args.autor):
                por_autor[series.chave_autor(linha["autor"])].append(dict(linha))

    todas: list[series.Serie] = []
    for docs in por_autor.values():
        if len(docs) >= 2:
            todas.extend(series.agrupar(docs))

    if args.autor:
        for s in sorted(todas, key=lambda s: s.nome):
            _mostrar(s, maximo=999)
        print(f"\n{len(todas)} séries de {args.autor}")
        return

    total = sum(len(c) for c in por_autor.values())
    em_series = sum(len(s.partes) for s in todas)
    por_origem = Counter(s.origem for s in todas)
    completadas = sum(1 for s in todas if s.origem == "site"
                      and any(not c.get("serie") for c in s.partes))
    tamanhos = Counter(min(len(s.partes), 10) for s in todas)
    varias_grafias = sum(1 for s in todas if len({c["autor"] for c in s.partes}) > 1)
    print(f"Catálogo: {_fmt(total)} textos de {_fmt(len(por_autor))} autores")
    print(f"Séries com partes em grafias diferentes do autor: {_fmt(varias_grafias)}")
    print(f"Séries:   {_fmt(len(todas))}  (site: {_fmt(por_origem['site'])}, "
          f"pelo título: {_fmt(por_origem['titulo'])}; séries do site completadas pelo título: {_fmt(completadas)})")
    print(f"Textos em séries: {_fmt(em_series)} ({em_series / total:.1%})")
    print("Partes por série: " + ", ".join(f"{k if k < 10 else '10+'}: {_fmt(v)}" for k, v in sorted(tamanhos.items())))
    print("\nMaiores:")
    for s in sorted(todas, key=lambda s: -len(s.partes))[:8]:
        print(f"  {len(s.partes):>4}  {s.nome}  —  {s.autor} [{s.origem}]")

    rng = random.Random(args.semente)
    detectadas = [s for s in todas if s.origem == "titulo"]
    if args.exemplos and detectadas:
        print("\nExemplos detectados pelo título:")
        for s in rng.sample(detectadas, min(args.exemplos, len(detectadas))):
            _mostrar(s)
    completas = [s for s in todas if s.origem == "site" and any(not c.get("serie") for c in s.partes)]
    if args.exemplos and completas:
        print("\nSéries do site completadas pelo título:")
        for s in rng.sample(completas, min(args.exemplos // 3 or 1, len(completas))):
            _mostrar(s)

    if not args.executar:
        print("\nSimulação: nada foi gravado. Rode com --executar para processar.")
        return

    # Todo autor com 2+ textos (todas as grafias): também zera séries antigas
    # que deixaram de existir.
    autores = sorted({c["autor"] for docs in por_autor.values() if len(docs) >= 2 for c in docs})
    for i in range(0, len(autores), args.lote):
        pipeline.enfileirar_series(autores[i:i + args.lote])
    print(f"\n{_fmt(len(autores))} autores enfileirados na fila '{pipeline.FILA_SERIES}' "
          f"em {_fmt(-(-len(autores) // args.lote))} jobs.")
    if args.drenar:
        pipeline.drenar()


if __name__ == "__main__":
    main()
