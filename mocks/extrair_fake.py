"""Extração fake: em vez de HTTP real, lê os HTMLs salvos em fixtures/ e
reaplica o parser real (scraper/parse.py), simulando erro em uma fração dos
itens para testar retry/backoff sem depender do site."""

import random
from pathlib import Path

from scraper.parse import parse_pagina_texto

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
FRACAO_ERRO = 0.15


def _fixtures_html() -> list[Path]:
    return sorted(FIXTURES_DIR.glob("texto_*.html"))


def extrair_fake(job: dict, rng: random.Random | None = None) -> dict:
    """Mesmo contrato do handler real (scraper.extrair.extrair): grava os
    campos e devolve o payload do próximo estágio; erro simulado vira retry."""
    from scraper.extrair import salvar, triar

    rng = rng or random.Random()
    fixtures = _fixtures_html()
    if not fixtures:
        raise RuntimeError(f"Nenhum fixture HTML encontrado em {FIXTURES_DIR}")
    if rng.random() < FRACAO_ERRO:
        raise RuntimeError("erro simulado (mock)")

    campos = parse_pagina_texto(rng.choice(fixtures).read_text(encoding="utf-8"))
    salvar(job["id"], campos)
    return triar(job, campos)
