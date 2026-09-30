"""Rodada única contra o site/serviços reais: descobre e drena as filas no
próprio processo, um estágio de cada vez, até não sobrar nada. Para rodar
continuamente com workers em paralelo, use o supervisor (supervisor.toml)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pipeline  # noqa: E402
from config import DB_PATH  # noqa: E402
from db import iniciar_schema  # noqa: E402
from scraper.descobrir import descobrir  # noqa: E402


def rodar(meses: int | None = 12, limite_urls: int | None = None, db_path: str = DB_PATH) -> None:
    iniciar_schema(db_path)
    pipeline.registrar()
    print(f"[descoberta] percorrendo sitemap (recorte: últimos {meses} meses)...")
    r = descobrir(meses=meses, limite_urls=limite_urls, db_path=db_path)
    print(f"[descoberta] vistos={r['vistos']} inseridos={r['inseridos']}")
    pipeline.drenar()
    print("Nada mais pendente. Pipeline estabilizado.")


if __name__ == "__main__":
    rodar()
