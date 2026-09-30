"""Aplica a regra de descarte (descarte.py) aos textos que já estão no
catálogo. Sem argumentos só simula: conta quantos sairiam, por tag, quantos a
proteção lésbica salvou e mostra exemplos — nada é alterado.

    uv run python scripts/descartar.py              # simulação
    uv run python scripts/descartar.py --executar   # enfileira na fila `delecao`

A remoção em si é feita pelos workers do estágio `remocao` (supervisor ou
`--drenar`, que processa tudo aqui mesmo). Enfileirar de novo é seguro: o id
do job é derivado do slug do texto (ver pipeline.enfileirar_delecao)."""

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import descarte  # noqa: E402
import pipeline  # noqa: E402
from db import conectar  # noqa: E402


def _fmt(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--executar", action="store_true", help="enfileira os textos na fila de deleção")
    parser.add_argument("--drenar", action="store_true", help="com --executar: já remove aqui, sem esperar workers")
    parser.add_argument("--exemplos", type=int, default=15, help="quantos títulos de exemplo mostrar")
    parser.add_argument("--lote", type=int, default=500,
                        help="textos por job: o handler apaga cada lote numa transação só")
    args = parser.parse_args()

    total = 0
    descartar: list[tuple[str, str, str, str]] = []  # (id, titulo, tag, url)
    protegidos: Counter[str] = Counter()
    with conectar() as conn:
        for linha in conn.execute("SELECT id, url, titulo, temas FROM contos WHERE estagio = 'concluido'"):
            total += 1
            c = descarte.classificar(linha["temas"])
            if c is None:
                continue
            tipo, tag = c
            if tipo == "protegido":
                protegidos[tag] += 1
            else:
                descartar.append((linha["id"], linha["titulo"] or "(sem título)", tag, linha["url"]))

    por_tag = Counter(tag for _, _, tag, _ in descartar)
    print(f"Catálogo: {_fmt(total)} textos")
    print(f"Sairiam:  {_fmt(len(descartar))} ({len(descartar) / total:.1%})" if total else "Catálogo vazio")
    print(f"Ficariam: {_fmt(total - len(descartar))}")
    print(f"\nProtegidos por tag lésbica/feminina (casariam a regra, mas ficam): {_fmt(sum(protegidos.values()))}")
    for tag, n in protegidos.most_common(5):
        print(f"  {_fmt(n):>8}  {tag}")
    print("\nTag que derrubou cada texto (a primeira que casou):")
    for tag, n in por_tag.most_common(25):
        print(f"  {_fmt(n):>8}  {tag}")
    if len(por_tag) > 25:
        print(f"  ... e mais {len(por_tag) - 25} tags")
    if args.exemplos and descartar:
        print("\nExemplos:")
        passo = max(1, len(descartar) // args.exemplos)
        for texto_id, titulo, tag, _ in descartar[::passo][: args.exemplos]:
            print(f"  [{tag}] {titulo}  ({texto_id})")

    if not args.executar:
        print("\nSimulação: nada foi alterado. Rode com --executar para enfileirar a deleção.")
        return

    lotes = [descartar[i : i + args.lote] for i in range(0, len(descartar), args.lote)]
    for lote in lotes:
        pipeline.enfileirar_delecao_lote([(texto_id, url, tag) for texto_id, _, tag, url in lote])
    print(f"\n{_fmt(len(descartar))} textos enfileirados na fila '{pipeline.FILA_DELECAO}' "
          f"em {_fmt(len(lotes))} jobs de até {args.lote}.")
    if args.drenar:
        pipeline.drenar()


if __name__ == "__main__":
    main()
