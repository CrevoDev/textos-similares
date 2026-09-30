"""Enfileira as partes do pacote exportado na fila `busca_v2` do CrevoMQ
(ver busca_v2/fila.py), pra rodar aqui mesmo em vez da GPU alugada.

    uv run python busca_v2/enfileirar.py --partes 3   # amostra: só as 3 primeiras
    uv run python busca_v2/enfileirar.py              # todas
    uv run python busca_v2/enfileirar.py --textos 500 # um job por texto (fila busca_v2_contos)

Idempotente: parte já enfileirada é ignorada. Também registra os textos que
o exportar.py deixou de fora (excluídos/sem texto), como o importar.py."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pipeline  # noqa: E402
from busca_v2.exportar import PASTA_PADRAO  # noqa: E402
from busca_v2.importar import _registrar_ignorados  # noqa: E402


def _enfileirar_textos(pasta: Path, m: dict, n: int) -> None:
    """Os N textos do FIM do pacote — as primeiras partes podem estar na fila
    busca_v2, e o mesmo texto nas duas só seria indexado duas vezes."""
    import gzip
    from collections import deque

    with gzip.open(pasta / "entrada" / "contos.jsonl.gz", "rt", encoding="utf-8") as f:
        ids = deque(((c["id"], len(c["texto"])) for c in map(json.loads, f)), maxlen=n)
    pipeline.registrar()
    for texto_id, tamanho in ids:
        pipeline.enfileirar_busca_v2_texto(texto_id, m["colecao"], tamanho)
    print(f"[enfileirar] {len(ids)} textos na fila busca_v2_contos · coleção {m['colecao']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Enfileira o pacote da busca v2 no CrevoMQ")
    parser.add_argument("--pasta", type=Path, default=PASTA_PADRAO)
    parser.add_argument("--partes", type=int, default=None, help="só as N primeiras (amostra)")
    parser.add_argument("--textos", type=int, default=None,
                        help="enfileira N textos um a um na fila busca_v2_contos, em vez das partes")
    args = parser.parse_args()

    manifesto = args.pasta / "entrada" / "manifesto.json"
    if not manifesto.exists():
        sys.exit(f"[enfileirar] {manifesto} não existe — rode busca_v2/exportar.py antes")
    m = json.loads(manifesto.read_text(encoding="utf-8"))
    if args.textos:
        _enfileirar_textos(args.pasta, m, args.textos)
        return
    n = min(args.partes or m["n_partes"], m["n_partes"])
    pipeline.registrar()
    for parte in range(n):
        pipeline.enfileirar_busca_v2(str(args.pasta.resolve()), parte)
    n_ign = _registrar_ignorados(args.pasta, m["colecao"])
    print(f"[enfileirar] {n}/{m['n_partes']} partes na fila busca_v2 · {n_ign} ignorados registrados · "
          f"coleção {m['colecao']}")


if __name__ == "__main__":
    main()
