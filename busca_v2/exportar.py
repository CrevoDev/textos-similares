"""Passo 1 de 3 da indexação na GPU alugada: exporta os textos pra um pacote
autocontido que roda lá (gerar_vetores.py) sem SQLite nem Qdrant.

    DB_PATH=data/contos.db uv run python busca_v2/exportar.py --limite 230     # amostra (teste)
    DB_PATH=data/contos.db uv run python busca_v2/exportar.py --modo completo  # só com a extração terminada

Gera `data/gpu_v2/entrada/` (é essa pasta que vai pra máquina alugada) com
textos.jsonl.gz, manifesto.json (modelo e parâmetros de divisão — a GPU usa
exatamente esses), gerar_vetores.py, nucleo.py e LEIAME.txt com os comandos.

Textos excluídos por tema e sem texto nunca entram no pacote: ficam listados
em `data/gpu_v2/ignorados.jsonl`, que não sai da sua máquina, e o importar.py
os registra no estado. Textos já indexados na coleção são pulados (a menos
que --todos), então dá pra exportar só o que falta."""

import argparse
import gzip
import json
import math
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import (  # noqa: E402
    BUSCA_V2_GPU_PASTA,
    BUSCA_V2_LIMITE,
    BUSCA_V2_MODO,
    DB_PATH,
    EMBEDDING_MODEL_V2,
    TRECHO_SOBREPOSICAO,
    TRECHO_TOKENS,
    TRECHOS_MAX_POR_TEXTO,
)
from busca_v2 import fonte, trechos  # noqa: E402
from busca_v2.tags import canonizar  # noqa: E402

PASTA_PADRAO = Path(BUSCA_V2_GPU_PASTA)
AQUI = Path(__file__).resolve().parent

LEIAME = """Indexação v2 na GPU alugada — {n_textos} textos, coleção {colecao}

Troque IP e PORTA pelos do "Connect" da instância (Vast.ai: botão >_ SSH).

1) Na SUA máquina, envie esta pasta:
   rsync -avP -e "ssh -p PORTA" data/gpu_v2/entrada/ root@IP:/workspace/entrada/

2) Na máquina ALUGADA:
   pip install sentence-transformers
   cd /workspace && nohup python entrada/gerar_vetores.py entrada saida > log.txt 2>&1 &
   tail -f log.txt          # Ctrl+C só sai do tail; o processo continua
   Se cair no meio, rode o mesmo comando de novo: as partes prontas são puladas.
   Termina quando aparecer "pronto em ..." (e o arquivo saida/CONCLUIDO).

3) Na SUA máquina, traga o resultado:
   rsync -avP -e "ssh -p PORTA" root@IP:/workspace/saida/ data/gpu_v2/saida/

4) Na SUA máquina, importe pro Qdrant:
   DB_PATH=data/contos.db uv run python busca_v2/importar.py

5) Na Vast.ai: DESTROY na instância (não Stop — parada ela continua cobrando
   disco e os textos continuam na máquina do host).
"""


def exportar(modo: str, limite: int, pasta: Path, todos: bool, textos_por_parte: int, db_path: str) -> None:
    entrada = pasta / "entrada"
    if pasta.exists() and any(pasta.iterdir()):
        sys.exit(f"[exportar] {pasta}/ já existe e não está vazia — importe/apague antes, ou use --pasta outra")

    ids = fonte.selecionar(modo, limite, db_path=db_path)
    colecao = trechos.nome_colecao(modo, EMBEDDING_MODEL_V2)
    if not todos:
        feitos = fonte.ja_indexados(colecao)
        ids = [i for i in ids if i not in feitos]
    print(f"[exportar] modo={modo} coleção={colecao} · {len(ids)} textos candidatos")
    if not ids:
        return

    entrada.mkdir(parents=True)
    progresso = pasta / "progresso.json"
    n_textos = 0
    ignorados = {"excluido": 0, "sem_texto": 0}
    with gzip.open(entrada / "contos.jsonl.gz", "wt", encoding="utf-8") as saida, \
            open(pasta / "ignorados.jsonl", "w", encoding="utf-8") as ign:
        for i in range(0, len(ids), 500):
            for c in fonte.carregar(ids[i : i + 500], db_path=db_path,
                                    campos="id, titulo, temas, trecho, texto_completo"):
                texto = c.get("texto_completo") or c.get("trecho") or ""
                motivo = "excluido" if fonte.excluido(c.get("temas")) else ("sem_texto" if not texto.strip() else None)
                if motivo:
                    ign.write(json.dumps({"id": c["id"], "motivo": motivo}) + "\n")
                    ignorados[motivo] += 1
                    continue
                saida.write(json.dumps({
                    "id": c["id"],
                    "titulo": c.get("titulo") or "",
                    "temas": canonizar(c.get("temas")),
                    "texto": texto,
                }, ensure_ascii=False) + "\n")
                n_textos += 1
            # lido pelo painel (busca_v2/painel.py) — a exportação completa leva minutos
            progresso.write_text(json.dumps({"feitos": min(i + 500, len(ids)), "total": len(ids),
                                             "atualizado_em": fonte.agora()}), encoding="utf-8")

    manifesto = {
        "versao": 1,
        "criado_em": fonte.agora(),
        "modo": modo,
        "colecao": colecao,
        "modelo": EMBEDDING_MODEL_V2,
        "trecho_tokens": TRECHO_TOKENS,
        "trecho_sobreposicao": TRECHO_SOBREPOSICAO,
        "trechos_max_por_conto": TRECHOS_MAX_POR_TEXTO,
        "contos_por_parte": textos_por_parte,
        "n_contos": n_textos,
        "n_partes": math.ceil(n_textos / textos_por_parte),
    }
    (entrada / "manifesto.json").write_text(json.dumps(manifesto, ensure_ascii=False, indent=2), encoding="utf-8")
    for arquivo in ("gerar_vetores.py", "nucleo.py"):
        shutil.copy2(AQUI / arquivo, entrada / arquivo)
    (entrada / "LEIAME.txt").write_text(LEIAME.format(**manifesto), encoding="utf-8")

    tamanho = sum(f.stat().st_size for f in entrada.iterdir()) / 1e6
    print(f"[exportar] {n_textos} textos em {manifesto['n_partes']} partes · ignorados={ignorados} · "
          f"{tamanho:.1f} MB em {entrada}/")
    print(f"[exportar] próximos passos em {entrada / 'LEIAME.txt'}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Exporta textos pra indexação na GPU alugada")
    parser.add_argument("--modo", default=BUSCA_V2_MODO, choices=fonte.MODOS)
    parser.add_argument("--limite", type=int, default=BUSCA_V2_LIMITE, help="só no modo amostra")
    parser.add_argument("--pasta", type=Path, default=PASTA_PADRAO)
    parser.add_argument("--todos", action="store_true", help="inclui textos já indexados na coleção")
    parser.add_argument("--textos-por-parte", type=int, default=1000)
    parser.add_argument("--db-path", default=DB_PATH)
    args = parser.parse_args()
    try:
        exportar(args.modo, args.limite, args.pasta, args.todos, args.textos_por_parte, args.db_path)
    except fonte.ExtracaoIncompleta as e:
        sys.exit(f"[exportar] {e}")


if __name__ == "__main__":
    main()
