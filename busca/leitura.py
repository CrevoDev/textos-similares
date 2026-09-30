"""Preparação do texto para a página de leitura do app.

Os textos extraídos antes da correção do parser têm `texto_completo` sem
quebras: `article.text(strip=True)` colava os `<p>` ("…percebiam.Uma vez…").
Para esses, os parágrafos são reconstruídos por heurística — um fim de frase
grudado no começo da frase seguinte (sem espaço) quase sempre é uma fronteira
de parágrafo, porque dentro do mesmo parágrafo sempre há espaço depois do ponto.
Textos extraídos depois da correção já chegam com `\\n\\n` entre parágrafos."""

import re

PALAVRAS_POR_MINUTO = 200

# Fim de frase (pontuação + aspas/parênteses de fechamento opcionais) seguido,
# sem espaço, de maiúscula, travessão/hífen de diálogo ou aspas de abertura.
_FRONTEIRA = re.compile(
    r"""(?<=[.!?…:;"”»)])(?=[A-ZÀ-ÖØ-Þ—–\-"“«(])"""
    # fala que começa logo depois da deixa do narrador: "disse- O quê?" —
    # o hífen precisa de espaço depois, senão "Coca-Cola" seria cortado
    r"""|(?<=[a-zà-öø-ÿ])(?=[—–]\s?[A-ZÀ-ÖØ-Þ]|-\s[A-ZÀ-ÖØ-Þ])"""
    # parágrafo que termina sem pontuação: "olho para meu paiMeu pai fala",
    # "kkkkEu", "JoséPergunto" (3+ minúsculas coladas numa palavra capitalizada)
    r"""|(?<=[a-zà-öø-ÿ]{3})(?=[A-ZÀ-ÖØ-Þ][a-zà-öø-ÿ])"""
)
# ...exceto palavras que são mesmo escritas assim (as que aparecem nos textos)
_CAMEL_CASE = {"whatsapp", "playstation", "youtube", "iphone", "tiktok", "linkedin", "facebook",
               "mcdonald", "mcdonalds", "netflix", "powerpoint", "onlyfans", "macbook", "airbnb"}


def paragrafos(texto: str | None) -> list[str]:
    if not texto:
        return []
    if "\n\n" in texto:
        return [re.sub(r"\s+", " ", p).strip() for p in texto.split("\n\n") if p.strip()]
    texto = re.sub(r"\s+", " ", texto)
    saida: list[str] = []
    inicio = 0
    for m in _FRONTEIRA.finditer(texto):
        pos = m.start()
        # sigla ("E.U.A.Então"): ponto depois de maiúscula isolada não fecha frase
        if (texto[pos - 1] == "." and pos >= 2 and texto[pos - 2].isupper()
                and (pos < 3 or texto[pos - 3] in ". ")):
            continue
        if texto[pos - 1].islower() and texto[pos].isupper():
            ini = pos
            while ini > 0 and texto[ini - 1].isalpha():
                ini -= 1
            fim = pos
            while fim < len(texto) and texto[fim].isalpha():
                fim += 1
            palavra = texto[ini:fim].lower()
            if palavra in _CAMEL_CASE or palavra.rstrip("s") in _CAMEL_CASE:
                continue
        # aspas: com uma fala aberta no parágrafo atual (nº ímpar de aspas),
        # a próxima aspa FECHA a fala e fica com ela — `Lipe?""Estou` corta
        # entre as duas aspas, não antes delas
        if texto[pos] in _ASPAS and _aspas_abertas(texto[inicio:pos]):
            continue
        # e uma fala aberta há pouco não é cortada no meio ("Oi.Tudo bem?" é
        # uma fala só); o limite evita que uma aspa esquecida pelo autor
        # engula o resto do texto num parágrafo só
        if texto[pos] not in _ASPAS and _aspas_abertas(texto[inicio:pos]):
            ultima = max(texto.rfind(a, inicio, pos) for a in _ASPAS)
            if pos - ultima < 400:
                continue
        trecho = texto[inicio:pos].strip()
        if trecho:
            saida.append(trecho)
        inicio = pos
    resto = texto[inicio:].strip()
    if resto:
        saida.append(resto)
    return _juntar_soltos(saida)


_ASPAS = '"“”'


def _aspas_abertas(trecho: str) -> bool:
    """Retas contam por paridade; curvas pelo saldo abre/fecha."""
    retas = trecho.count('"') % 2 == 1
    curvas = trecho.count("“") > trecho.count("”")
    return retas or curvas


def _juntar_soltos(partes: list[str]) -> list[str]:
    """Rede de segurança: pedaço sem nenhuma letra (uma aspa, um parêntese)
    volta pro parágrafo anterior em vez de virar parágrafo sozinho."""
    saida: list[str] = []
    for p in partes:
        if saida and not re.search(r"\w", p):
            saida[-1] += p
        else:
            saida.append(p)
    return saida


def tempo_leitura(texto: str | None) -> int:
    """Minutos estimados (mínimo 1)."""
    if not texto:
        return 0
    return max(1, round(len(texto.split()) / PALAVRAS_POR_MINUTO))
