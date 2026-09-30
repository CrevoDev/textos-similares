"""Parsing da página de um texto — seletores mapeados na Fase 1
(ver fixtures/SELETORES.md). Usado tanto pelo extrator real quanto pelo mock,
para que o mock exercite o mesmo código de parsing contra HTML real."""

import json
import re
from datetime import datetime

from selectolax.parser import HTMLParser

TAMANHO_TRECHO = 1500


def _parse_data_pub(texto: str | None) -> str | None:
    if not texto:
        return None
    match = re.search(r"(\d{2})/(\d{2})/(\d{4})\s+(\d{2}:\d{2}:\d{2})", texto)
    if not match:
        return None
    dia, mes, ano, hora = match.groups()
    try:
        dt = datetime.strptime(f"{dia}/{mes}/{ano} {hora}", "%d/%m/%Y %H:%M:%S")
    except ValueError:
        return None
    return dt.isoformat()


def parse_pagina_texto(html: str) -> dict:
    tree = HTMLParser(html)

    titulo_no = tree.css_first("h1.main") or tree.css_first("title")
    titulo = titulo_no.text(strip=True) if titulo_no else None

    # Quando o texto pertence a uma série, existem dois blocos `.autor`: o
    # primeiro é "Da série <a href=/serie/..>", o segundo é o autor de fato
    # ("Um texto erótico de <a href=/perfil/..>"). Não dá pra confiar na
    # posição — tem que escolher pelo href.
    autor_no = tree.css_first('.textinfo .autor a[href*="/perfil/"]')
    autor = autor_no.text(strip=True) if autor_no else None

    serie_no = tree.css_first('.textinfo .autor a[href*="/serie/"]')
    serie = serie_no.text(strip=True) if serie_no else None

    blocos_categoria = tree.css(".textinfo .categoria")
    data_no = tree.css_first(".textinfo .data")
    data_pub = _parse_data_pub(data_no.text(strip=True) if data_no else None)

    temas: list[str] = []
    if len(blocos_categoria) > 1:
        temas = [a.text(strip=True) for a in blocos_categoria[1].css('a[href^="/tema/"]')]

    estrelas_no = tree.css_first("#valor-estrelas")
    estrelas = None
    if estrelas_no:
        texto_estrelas = estrelas_no.text(strip=True)
        digitos = re.sub(r"\D", "", texto_estrelas)
        estrelas = int(digitos) if digitos else None

    # Um parágrafo por `<p>`, separados por linha em branco — a página de
    # leitura do app depende disso (`article.text(strip=True)` colava tudo).
    article_no = tree.css_first("article")
    texto_completo = None
    if article_no:
        paragrafos = [re.sub(r"\s+", " ", p.text()).strip() for p in article_no.css("p")]
        texto_completo = "\n\n".join(p for p in paragrafos if p) or article_no.text(strip=True) or None
    trecho = texto_completo[:TAMANHO_TRECHO] if texto_completo else None

    return {
        "titulo": titulo,
        "autor": autor,
        "serie": serie,
        "data_pub": data_pub,
        "temas": json.dumps(temas, ensure_ascii=False),
        "estrelas": estrelas,
        "trecho": trecho,
        "texto_completo": texto_completo,
    }
