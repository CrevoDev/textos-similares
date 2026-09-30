"""Normalização das tags livres ("Assuntos") — tag original -> tag canônica.

    DB_PATH=data/contos.db uv run python busca_v2/tags.py                  # gera a tabela e mostra o relatório
    DB_PATH=data/contos.db uv run python busca_v2/tags.py --limiar 0.9     # mais conservador

São ~52 mil tags distintas, 2/3 usadas uma vez só. Duas camadas:

1. Léxica (sempre segura): minúsculas, sem acento, sem pontuação e espaços
   extras, e plural simples ("cadelas" -> "cadela", só se o singular existir).
   "Adolescência", "adolescencia" e "adolescência." viram o mesmo grupo.
2. Semântica (DESLIGADA por padrão; `--semantico` liga): cada grupo com menos de `--min-freq` usos é
   ligado ao grupo FREQUENTE mais parecido se a similaridade passar do
   limiar ("traiçao da esposa" -> "esposa traidora"). Frequentes nunca se
   fundem entre si: duas tags populares parecidas podem ser categorias distintas no site.
   Usa o modelo de paráfrase da v1, que é treinado justamente pra dizer se
   duas frases curtas significam a mesma coisa. Na prática erra demais com
   tags curtas, mesmo com similaridade alta ("espiada" -> "porrada" a 0,95,
   "A FILHA" -> "Sobrinha"), por isso não é usada — as tags sugeridas pelo
   LLM nos resumos são o caminho pra agrupar por significado.

A forma exibida de cada grupo é a grafia original mais usada dele. Nada é
alterado no contos.db: o resultado fica em `tags_canonicas` (busca_v2.db) e
`canonizar()` aplica na hora de indexar/filtrar."""

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from config import DB_PATH, EMBEDDING_MODEL  # noqa: E402
from busca_v2 import fonte  # noqa: E402

SCHEMA = """
CREATE TABLE IF NOT EXISTS tags_canonicas (
    tag       TEXT PRIMARY KEY,   -- grafia original, como está no contos.db
    canonica  TEXT NOT NULL,      -- forma exibida do grupo
    metodo    TEXT NOT NULL,      -- propria | lexico | semantico
    score     REAL,               -- similaridade (só semântico)
    usos      INTEGER NOT NULL
);
"""

_cache: dict[str, str] | None = None


def chave_lexica(tag: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", tag.lower()).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", sem_acento)).strip()


def _contar_tags(db_path: str) -> Counter:
    with fonte.conectar_fonte(db_path) as conn:
        return Counter({l[0]: l[1] for l in conn.execute(
            "SELECT j.value, COUNT(*) FROM contos c, json_each(c.temas) j WHERE c.estagio = 'concluido' GROUP BY 1"
        )})


def _grupos_lexicos(usos: Counter) -> dict[str, list[str]]:
    grupos: dict[str, list[str]] = defaultdict(list)
    for tag in usos:
        chave = chave_lexica(tag)
        if chave:
            grupos[chave].append(tag)
    # plural simples: só junta se o singular também existir como grupo
    for chave in [c for c in grupos if c.endswith("s") and len(c) > 4]:
        singular = chave[:-1]
        if singular in grupos and chave in grupos:
            grupos[singular] += grupos.pop(chave)
    return grupos


def gerar(limiar: float, min_freq: int, db_path: str, semantico: bool = False) -> list[tuple]:
    usos = _contar_tags(db_path)
    grupos = _grupos_lexicos(usos)
    total = {c: sum(usos[t] for t in tags) for c, tags in grupos.items()}
    exibicao = {c: max(tags, key=lambda t: usos[t]) for c, tags in grupos.items()}
    print(f"[tags] {len(usos)} tags distintas -> {len(grupos)} grupos léxicos")

    destino = {c: (c, None) for c in grupos}  # chave -> (chave canônica, score)
    if semantico:
        _fundir_raras(grupos, total, exibicao, destino, limiar, min_freq)
    linhas = []
    for chave, tags in grupos.items():
        alvo, score = destino[chave]
        for tag in tags:
            if alvo != chave:
                metodo = "semantico"
            elif tag == exibicao[chave]:
                metodo = "propria"
            else:
                metodo = "lexico"
            linhas.append((tag, exibicao[alvo], metodo, score, usos[tag]))
    return linhas


def _fundir_raras(grupos, total, exibicao, destino, limiar: float, min_freq: int) -> None:
    frequentes = [c for c in grupos if total[c] >= min_freq]
    raras = [c for c in grupos if total[c] < min_freq]
    from sentence_transformers import SentenceTransformer

    modelo = SentenceTransformer(EMBEDDING_MODEL)
    vf = modelo.encode([exibicao[c] for c in frequentes], batch_size=256, normalize_embeddings=True)
    vr = modelo.encode([exibicao[c] for c in raras], batch_size=256, normalize_embeddings=True)

    for i in range(0, len(raras), 2000):
        sims = vr[i : i + 2000] @ vf.T
        melhores = sims.argmax(axis=1)
        for j, k in enumerate(melhores):
            score = float(sims[j, k])
            if score >= limiar:
                destino[raras[i + j]] = (frequentes[k], score)


def salvar(linhas: list[tuple]) -> None:
    with fonte.conectar_estado() as estado:
        estado.executescript(SCHEMA)
        estado.execute("DELETE FROM tags_canonicas")
        estado.executemany("INSERT INTO tags_canonicas VALUES (?, ?, ?, ?, ?)", linhas)


def canonizar(temas: list[str] | str | None) -> list[str]:
    """Tags de um texto -> canônicas, sem repetição, na ordem original.
    Tag que não está na tabela (texto novo) passa como veio."""
    global _cache
    if _cache is None:
        with fonte.conectar_estado() as estado:
            estado.executescript(SCHEMA)
            _cache = {l["tag"]: l["canonica"] for l in estado.execute("SELECT tag, canonica FROM tags_canonicas")}
    if isinstance(temas, str):
        temas = json.loads(temas)
    return list(dict.fromkeys(_cache.get(t, t) for t in temas or []))


def relatorio(linhas: list[tuple], mostrar: int) -> None:
    canonicas = {l[1] for l in linhas}
    por_metodo = Counter(l[2] for l in linhas)
    print(f"[tags] {len(linhas)} tags -> {len(canonicas)} canônicas · {dict(por_metodo)}")
    usos_semantico = sum(l[4] for l in linhas if l[2] == "semantico")
    if usos_semantico:
        print(f"[tags] usos remapeados pela camada semântica: {usos_semantico}")

    lexicos = defaultdict(list)
    for tag, canonica, metodo, _, n in linhas:
        if metodo == "lexico":
            lexicos[canonica].append(tag)
    print("\n--- léxico (maiores grupos)")
    for canonica, tags in sorted(lexicos.items(), key=lambda x: -len(x[1]))[:mostrar]:
        print(f"  {canonica!r} <- {tags[:6]}")

    semanticos = sorted((l for l in linhas if l[2] == "semantico"), key=lambda l: l[3])
    if not semanticos:
        return
    print("\n--- semântico: os MAIS FRACOS aceitos (é aqui que o limiar erra)")
    for tag, canonica, _, score, _ in semanticos[:mostrar]:
        print(f"  [{score:.3f}] {tag!r} -> {canonica!r}")
    print("\n--- semântico: amostra aleatória")
    rng = np.random.default_rng(0)
    for idx in rng.choice(len(semanticos), size=min(mostrar, len(semanticos)), replace=False):
        tag, canonica, _, score, _ = semanticos[idx]
        print(f"  [{score:.3f}] {tag!r} -> {canonica!r}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalização das tags")
    parser.add_argument("--limiar", type=float, default=0.85, help="similaridade mínima pra fundir uma tag rara")
    parser.add_argument("--min-freq", type=int, default=20, help="grupos com menos usos que isso são 'raros'")
    parser.add_argument("--semantico", action="store_true", help="liga a camada semântica (experimental, erra bastante)")
    parser.add_argument("--mostrar", type=int, default=15)
    parser.add_argument("--db-path", default=DB_PATH)
    args = parser.parse_args()
    linhas = gerar(args.limiar, args.min_freq, args.db_path, args.semantico)
    salvar(linhas)
    relatorio(linhas, args.mostrar)


if __name__ == "__main__":
    main()
