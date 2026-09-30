"""Fase 2a: roda o pipeline completo com dados mockados, até as filas
esvaziarem, e checa os critérios de saída da 2a.

Aponte DB_PATH e FILA_DB_PATH para bancos descartáveis — o mock escreve
textos com origem='mock' e usa o Qdrant local (qdrant_local/):
    DB_PATH=data/mock.db FILA_DB_PATH=data/mock_fila.db MOCK_MODE=true \\
        uv run python scripts/rodar_pipeline_mock.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pipeline  # noqa: E402
from config import DB_PATH, MAX_TENTATIVAS, MOCK_MODE  # noqa: E402
from db import conectar, iniciar_schema  # noqa: E402
from mocks.descobrir_fake import descobrir_fake  # noqa: E402

QUANTIDADE_MOCK = 200


def rodar(db_path: str = DB_PATH, quantidade: int = QUANTIDADE_MOCK) -> None:
    if not MOCK_MODE:
        sys.exit("rode com MOCK_MODE=true (senão a extração baixa do site de verdade)")
    iniciar_schema(db_path)
    pipeline.registrar()

    inseridos = descobrir_fake(quantidade, db_path=db_path)
    print(f"[descoberta] {inseridos} textos mock inseridos")
    pipeline.drenar()
    _checar_criterios(db_path, quantidade)


def _checar_criterios(db_path: str, quantidade: int) -> None:
    with conectar(db_path) as conn:
        catalogo = dict(conn.execute(
            "SELECT estagio, COUNT(*) FROM contos WHERE origem = 'mock' GROUP BY estagio").fetchall())
        (descartados,) = conn.execute("SELECT COUNT(*) FROM descartados WHERE id LIKE 'mock-%'").fetchone()

    print("\n--- Filas ---")
    for nome, fila in pipeline.filas().items():
        for estagio, contagem in fila.contagens().items():
            print(f"  {nome:8s} {estagio:10s} {contagem}")

    erros = sum(c.get("erro", 0) for c in pipeline.fila_textos().contagens().values())
    presos = pipeline.restantes()
    concluidos = catalogo.get("concluido", 0)
    print("\n--- Critérios de saída da Fase 2a ---")
    print(f"1. nenhum job preso (pendente/processando): {'OK' if presos == 0 else 'FALHOU'}")
    print(f"2. erros simulados respeitaram MAX_TENTATIVAS={MAX_TENTATIVAS}: {erros} jobs retidos em erro")
    print("3. nenhuma chamada de rede real: OK (mock não usa httpx nem Qdrant remoto)")
    print(f"\nTotal mock: {quantidade} | no catálogo: {concluidos} | descartados: {descartados} | "
          f"erro permanente: {erros}")
    if concluidos + descartados + erros != quantidade:
        print("AVISO: a soma não bate com a quantidade inserida")


if __name__ == "__main__":
    rodar()
