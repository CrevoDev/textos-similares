"""Descoberta fake: gera URLs sintéticas em vez de baixar o sitemap real."""

from datetime import datetime, timezone

from config import DB_PATH
from scraper.descobrir import inserir_novos


def descobrir_fake(quantidade: int = 200, db_path: str = DB_PATH) -> int:
    """Insere `quantidade` textos sintéticos com origem='mock' e os enfileira
    na extração. Retorna quantos foram inseridos (já existentes são ignorados)."""
    agora = datetime.now(timezone.utc).isoformat()
    lote = [(f"mock-{i}", f"mock://texto/{i}", "mock", agora) for i in range(1, quantidade + 1)]
    return inserir_novos(lote, db_path=db_path)
