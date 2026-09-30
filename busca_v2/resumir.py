"""Resumos estruturados com um LLM de pesos abertos rodando por sua conta.

    DB_PATH=data/contos.db uv run python busca_v2/resumir.py                       # 50 da amostra, provedor mock
    DB_PATH=data/contos.db LLM_PROVEDOR=openai LLM_URL=http://localhost:8080/v1 \\
        uv run python busca_v2/resumir.py --limite 50                             # teste de qualidade/recusa
    ... --modo completo                                                           # só com a extração terminada

`openai` fala com qualquer servidor que implemente /v1/chat/completions —
llama.cpp (`llama-server -m qwen3-4b-q4_k_m.gguf`), Ollama, ou vLLM numa GPU
alugada pro lote grande; o código é o mesmo, só muda LLM_URL. `mock` não
chama rede nenhuma: monta um "resumo" com as primeiras frases, só pra
exercitar o fluxo e o armazenamento.

O relatório final separa ok / recusa / erro e estima o tempo pra base
inteira — é isso que decide se vale rodar localmente ou alugar GPU."""

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from config import (  # noqa: E402
    BUSCA_V2_MODO,
    DB_PATH,
    LLM_API_KEY,
    LLM_MAX_CHARS,
    LLM_MODELO,
    LLM_PROVEDOR,
    LLM_TIMEOUT_S,
    LLM_URL,
)
from busca_v2 import fonte  # noqa: E402

LIMITE_PADRAO = 50

INSTRUCOES = """Você cataloga uma biblioteca pessoal de textos em português. Leia o texto e \
responda SOMENTE com um objeto JSON, sem texto antes ou depois, com estes campos:

- "sinopse": 3 a 5 frases descrevendo o enredo (quem são os personagens, a situação, o que \
muda do começo ao fim). Tom neutro de catálogo: descreva o que acontece sem reproduzir \
cenas nem linguagem explícita.
- "personagens": lista de objetos {"nome": string ou null, "papel": string curta}.
- "cenario": onde e quando se passa, em poucas palavras.
- "tom": uma ou duas palavras (ex.: romântico, cômico, dramático, suspense).
- "temas": 3 a 8 tags curtas em minúsculas que ajudem a encontrar o texto numa busca."""

PADROES_RECUSA = re.compile(
    r"não posso|nao posso|não consigo ajudar|não é apropriado|não vou|conteúdo explícito|"
    r"i can(?:'|no)t|i'm sorry|i am sorry|as an ai|desculpe,? mas",
    re.IGNORECASE,
)
_PENSAMENTO = re.compile(r"<think>.*?</think>", re.DOTALL)


def _recortar(texto: str, maximo: int = LLM_MAX_CHARS) -> str:
    """Textos longos não cabem inteiros num modelo pequeno: começo, meio e
    fim bastam pra sinopse (o começo apresenta, o fim resolve)."""
    if len(texto) <= maximo:
        return texto
    a, b, c = int(maximo * 0.45), int(maximo * 0.25), int(maximo * 0.30)
    meio = len(texto) // 2
    return f"{texto[:a]}\n[...]\n{texto[meio - b // 2 : meio + b // 2]}\n[...]\n{texto[-c:]}"


def _mensagens(doc: dict) -> list[dict]:
    texto = _recortar(doc.get("texto_completo") or doc.get("trecho") or "")
    usuario = f"Título: {texto.get('titulo') or '(sem título)'}\n\nConto:\n{texto}"
    if "qwen3" in LLM_MODELO.lower():
        usuario += "\n\n/no_think"  # Qwen3 pensa em voz alta por padrão: gasta tokens e tempo à toa aqui
    return [{"role": "system", "content": INSTRUCOES}, {"role": "user", "content": usuario}]


def _chamar_openai(cliente: httpx.Client, doc: dict) -> str:
    corpo = {
        "model": LLM_MODELO,
        "messages": _mensagens(doc),
        "temperature": 0.2,
        "max_tokens": 700,
        "response_format": {"type": "json_object"},
    }
    resposta = cliente.post("/chat/completions", json=corpo)
    if resposta.status_code == 400:
        # nem todo servidor aceita response_format — o prompt já pede JSON
        corpo.pop("response_format")
        resposta = cliente.post("/chat/completions", json=corpo)
    resposta.raise_for_status()
    return resposta.json()["choices"][0]["message"]["content"] or ""


def _chamar_mock(doc: dict) -> str:
    frases = re.split(r"(?<=[.!?])\s+", (doc.get("trecho") or "").strip())
    temas = json.loads(doc["temas"]) if doc.get("temas") else []
    return json.dumps({
        "sinopse": " ".join(frases[:3])[:400],
        "personagens": [],
        "cenario": "",
        "tom": "mock",
        "temas": [t.lower() for t in temas[:5]],
    }, ensure_ascii=False)


def interpretar(bruto: str) -> tuple[str, dict | None]:
    """(status, campos): ok com JSON válido e sinopse; recusa se o modelo
    negou (com ou sem JSON); erro pra qualquer outra resposta quebrada."""
    limpo = _PENSAMENTO.sub("", bruto).strip()
    inicio, fim = limpo.find("{"), limpo.rfind("}")
    campos = None
    if inicio != -1 and fim > inicio:
        try:
            campos = json.loads(limpo[inicio : fim + 1])
        except json.JSONDecodeError:
            campos = None
    sinopse = (campos or {}).get("sinopse") if isinstance(campos, dict) else None
    if not sinopse:
        return ("recusa" if PADROES_RECUSA.search(limpo) else "erro"), None
    if PADROES_RECUSA.search(sinopse) and len(sinopse) < 300:
        return "recusa", None
    return "ok", campos


def _salvar(texto_id: str, modelo: str, status: str, campos: dict | None, bruto: str, segundos: float) -> None:
    campos = campos or {}
    with fonte.conectar_estado() as estado:
        estado.execute(
            "INSERT OR REPLACE INTO resumos (conto_id, modelo, status, sinopse, personagens, cenario, tom, temas, "
            "bruto, segundos, gerado_em) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                texto_id, modelo, status, campos.get("sinopse"),
                json.dumps(campos.get("personagens") or [], ensure_ascii=False),
                campos.get("cenario"), campos.get("tom"),
                json.dumps(campos.get("temas") or [], ensure_ascii=False),
                bruto, segundos, fonte.agora(),
            ),
        )


def resumir(modo: str, limite: int, provedor: str, refazer: bool, mostrar: int, db_path: str) -> None:
    modelo = "mock" if provedor == "mock" else LLM_MODELO
    ids = fonte.selecionar(modo, limite, db_path=db_path)
    if not refazer:
        with fonte.conectar_estado() as estado:
            feitos = {l["conto_id"] for l in estado.execute(
                "SELECT conto_id FROM resumos WHERE modelo = ? AND status = 'ok'", (modelo,)
            )}
        ids = [i for i in ids if i not in feitos]
    print(f"[resumo] modo={modo} provedor={provedor} modelo={modelo} · {len(ids)} textos a resumir")

    cliente = None
    if provedor == "openai":
        cabecalhos = {"Authorization": f"Bearer {LLM_API_KEY}"} if LLM_API_KEY else {}
        cliente = httpx.Client(base_url=LLM_URL, headers=cabecalhos, timeout=LLM_TIMEOUT_S)

    contagem = {"ok": 0, "recusa": 0, "erro": 0, "excluido": 0}
    tempos: list[float] = []
    try:
        for n, doc in enumerate(fonte.carregar(ids, db_path=db_path), 1):
            if fonte.excluido(doc.get("temas")):
                contagem["excluido"] += 1
                continue
            t = time.time()
            try:
                bruto = _chamar_mock(doc) if provedor == "mock" else _chamar_openai(cliente, doc)
                status, campos = interpretar(bruto)
            except httpx.HTTPError as e:
                bruto, status, campos = f"{type(e).__name__}: {e}", "erro", None
            segundos = time.time() - t
            tempos.append(segundos)
            contagem[status] += 1
            _salvar(doc["id"], modelo, status, campos, bruto, segundos)
            print(f"[resumo] {n}/{len(ids)} {texto['id']}: {status} ({segundos:.1f}s)", flush=True)
    except KeyboardInterrupt:
        print("\n[resumo] interrompido — rode de novo pra continuar (só refaz os que não deram ok)")
    finally:
        if cliente is not None:
            cliente.close()

    feitos = contagem["ok"] + contagem["recusa"] + contagem["erro"]
    print(f"\n[resumo] {contagem}")
    if feitos:
        print(f"[resumo] taxa de recusa: {contagem['recusa'] / feitos:.1%} · erro: {contagem['erro'] / feitos:.1%}")
    if tempos:
        media = sum(tempos) / len(tempos)
        with fonte.conectar_fonte(db_path) as conn:
            (total,) = conn.execute("SELECT COUNT(*) FROM contos WHERE origem = 'real'").fetchone()
        print(f"[resumo] {media:.1f}s/texto · base inteira ({total:,} textos) levaria ~{media * total / 3600:.0f}h "
              "nesse ritmo, sequencial")

    if mostrar:
        with fonte.conectar_estado() as estado:
            exemplos = estado.execute(
                "SELECT conto_id, sinopse, tom, temas FROM resumos WHERE modelo = ? AND status = 'ok' "
                "ORDER BY gerado_em DESC LIMIT ?", (modelo, mostrar)
            ).fetchall()
        for e in exemplos:
            print(f"\n--- {e['texto_id']} · tom={e['tom']} · temas={e['temas']}\n{e['sinopse']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Resumos estruturados via LLM")
    parser.add_argument("--modo", default=BUSCA_V2_MODO, choices=fonte.MODOS)
    parser.add_argument("--limite", type=int, default=LIMITE_PADRAO, help="só no modo amostra")
    parser.add_argument("--provedor", default=LLM_PROVEDOR, choices=("mock", "openai"))
    parser.add_argument("--refazer", action="store_true", help="refaz também os que já deram ok")
    parser.add_argument("--mostrar", type=int, default=3, help="imprime N resumos no final")
    parser.add_argument("--db-path", default=DB_PATH)
    args = parser.parse_args()
    try:
        resumir(args.modo, args.limite, args.provedor, args.refazer, args.mostrar, args.db_path)
    except fonte.ExtracaoIncompleta as e:
        sys.exit(f"[resumo] {e}")


if __name__ == "__main__":
    main()
