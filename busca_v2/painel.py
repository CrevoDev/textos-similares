"""Seção "Busca v2" do painel (painel_filas.py): em que ponto está a
indexação na GPU (exportar -> gerar -> importar), as coleções de trechos,
a normalização das tags e os resumos.

Só lê: busca_v2.db e a pasta do pacote (BUSCA_V2_GPU_PASTA). A geração roda
na máquina alugada, então daqui só dá pra ver o que já voltou pra `saida/` —
o andamento lá fica no `tail -f log.txt` da própria máquina."""

import json
from pathlib import Path

import streamlit as st

import ui
from busca_v2 import fonte
from config import BUSCA_V2_GPU_PASTA

COR = {"exportar": "#3987e5", "gerar": "#d95926", "importar": "#199e70"}


def _ler_json(caminho: Path) -> dict | None:
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def carregar_status(pasta: Path = Path(BUSCA_V2_GPU_PASTA)) -> dict:
    entrada, saida = pasta / "entrada", pasta / "saida"
    manifesto = _ler_json(entrada / "manifesto.json") or _ler_json(saida / "manifesto.json")
    progresso = _ler_json(pasta / "progresso.json")
    partes = [p for p in saida.glob("parte-*.npz") if ".tmp" not in p.name] if saida.exists() else []
    tamanho_mb = sum(f.stat().st_size for f in entrada.iterdir()) / 1e6 if entrada.exists() else 0.0

    with fonte.conectar_estado() as estado:
        colecoes = [dict(l) for l in estado.execute(
            "SELECT colecao, COUNT(*) AS contos, SUM(n_trechos) AS trechos, SUM(motivo IS NULL) AS indexados, "
            "SUM(motivo = 'excluido') AS excluidos, MAX(indexado_em) AS ultimo FROM trechos_indexados "
            "GROUP BY colecao ORDER BY colecao"
        )]
        resumos = [dict(l) for l in estado.execute(
            "SELECT modelo, status, COUNT(*) AS n, AVG(segundos) AS seg FROM resumos GROUP BY modelo, status"
        )]
        tem_tags = estado.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'tags_canonicas'"
        ).fetchone()
        tags = dict(estado.execute(
            "SELECT COUNT(*) AS originais, COUNT(DISTINCT canonica) AS canonicas, SUM(metodo = 'lexico') AS lexico, "
            "SUM(metodo = 'semantico') AS semantico FROM tags_canonicas"
        ).fetchone()) if tem_tags else None

    importados = None
    if manifesto:
        col = next((c for c in colecoes if c["colecao"] == manifesto["colecao"]), None)
        importados = (col or {}).get("indexados") or 0

    if not entrada.exists() and not manifesto:
        etapa = "sem_pacote"
    elif not manifesto:
        etapa = "exportando"
    elif not partes:
        etapa = "aguardando_gpu"
    elif not (saida / "CONCLUIDO").exists():
        etapa = "recebendo"
    elif importados < manifesto["n_contos"]:
        etapa = "importar"
    else:
        etapa = "concluido"

    return {
        "pasta": str(pasta), "etapa": etapa, "manifesto": manifesto, "progresso": progresso,
        "tamanho_mb": tamanho_mb, "partes": len(partes), "concluido_gpu": (saida / "CONCLUIDO").exists(),
        "importados": importados, "colecoes": colecoes, "resumos": resumos, "tags": tags,
    }


ETAPAS = {
    "sem_pacote": ("Nenhum pacote exportado", "", "Rode busca_v2/exportar.py --modo completo."),
    "exportando": ("Exportando", "accent", "Gerando o pacote a partir do contos.db."),
    "aguardando_gpu": ("Pronto pra GPU", "warn", "Envie entrada/ pra máquina alugada e rode gerar_vetores.py (LEIAME.txt)."),
    "recebendo": ("Vetores parciais", "warn", "Só parte da saida/ voltou — a geração terminou? Traga a pasta inteira."),
    "importar": ("Pronto pra importar", "accent", "Rode busca_v2/importar.py com o Qdrant ligado."),
    "concluido": ("Concluído", "good", "Tudo importado. Pode apagar a pasta do pacote (tem o texto dos textos)."),
}


def _pct(feito: int | None, total: int | None) -> float:
    return min(100.0, 100 * (feito or 0) / total) if total else 0.0


def _card(chave: str, num: str, nome: str, desc: str, valor: str, rotulo: str, pct: float, linhas: list[tuple[str, str]]) -> None:
    grade = "".join(f"<div><span>{ui.esc(k)}</span><b>{ui.esc(v)}</b></div>" for k, v in linhas)
    with st.container(key=f"painel-v2-{chave}", height="stretch"):
        st.html(
            f'<div style="height:3px;width:40px;border-radius:3px;background:{COR[chave]};margin-bottom:.8rem"></div>'
            f'<div class="ui-estagio-head"><span class="ui-estagio-num">PASSO {num}</span></div>'
            f'<div class="ui-estagio-nome">{nome}</div>'
            f'<div class="ui-resumo-pagina">{desc}</div>'
            f'<div class="ui-estagio-fila">{valor}<small>{rotulo}</small></div>'
            f'<div class="ui-grade" style="grid-template-columns:1fr;margin:.6rem 0 .5rem">'
            f'<div><span>Progresso · {pct:.0f}%</span><div class="ui-ocupacao"><i style="width:{pct:.1f}%"></i></div></div></div>'
            f'<div class="ui-grade">{grade}</div>'
        )


def render() -> None:
    s = carregar_status()
    m = s["manifesto"] or {}
    titulo, tom, dica = ETAPAS[s["etapa"]]
    ui.secao("Busca v2", "Indexação por trechos na GPU alugada: exportar → gerar → importar.")
    st.html(f'<div style="margin:-.3rem 0 .8rem">{ui.pill(titulo, tom)} '
            f'<span class="ui-resumo-pagina">{ui.esc(dica)}</span></div>')

    n = m.get("n_contos")
    prog = s["progresso"] or {}
    exportados = n if n is not None else prog.get("feitos", 0)
    total_export = n if n is not None else prog.get("total")
    c1, c2, c3 = st.columns(3, gap="medium")
    with c1:
        _card("exportar", "01", "Exportar", "contos.db → pacote (sua máquina)",
              ui.num(exportados), "contos", 100.0 if n is not None else _pct(prog.get("feitos"), prog.get("total")),
              [("Pacote", f"{s['tamanho_mb']:.0f} MB" if s["tamanho_mb"] else "—"),
               ("Partes", ui.num(m.get("n_partes")) if m else "—"),
               ("Coleção", (m.get("colecao") or "—").split("__")[0]),
               ("Criado", ui.data_curta(m.get("criado_em")) if m else "—")])
    with c2:
        _card("gerar", "02", "Gerar vetores", "Na GPU alugada · aqui só o que voltou",
              f"{ui.num(s['partes'])}/{ui.num(m.get('n_partes') or 0)}", "partes recebidas",
              _pct(s["partes"], m.get("n_partes")),
              [("Modelo", (m.get("modelo") or "—").split("/")[-1]),
               ("Trecho", f"{m['trecho_tokens']} tokens" if m else "—"),
               ("Terminou", "sim" if s["concluido_gpu"] else "não"),
               ("Textos/parte", ui.num(m.get("contos_por_parte")) if m else "—")])
    with c3:
        _card("importar", "03", "Importar", "Vetores → Qdrant (sua máquina)",
              ui.num(s["importados"] or 0), "textos indexados", _pct(s["importados"], n),
              [("Esperado", ui.num(n) if n is not None else "—"),
               ("Faltam", ui.num(max(0, (n or 0) - (s["importados"] or 0))) if n is not None else "—"),
               ("Pasta", s["pasta"]), ("", "")])

    col_a, col_b = st.columns(2, gap="medium")
    with col_a:
        st.html('<div class="ui-kpi-label" style="margin:.8rem 0 .4rem">Coleções de trechos</div>')
        if s["colecoes"]:
            st.dataframe(
                [{"coleção": c["colecao"], "contos": c["indexados"], "trechos": c["trechos"],
                  "excluídos": c["excluidos"], "última": ui.data_curta(c["ultimo"])} for c in s["colecoes"]],
                hide_index=True, width="stretch",
            )
        else:
            st.caption("Nenhuma coleção indexada ainda.")
    with col_b:
        st.html('<div class="ui-kpi-label" style="margin:.8rem 0 .4rem">Tags e resumos</div>')
        t = s["tags"]
        linhas = []
        if t:
            linhas.append(f"Tags: {ui.num(t['originais'])} grafias → {ui.num(t['canonicas'])} canônicas "
                          f"({ui.num(t['lexico'])} fundidas por grafia)")
        else:
            linhas.append("Tags: ainda não normalizadas (busca_v2/tags.py)")
        if s["resumos"]:
            por_modelo: dict[str, dict] = {}
            for r in s["resumos"]:
                por_modelo.setdefault(r["modelo"], {})[r["status"]] = r["n"]
            for modelo, cont in por_modelo.items():
                feitos = sum(cont.values())
                linhas.append(f"Resumos ({modelo}): {ui.num(cont.get('ok', 0))} ok · "
                              f"{ui.num(cont.get('recusa', 0))} recusas ({cont.get('recusa', 0) / feitos:.0%}) · "
                              f"{ui.num(cont.get('erro', 0))} erros")
        else:
            linhas.append("Resumos: nenhum gerado ainda")
        st.html("".join(f'<div class="ui-resumo-pagina" style="margin:.25rem 0">{ui.esc(l)}</div>' for l in linhas))
