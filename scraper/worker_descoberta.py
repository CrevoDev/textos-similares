"""Worker da Fila 1 — Descoberta. Diferente das outras filas, não consome um
estágio anterior: é um produtor que varre o sitemap e insere URLs novas.

Por padrão roda uma vez e sai; as outras filas continuam consumindo o que já
foi inserido enquanto este processo não está rodando. Para atualização
periódica (Fase 7 de manutenção) há duas formas de usar este mesmo script,
sem precisar de scheduler externo (mesmo raciocínio do plan.md §1 contra
infraestrutura de fila pesada — aqui também, o mais simples resolve):

1. Agendado pelo cron do sistema (ex. diário), rodando uma vez e saindo:
   0 4 * * * cd /home/projects/scrap-project && uv run python scraper/worker_descoberta.py --meses 1

2. Como processo contínuo em background, igual aos outros 3 workers, usando
   --loop (dorme --intervalo-horas entre execuções):
   uv run python scraper/worker_descoberta.py --meses 1 --loop --intervalo-horas 24

Em ambos os casos um recorte pequeno (--meses 1, por exemplo) já é suficiente
pra pegar contos novos, já que o sitemap é percorrido do mais recente pro
mais antigo e descobrir() usa INSERT OR IGNORE — rodar de novo sobre URLs já
vistas é seguro e não duplica nada."""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import DB_PATH  # noqa: E402
from scraper.descobrir import descobrir  # noqa: E402


def rodar_uma_vez(meses: int | None, limite_urls: int | None, db_path: str) -> dict:
    recorte = f"últimos {meses} meses" if meses is not None else "sitemap inteiro, sem limite de data"
    print(f"[descoberta] percorrendo sitemap ({recorte})...")
    r = descobrir(meses=meses, limite_urls=limite_urls, db_path=db_path)
    print(f"[descoberta] vistos={r['vistos']} inseridos={r['inseridos']}")
    return r


def main() -> None:
    parser = argparse.ArgumentParser(description="Worker de descoberta (Fila 1)")
    parser.add_argument(
        "--meses", type=int, default=12,
        help="recorte de meses para trás; 0 (ou negativo) = sitemap inteiro, sem limite de data "
        "(usado pra coleta da base completa, ~250 mil textos)",
    )
    parser.add_argument("--limite-urls", type=int, default=None, help="teto de URLs novas")
    parser.add_argument("--db-path", default=DB_PATH)
    parser.add_argument(
        "--loop",
        action="store_true",
        help="roda em loop contínuo (atualização periódica) em vez de uma vez só",
    )
    parser.add_argument(
        "--intervalo-horas",
        type=float,
        default=24.0,
        help="intervalo entre execuções no modo --loop (default: 24h)",
    )
    args = parser.parse_args()
    meses = args.meses if args.meses > 0 else None
    # NÃO chama iniciar_schema() aqui — rode `scripts/init_schema.py` antes
    # (é o que o serviço `init` do docker-compose.yml já faz).

    if not args.loop:
        rodar_uma_vez(meses, args.limite_urls, args.db_path)
        return

    print(f"[descoberta] modo loop: rodando a cada {args.intervalo_horas}h (Ctrl+C para parar)...")
    try:
        while True:
            rodar_uma_vez(meses, args.limite_urls, args.db_path)
            print(f"[descoberta] dormindo {args.intervalo_horas}h até a próxima rodada...")
            time.sleep(args.intervalo_horas * 3600)
    except KeyboardInterrupt:
        print("[descoberta] encerrando...")


if __name__ == "__main__":
    main()
