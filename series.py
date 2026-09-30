"""Séries: agrupa textos do mesmo autor que são partes de uma história.

Duas fontes, juntadas por autor:
- a série que o site informa (`textos.serie`, link "Da série ..." na página);
- o título: "Base - Parte 2", "Base cap. 11", "Base T2 – EP 06", "Base VII",
  "Base 2/3", "Base (Final)", "Base 3 - subtítulo"... Textos do mesmo autor
  com a mesma base (normalizada) formam uma série se ao menos um tiver
  marcador de parte — dois títulos iguais sem número são repostagem, não
  série. Um texto sem marcador cuja base bate com o grupo entra como parte 1
  ("Minha namorada" + "Minha namorada parte II").

Quando a base de um grupo detectado bate com o nome de uma série do site do
mesmo autor, as partes são juntadas nela (o site deixa partes de fora).

A ordem das partes é pelo número do título (temporada, depois os números na
ordem em que aparecem — "Capítulo 15 - Parte 27"), com a data de publicação
desempatando. Quando as partes não têm a mesma base de título (séries do site
que são antologias, cada história com sua numeração), a ordem é a de
publicação.

Grava em `series`/`serie_contos` (handler `agrupar`) e numa coleção do
Qdrant com o vetor médio das partes (handler `vetorizar`), que também marca
`serie_id` nos pontos dos textos — é o que o filtro "só séries" da busca
semântica usa. O trabalho é por autor: recalcular um autor inteiro é
idempotente e barato, e é o que um texto novo ou descartado pede."""

import hashlib
import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone

from config import DB_PATH
from db import conectar

SEP = r"[\s\-–—:,.;|/(\[]"
# Temporada (nível acima da parte): "3a Temporada", "2°TEMPORADA", "Temporada 2", "T2", "Série 2"
_TEMPORADA = re.compile(
    r"(?i)\b(\d{1,2})\s*[ªaº°]?\s*temporada\b|\btemporada\s*(\d{1,2})|\bt(\d{1,2})\b|\bs[eé]rie\s*(\d{1,2})\b"
)
# Números por extenso. Ordinais: "Primeiro Capítulo", "segunda parte",
# "Décimo Primeiro Capítulo"; cardinais só depois da palavra: "parte um".
_UNIDADES_ORD = {"primeir": 1, "segund": 2, "terceir": 3, "quart": 4, "quint": 5, "sext": 6, "setim": 7,
                 "oitav": 8, "non": 9}
_DEZENAS_ORD = {"decim": 10, "vigesim": 20, "trigesim": 30}
_CARDINAIS = {"um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6, "sete": 7,
              "oito": 8, "nove": 9, "dez": 10, "onze": 11, "doze": 12, "treze": 13, "catorze": 14, "quatorze": 14,
              "quinze": 15}
_UNI = r"(?:primeir|segund|terceir|quart|quint|sext|s[eé]tim|oitav|non)[oa]"
_ORD = rf"(?:(?:d[eé]cim|vig[eé]sim|trig[eé]sim)[oa](?:\s+{_UNI})?|{_UNI})"
_CARD = r"(?:um|uma|dois|duas|tr[eê]s|quatro|cinco|seis|sete|oito|nove|dez|onze|doze|treze|catorze|quatorze|quinze)"
_PARTE = r"(?:parte|cap[ií]tulo|epis[oó]dio|ato)"
# "Primeiro Capítulo", "quarto e último capítulo", "Segunda Temporada"
_ORDINAL_ANTES = re.compile(rf"(?i)\b({_ORD})(\s+e\s+[uú]ltim[oa])?\s+({_PARTE}|temporada)\b")
# "1º capítulo", "2ª parte", "3° episódio e último"
_DIGITO_ANTES = re.compile(rf"(?i)\b(\d{{1,3}})\s*[ºª°]\s*(e\s+[uú]ltim[oa]\s+)?{_PARTE}\b")
# "parte um", "capítulo sete", "parte segunda"
_PALAVRA_EXTENSO = re.compile(rf"(?i)\b{_PARTE}\s+({_CARD}|{_ORD})\b")
# "Parte Final: O grande encontro!", "Último Capítulo: ...", "Epílogo" — em
# qualquer lugar do título, não só no fim.
_ULTIMA = re.compile(rf"(?i)\b(?:[uú]ltim[oa]\s+{_PARTE}|{_PARTE}\s+final|ep[ií]logo)\b")
_PROLOGO = re.compile(r"(?i)\bpr[oó]logo\b")
# Palavra de parte + número (arábico ou romano): "Parte 2", "pt4", "Cap. XI", "EP 06", "nº 3"
_PALAVRA = re.compile(
    r"(?i)\b(?:parte|part|pt|cap[ií]tulo|cap|epis[oó]dio|ep|ato|vol(?:ume)?|livro|chapter|n[º°])"
    r"\.?\s*[-–:#]?\s*(\d{1,3}|[ivxlc]{1,7})\b"
)
_FRACAO = re.compile(r"\b(\d{1,3})\s*/\s*\d{1,3}\b")          # "2/3"
_HASH = re.compile(r"#\s*(\d{1,3})\b")                          # "#4"
# Número solto: precedido de espaço/separador e seguido de fim ou separador
# ("Base 11", "Base - 2", "Base 3 - Subtítulo"; não "Comi 2 mulheres"). Não
# vale depois de palavra de época, idade ou valor: "Anos 50: Juventude",
# "Minha vida aos 18", "de R$ 50 - ...".
_NUMERO = re.compile(
    r"(?i)(?<=[\s\-–—:,(])(?<!anos )(?<!ano )(?<!aos )(?<!idade )(?<!R\$ )(?<!R\$)"
    r"(\d{1,3})(?=\s*(?:$|[-–—:.)(|]))"
)
# Romano solto, só maiúsculo: "Base VII", "Base - XXVI", "Base III (final)"
_ROMANO = re.compile(r"(?<=[\s\-–—:,(])([IVXLC]{1,7})(?=\s*(?:$|[-–—:.)(|]))")
_FINAL = re.compile(r"(?i)[\s\-–—:,(]*\(?\s*(?:parte\s+|epis[oó]dio\s+|cap[ií]tulo\s+)?(?:final|fim)\s*\)?\s*[.!]*\s*$")

_ROMANOS = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100}
# Só a forma canônica: em título todo em maiúsculas, "CIVIL" ou "VIVI" são
# palavras, não números.
_ROMANO_CANONICO = re.compile(r"(?i)^c{0,3}(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})$")


def _romano(s: str) -> int | None:
    s = s.lower()
    if not s or not _ROMANO_CANONICO.match(s):
        return None
    total = 0
    for i, ch in enumerate(s):
        v = _ROMANOS[ch]
        total += -v if i + 1 < len(s) and _ROMANOS[s[i + 1]] > v else v
    return total if 0 < total < 200 else None


def _numero(texto: str) -> int | None:
    if texto.isdigit():
        return int(texto)
    return _extenso(texto) or _romano(texto)


def _extenso(texto: str) -> int | None:
    """'sétimo' -> 7, 'Décimo Primeiro' -> 11, 'um' -> 1; None se não for."""
    palavras = normalizar(texto).split()
    if len(palavras) == 1 and palavras[0] in _CARDINAIS:
        return _CARDINAIS[palavras[0]]
    total = 0
    for p in palavras:
        raiz = p[:-1]  # tira o gênero (o/a)
        if raiz in _DEZENAS_ORD:
            total += _DEZENAS_ORD[raiz]
        elif raiz in _UNIDADES_ORD:
            total += _UNIDADES_ORD[raiz]
        else:
            return None
    return total or None


def normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", sem_acento.lower()).strip()


def _limpar_base(base: str) -> str:
    return re.sub(rf"{SEP}+$", "", base).strip(" \"'“”«»")


@dataclass
class Titulo:
    base: str                 # como no título, para exibir
    chave: str                # base normalizada, para agrupar
    temporada: int | None = None
    numeros: tuple[int, ...] = ()   # na ordem do título: ("Capítulo 15 - Parte 27") -> (15, 27)
    final: bool = False

    @property
    def numero(self) -> int | None:
        return self.numeros[0] if self.numeros else None

    @property
    def marcado(self) -> bool:
        return bool(self.numeros) or self.temporada is not None or self.final


def _marcadores(t: str) -> list[tuple[int, int, str, int]]:
    """(início, fim, tipo, valor) de todos os marcadores, sem sobreposição —
    na ordem de prioridade: temporada, números por extenso, palavra de parte,
    final/epílogo, prólogo, fração, #, número solto, romano solto. `tipo` é
    'temporada', 'parte' ou 'final' (valor ignorado)."""
    achados: list[tuple[int, int, str, int]] = []

    def livre(a: int, b: int) -> bool:
        return all(b <= x or a >= y for x, y, _, _ in achados)

    def achar(padrao: re.Pattern, tipo_de) -> None:
        for m in padrao.finditer(t):
            if m.start() > 0 and livre(m.start(), m.end()):
                for tipo, valor in tipo_de(m):
                    achados.append((m.start(), m.end(), tipo, valor))

    def numero_do_grupo(m, grupo=1):
        n = _numero(m.group(grupo))
        return [("parte", n)] if n is not None else []

    achar(_TEMPORADA, lambda m: [("temporada", int(next(g for g in m.groups() if g)))])
    achar(_ORDINAL_ANTES, lambda m: (
        [("temporada" if m.group(3).lower() == "temporada" else "parte", _extenso(m.group(1)))]
        + ([("final", 0)] if m.group(2) else [])) if _extenso(m.group(1)) else [])
    achar(_DIGITO_ANTES, lambda m: [("parte", int(m.group(1)))] + ([("final", 0)] if m.group(2) else []))
    achar(_PALAVRA, numero_do_grupo)
    achar(_PALAVRA_EXTENSO, numero_do_grupo)
    achar(_ULTIMA, lambda m: [("final", 0)])
    achar(_PROLOGO, lambda m: [("parte", 0)])
    for padrao in (_FRACAO, _HASH, _NUMERO, _ROMANO):
        achar(padrao, numero_do_grupo)
    return sorted(achados)


def analisar(titulo: str | None) -> Titulo:
    """Separa o título em base + marcadores de parte. Sem marcador, a base é
    o título inteiro."""
    t = (titulo or "").strip()
    final = bool(_FINAL.search(t))
    if final:
        t = _FINAL.sub("", t)

    marcadores = _marcadores(t)
    corte = marcadores[0][0] if marcadores else len(t)
    temporada = next((v for _, _, tipo, v in marcadores if tipo == "temporada"), None)
    numeros = tuple(v for _, _, tipo, v in marcadores if tipo == "parte")[:3]
    final = final or any(tipo == "final" for _, _, tipo, _ in marcadores)
    base = _limpar_base(t[:corte])
    chave = normalizar(base)
    if len(chave) < 3:  # "Parte 2" sem base nenhuma: não dá pra agrupar pelo título
        base, chave, temporada, numeros, final = t, normalizar(t), None, (), False
    return Titulo(base=base, chave=chave, temporada=temporada, numeros=numeros, final=final)


# ---------------------------------------------------------------- agrupamento

@dataclass
class Serie:
    autor: str
    nome: str
    origem: str               # 'site' | 'titulo'
    chave: str
    partes: list[dict] = field(default_factory=list)  # linhas de textos, já em ordem

    @property
    def id(self) -> str:
        # Pela chave do autor, não pela grafia: a série não muda de id se a
        # grafia mais usada entre as partes mudar.
        return hashlib.sha1(f"{chave_autor(self.autor)}\x1f{self.origem}\x1f{self.chave}".encode()).hexdigest()[:16]


def chave_autor(autor: str | None) -> str:
    """O mesmo autor aparece com grafias diferentes no site — 'Biel'/'BIEL',
    'Ana Beatriz S'/'Ana Beatriz S.', '2H2O'/'2H20' (letra O e zero) — e as
    partes de uma série se espalham entre elas. Pra agrupar séries, vale esta
    chave: sem maiúsculas, acento, espaço nem pontuação, e 0 = o. Só junta
    partes com o mesmo título-base, então dois autores diferentes com o
    mesmo nome só se misturariam se escrevessem a mesma série."""
    return normalizar(autor or "").replace(" ", "").replace("0", "o")


def _ordem_titulo(doc: dict) -> tuple:
    t: Titulo = doc["_titulo"]
    # Sem número: costuma ser a parte 1 ("Base" + "Base parte 2") -> () vem
    # antes de tudo. "Final" sem número vai pro fim; com número, desempata
    # logo depois do mesmo número ("Cap. 2" < "Cap. 2 (Parte Final)").
    numeros = t.numeros or ((10**6,) if t.final else ())
    return (t.temporada or 0, numeros, t.final, doc.get("data_pub") or "", doc["id"])


def _ordem_data(doc: dict) -> tuple:
    return (doc.get("data_pub") or "", doc["id"])


def _mesma_historia(membros: list[dict]) -> bool:
    """Todas as partes têm a mesma base de título? Séries do site às vezes são
    antologias (várias histórias, cada uma com a própria numeração); aí a
    ordem é a de publicação, não a dos números."""
    chaves = {c["_titulo"].chave for c in membros}
    return len(chaves) == 1 and any(c["_titulo"].marcado for c in membros)


def _prefixo_comum(titulos: list[str]) -> str:
    palavras = [t.split() for t in titulos]
    comum = []
    for grupo in zip(*palavras):
        if len({normalizar(p) for p in grupo}) != 1:
            break
        comum.append(grupo[0])
    return _limpar_base(" ".join(comum))


def agrupar(docs: list[dict]) -> list[Serie]:
    """Séries de UM autor (todas as grafias dele — ver `chave_autor`).
    `textos`: dicts com id, titulo, autor, serie (do site) e data_pub."""
    if not docs:
        return []
    for c in docs:
        c["_titulo"] = analisar(c.get("titulo"))

    grupos: dict[tuple[str, str], list[dict]] = {}
    nomes: dict[tuple[str, str], str] = {}
    # Séries do site primeiro; os nomes (e as bases dos títulos delas) viram
    # apelidos, para as partes que o site deixou de fora entrarem nelas.
    apelidos: dict[str, tuple[str, str]] = {}
    for c in docs:
        if c.get("serie") and normalizar(c["serie"]):
            g = ("site", normalizar(c["serie"]))
            grupos.setdefault(g, []).append(c)
            nomes.setdefault(g, c["serie"].strip())
    for g, membros in grupos.items():
        apelidos.setdefault(g[1], g)
        for c in membros:
            apelidos.setdefault(c["_titulo"].chave, g)

    for c in docs:
        if c.get("serie") and normalizar(c["serie"]):
            continue
        t: Titulo = c["_titulo"]
        g = apelidos.get(t.chave) or ("titulo", t.chave)
        grupos.setdefault(g, []).append(c)
        if g[0] == "titulo" and t.marcado:
            nomes.setdefault(g, t.base)

    series = []
    for (origem, chave), membros in grupos.items():
        if len(membros) < 2:
            continue
        if origem == "titulo" and not any(c["_titulo"].marcado for c in membros):
            continue  # títulos iguais sem número: repostagem, não série
        nome = nomes.get((origem, chave)) or membros[0]["_titulo"].base
        numeros = {c["_titulo"].numeros[:1] for c in membros}
        titulos_distintos = len({normalizar(c["titulo"]) for c in membros}) > 1
        if origem == "titulo" and len(numeros) == 1 and () not in numeros and titulos_distintos:
            # Todas com o mesmo número: ele é parte do nome ("... de R$ 50 -
            # subtítulo"), não a parte — o nome vem do começo comum dos títulos.
            nome = _prefixo_comum([c["titulo"] for c in membros]) or nome
        series.append(_montar(origem, chave, nome, membros))
    return _por_prefixo(docs, series)


def _montar(origem: str, chave: str, nome: str, membros: list[dict]) -> Serie:
    ordem = _ordem_titulo if _mesma_historia(membros) else _ordem_data
    partes = sorted(membros, key=ordem)
    # Grafia do autor mostrada: a mais usada nas partes (empate: a da parte 1).
    grafias = Counter(c["autor"] for c in partes)
    autor = max(grafias, key=lambda a: (grafias[a], a == partes[0]["autor"]))
    return Serie(autor=autor, nome=nome, origem=origem, chave=chave, partes=partes)


# Prefixo comum: "Memorial de Dona Maroca, a filha da luxúria - Prólogo",
# "... - Anos 50: Juventude", "... - Últimos Tempos: A despedida". Separador
# " - ", ": " ou ". " depois de pelo menos 3 palavras.
_SEP_PREFIXO = re.compile(r"\s+[-–—]\s+|:\s+|\.\s+")
PREFIXO_MIN_PALAVRAS = 3


def prefixo(titulo: str | None) -> str | None:
    t = (titulo or "").strip()
    m = _SEP_PREFIXO.search(t)
    if not m or not t[m.end():].strip():
        return None
    p = _limpar_base(t[:m.start()])
    return p if len(normalizar(p).split()) >= PREFIXO_MIN_PALAVRAS else None


def _por_prefixo(docs: list[dict], series: list[Serie]) -> list[Serie]:
    """Segunda passada, só com os textos que não entraram em série: agrupa
    pelo prefixo comum do título (os marcados, pela base). Grupo com a mesma
    chave de uma série já formada é somado a ela."""
    em_serie = {c["id"] for s in series for c in s.partes}
    grupos: dict[str, list[dict]] = {}
    nomes: dict[str, str] = {}
    for c in docs:
        if c["id"] in em_serie:
            continue
        t: Titulo = c["_titulo"]
        p = None if t.marcado else prefixo(c.get("titulo"))
        chave = t.chave if t.marcado else (normalizar(p) if p else None)
        if chave:
            grupos.setdefault(chave, []).append(c)
            nomes.setdefault(chave, t.base if t.marcado else p)
    por_chave = {s.chave: i for i, s in enumerate(series) if s.origem == "titulo"}
    for chave, membros in grupos.items():
        if chave in por_chave:
            s = series[por_chave[chave]]
            series[por_chave[chave]] = _montar(s.origem, s.chave, s.nome, s.partes + membros)
        elif len({normalizar(c.get("titulo")) for c in membros}) >= 2:  # repostagem do mesmo título não conta
            series.append(_montar("titulo", chave, nomes[chave], membros))
    return series


_grafias: dict[str, set[str]] = {}


def grafias(conn, autores: list[str]) -> list[str]:
    """Todas as grafias dos autores dados (ver `chave_autor`). O mapa é
    montado uma vez por processo e remontado quando aparece autor novo."""
    global _grafias
    if not _grafias or any(chave_autor(a) not in _grafias for a in autores):
        mapa: dict[str, set[str]] = {}
        for (a,) in conn.execute("SELECT DISTINCT autor FROM contos WHERE autor IS NOT NULL AND autor <> ''"):
            mapa.setdefault(chave_autor(a), set()).add(a)
        _grafias = mapa
    return sorted({g for a in autores for g in _grafias.get(chave_autor(a), {a})})


def textos_dos_autores(conn, autores: list[str]) -> list[dict]:
    return [dict(l) for l in conn.execute(
        f"SELECT id, titulo, autor, serie, data_pub, temas, estrelas FROM contos "
        f"WHERE estagio = 'concluido' AND autor IN ({','.join('?' * len(autores))})", autores)]


# ---------------------------------------------------------------- gravação

def gravar_autor(conn, autores: list[str], series: list[Serie], ids_do_autor: list[str]) -> None:
    """Troca as séries do autor (todas as grafias em `autores`) pelas
    recalculadas, numa transação."""
    for i in range(0, len(ids_do_autor), 500):
        parte = ids_do_autor[i:i + 500]
        conn.execute(f"DELETE FROM serie_contos WHERE conto_id IN ({','.join('?' * len(parte))})", parte)
    conn.execute(f"DELETE FROM series WHERE autor IN ({','.join('?' * len(autores))})", autores)
    agora = datetime.now(timezone.utc).isoformat()
    for s in series:
        temas = Counter(t for c in s.partes for t in json.loads(c.get("temas") or "[]"))
        datas = [c["data_pub"] for c in s.partes if c.get("data_pub")]
        conn.execute(
            "INSERT INTO series (id, autor, nome, origem, n_partes, primeiro_conto, primeira_pub, ultima_pub, "
            "estrelas, temas, atualizado_em) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (s.id, s.autor, s.nome, s.origem, len(s.partes), s.partes[0]["id"], min(datas, default=None),
             max(datas, default=None), sum(c.get("estrelas") or 0 for c in s.partes),
             json.dumps([t for t, _ in temas.most_common(12)], ensure_ascii=False), agora),
        )
        conn.executemany(
            "INSERT OR REPLACE INTO serie_contos (conto_id, serie_id, parte, numero) VALUES (?, ?, ?, ?)",
            [(c["id"], s.id, i, c["_titulo"].numero) for i, c in enumerate(s.partes, 1)],
        )


# ---------------------------------------------------------------- handlers

def agrupar_autores(job: dict, db_path: str = DB_PATH) -> dict:
    """Estágio `agrupar`: recalcula as séries dos autores do job no SQLite,
    cada um com todas as grafias do nome. Passa adiante a lista expandida —
    o `vetorizar` precisa trocar as séries e os payloads de todas elas."""
    todas: set[str] = set()
    feitas: set[str] = set()
    for autor in job["payload"]["autores"]:
        if chave_autor(autor) in feitas:
            continue
        feitas.add(chave_autor(autor))
        with conectar(db_path) as conn:
            variantes = grafias(conn, [autor])
            docs = textos_dos_autores(conn, variantes)
            gravar_autor(conn, variantes, agrupar(docs), [c["id"] for c in docs])
        todas.update(variantes)
    return {**job["payload"], "autores": sorted(todas)}


def vetorizar(job: dict, db_path: str = DB_PATH) -> dict:
    """Estágio `vetorizar`: coleção de séries no Qdrant (vetor médio das
    partes) e `serie_id` no payload dos pontos dos textos."""
    from embeddings import enviar_qdrant

    enviar_qdrant.sincronizar_series(job["payload"]["autores"], db_path=db_path)
    return job["payload"]
