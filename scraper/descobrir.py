"""Descoberta: percorre o sitemap, insere URLs novas de textos
(estagio='descoberta') e enfileira cada uma na extração (fila `textos` do
CrevoMQ — ver pipeline.py). Os sitemaps vêm ordenados do mais recente para o
mais antigo (tanto o índice quanto as URLs dentro de cada arquivo), então dá
para parar assim que passar do recorte de datas desejado."""

import gzip
import re
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree

import httpx
import tenacity

import pipeline
from config import DB_PATH, REQUEST_INTERVAL_SECONDS, SITEMAP_INDEX_URL, USER_AGENT
from db import conectar

NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
SLUG_RE = re.compile(r"/texto/([^/]+)/?$")


def _cliente() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30.0, follow_redirects=True)


@tenacity.retry(
    retry=tenacity.retry_if_exception_type((httpx.HTTPStatusError, httpx.TransportError)),
    wait=tenacity.wait_exponential(multiplier=1, min=2, max=60),
    stop=tenacity.stop_after_attempt(5),
    reraise=True,
)
def _baixar(client: httpx.Client, url: str) -> bytes:
    resp = client.get(url)
    if resp.status_code == 429 or resp.status_code >= 500:
        resp.raise_for_status()
    resp.raise_for_status()
    return resp.content


def _listar_sitemaps(client: httpx.Client) -> list[str]:
    conteudo = gzip.decompress(_baixar(client, SITEMAP_INDEX_URL))
    raiz = ElementTree.fromstring(conteudo)
    return [no.text for no in raiz.findall(".//sm:sitemap/sm:loc", NS)]


def _urls_do_sitemap(client: httpx.Client, sitemap_url: str) -> list[tuple[str, str]]:
    """Retorna [(url, lastmod), ...] de um arquivo sitemap.N.xml.gz."""
    conteudo = gzip.decompress(_baixar(client, sitemap_url))
    raiz = ElementTree.fromstring(conteudo)
    resultado = []
    for no in raiz.findall(".//sm:url", NS):
        loc = no.find("sm:loc", NS)
        lastmod = no.find("sm:lastmod", NS)
        if loc is not None and loc.text:
            resultado.append((loc.text, lastmod.text if lastmod is not None else ""))
    return resultado


def _slug(url: str) -> str | None:
    match = SLUG_RE.search(url)
    return match.group(1) if match else None


def inserir_novos(lote: list[tuple[str, str, str, str]], db_path: str = DB_PATH) -> int:
    """Insere (slug, url, origem, coletado_em) que ainda não estão no banco
    nem em `descartados`, e enfileira cada um na extração. Retorna quantos
    eram novos."""
    conhecidos: set[str] = set()
    with conectar(db_path) as conn:
        # Em partes: um sitemap tem milhares de URLs e o SQLite limita os
        # parâmetros por consulta.
        for i in range(0, len(lote), 500):
            slugs = [linha[0] for linha in lote[i : i + 500]]
            marcadores = ",".join("?" * len(slugs))
            conhecidos.update(
                linha[0] for linha in conn.execute(
                    f"SELECT id FROM contos WHERE id IN ({marcadores}) "
                    f"UNION SELECT id FROM descartados WHERE id IN ({marcadores})",
                    slugs + slugs,
                )
            )
        novos = [linha for linha in lote if linha[0] not in conhecidos]
        conn.executemany(
            "INSERT OR IGNORE INTO contos (id, url, origem, coletado_em, estagio, status) "
            "VALUES (?, ?, ?, ?, 'descoberta', 'pendente')",
            novos,
        )
    # Depois do commit: o worker de extração não pode pegar um job cujo texto
    # ainda não está gravado.
    for slug, url, _, _ in novos:
        pipeline.enfileirar_texto(slug, url)
    return len(novos)


def descobrir(
    meses: int | None = 12,
    limite_urls: int | None = None,
    db_path: str = DB_PATH,
    intervalo: float = REQUEST_INTERVAL_SECONDS,
) -> dict:
    """Percorre o sitemap (mais recente -> mais antigo) e insere URLs novas.
    `meses`: recorte de quantos meses para trás considerar (None = sem limite,
    percorre o sitemap inteiro). `limite_urls`: teto de URLs novas a inserir
    (None = sem limite)."""
    cutoff = None
    if meses is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=30 * meses)

    inseridos = 0
    vistos = 0
    agora = datetime.now(timezone.utc).isoformat()

    with _cliente() as client:
        sitemaps = _listar_sitemaps(client)
        for sitemap_url in sitemaps:
            import time

            time.sleep(intervalo)
            urls = _urls_do_sitemap(client, sitemap_url)

            parar = False
            lote = []
            for url, lastmod in urls:
                vistos += 1
                if cutoff is not None and lastmod:
                    try:
                        data = datetime.fromisoformat(lastmod).replace(tzinfo=timezone.utc)
                    except ValueError:
                        data = None
                    if data is not None and data < cutoff:
                        parar = True
                        break

                slug = _slug(url)
                if not slug:
                    continue
                lote.append((slug, url, "real", agora))

                if limite_urls is not None and inseridos + len(lote) >= limite_urls:
                    parar = True
                    break

            if lote:
                inseridos += inserir_novos(lote, db_path=db_path)

            if parar:
                break

    return {"vistos": vistos, "inseridos": inseridos}


if __name__ == "__main__":
    resultado = descobrir()
    print(resultado)
