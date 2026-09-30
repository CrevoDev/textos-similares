"""Divisão do texto em trechos — a parte que precisa dar exatamente o mesmo
resultado na indexação local (trechos.py) e na GPU alugada (gerar_vetores.py).

Sem nenhum import do projeto (config, SQLite, Qdrant): este arquivo é copiado
junto com gerar_vetores.py pra máquina remota e roda sozinho lá."""


def prefixos(nome_modelo: str) -> tuple[str, str]:
    """(consulta, passagem). A família E5 foi treinada com esses prefixos;
    sem eles a qualidade cai bastante. Outros modelos (bge-m3, MiniLM) não usam."""
    if "e5" in nome_modelo.lower():
        return "query: ", "passage: "
    return "", ""


def espalhados(n: int, maximo: int) -> list[int]:
    """`maximo` índices distribuídos de 0 a n-1, sempre incluindo o primeiro e o último."""
    if maximo <= 0 or n <= maximo:
        return list(range(n))
    if maximo == 1:
        return [0]
    return sorted({round(i * (n - 1) / (maximo - 1)) for i in range(maximo)})


def janelas(offsets: list[tuple[int, int]], tokens: int, sobreposicao: int) -> list[tuple[int, int]]:
    """Janelas de `tokens` tokens com `sobreposicao` entre vizinhas, em offsets de caractere."""
    if not offsets:
        return []
    passo = max(1, tokens - sobreposicao)
    saida = []
    for ini in range(0, len(offsets), passo):
        fim = min(ini + tokens, len(offsets))
        saida.append((offsets[ini][0], offsets[fim - 1][1]))
        if fim == len(offsets):
            break
    return saida


def montar(texto: str, titulo: str, temas: list[str], offsets: list[tuple[int, int]], nome_modelo: str,
           tokens: int, sobreposicao: int, maximo: int) -> list[dict]:
    """Trechos prontos pro modelo. O título vai na frente de todo trecho (dá
    contexto a uma cena solta) e as tags só no primeiro. `offsets` vem do
    tokenizer do próprio modelo (`return_offsets_mapping=True`, sem tokens
    especiais) — quem chama tokeniza, pra poder fazer isso em lote."""
    todas = janelas(offsets, tokens, sobreposicao)
    _, prefixo = prefixos(nome_modelo)
    temas_txt = " ".join(temas)
    trechos = []
    for ordem in espalhados(len(todas), maximo):
        inicio, fim = todas[ordem]
        cabecalho = "\n".join(p for p in (titulo, temas_txt if ordem == 0 else "") if p)
        trechos.append({
            "ordem": ordem,
            "inicio": inicio,
            "fim": fim,
            "texto_embedding": f"{prefixo}{cabecalho}\n{texto[inicio:fim]}".strip(),
        })
    return trechos
