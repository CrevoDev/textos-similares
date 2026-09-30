"""Fila `textos`, estágio `extracao`: baixa a página do texto e extrai os
campos com o parser mapeado na Fase 1 (scraper/parse.py). Uma requisição por
vez em cada worker, com intervalo de cortesia entre elas.

Retry e backoff ficam com o CrevoMQ: 4xx vira ErroPermanente (não adianta
tentar de novo — página removida depois que o sitemap foi gerado); 429/5xx e
falha de rede sobem como erro comum, e a fila espera mais a cada tentativa
(429 espera 4x mais, e o Retry-After do site é respeitado).

Texto que a regra de descarte (descarte.py) recusa não segue para
embedding: vai para a fila `delecao` e atravessa os estágios seguintes sem
fazer nada."""

import time
from datetime import datetime, timezone

import httpx
from crevomq import ErroPermanente

import descarte
import pipeline
from config import DB_PATH, MOCK_MODE, REQUEST_INTERVAL_SECONDS, USER_AGENT
from db import conectar
from scraper.parse import parse_pagina_texto

_client: httpx.Client | None = None
_ultima_requisicao = 0.0


def _cliente() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30.0, follow_redirects=True)
    return _client


def _baixar(url: str, intervalo: float = REQUEST_INTERVAL_SECONDS) -> str:
    global _ultima_requisicao
    espera = intervalo - (time.monotonic() - _ultima_requisicao)
    if espera > 0:
        time.sleep(espera)
    try:
        resp = _cliente().get(url)
    finally:
        _ultima_requisicao = time.monotonic()
    # Sem a URL na mensagem: o CrevoMQ classifica o erro pelo texto, e um
    # número solto na URL (ex.: /404/) viraria um falso erro permanente.
    if resp.status_code == 429 or resp.status_code >= 500:
        retry_after = resp.headers.get("Retry-After")
        sufixo = f" Retry-After: {retry_after}" if retry_after and retry_after.isdigit() else ""
        raise RuntimeError(f"HTTP {resp.status_code}{sufixo}")
    if resp.status_code >= 400:
        raise ErroPermanente(f"HTTP {resp.status_code}")
    return resp.text


def salvar(texto_id: str, campos: dict, db_path: str = DB_PATH) -> None:
    with conectar(db_path) as conn:
        conn.execute(
            "UPDATE contos SET titulo=?, autor=?, serie=?, data_pub=?, temas=?, "
            "estrelas=?, trecho=?, texto_completo=?, coletado_em=? WHERE id=?",
            (
                campos["titulo"],
                campos["autor"],
                campos["serie"],
                campos["data_pub"],
                campos["temas"],
                campos["estrelas"],
                campos["trecho"],
                campos["texto_completo"],
                datetime.now(timezone.utc).isoformat(),
                texto_id,
            ),
        )


def triar(job: dict, campos: dict) -> dict:
    """Payload de saída da extração: o mesmo, ou marcado como descartado
    (e o texto enfileirado na `delecao`) se a regra de descarte recusar."""
    motivo = descarte.motivo(campos["temas"])
    if motivo is None:
        return job["payload"]
    pipeline.enfileirar_delecao(job["id"], job["payload"].get("url"), motivo)
    return {**job["payload"], "descartado": motivo}


def extrair(job: dict) -> dict:
    """Handler do estágio `extracao` (em modo mock, lê as fixtures)."""
    if MOCK_MODE:
        from mocks.extrair_fake import extrair_fake

        return extrair_fake(job)
    campos = parse_pagina_texto(_baixar(job["payload"]["url"]))
    salvar(job["id"], campos)
    return triar(job, campos)
