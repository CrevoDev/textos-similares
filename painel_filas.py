"""Painel do projeto (Streamlit): resumo das filas, busca v2 e os textos
descartados. A operação das filas — workers, erros, pausar, reenfileirar,
vazão — fica no painel do CrevoMQ (`crevomq-servidor`, CREVOMQ_URL), que lê
o mesmo FILA_DB_PATH; aqui só o que é específico dos textos.

A parte "ao vivo" roda num fragmento com `run_every`, então só ela se
atualiza sozinha; as abas embaixo ficam paradas enquanto o usuário
interage com elas."""

from datetime import datetime

import pandas as pd
import streamlit as st

import pipeline
import ui
from busca_v2 import painel as painel_v2
from config import CREVOMQ_URL, DB_PATH, MOCK_MODE
from db import conectar

NOMES_ESTAGIO = {"extracao": "Extração", "embedding": "Embeddings", "indexacao": "Indexação",
                 "remocao": "Remoção"}
ITENS_POR_PAGINA = 50

st.set_page_config(page_title="Filas — textos-similares", layout="wide", page_icon="⚙️")
ui.aplicar_estilo()
# A cada `run_every` o Streamlit esmaece os elementos do fragmento enquanto
# ele roda de novo — num painel que se atualiza sozinho isso vira um "pisca"
# constante. O pill "ao vivo" + "atualizado às" já indicam a atualização.
st.html("<style>[data-stale='true'] { opacity: 1 !important; transition: none !important; }</style>")


# ---------------------------------------------------------------- leitura (cache de 5s)

@st.cache_data(ttl=5, show_spinner=False)
def carregar_catalogo(db_path: str) -> dict[str, int]:
    with conectar(db_path) as conn:
        por_estagio = dict(conn.execute("SELECT estagio, COUNT(*) FROM contos GROUP BY estagio").fetchall())
        (descartados,) = conn.execute("SELECT COUNT(*) FROM descartados").fetchone()
    return {"concluido": por_estagio.get("concluido", 0),
            "andamento": sum(n for e, n in por_estagio.items() if e != "concluido"),
            "descartados": descartados}


@st.cache_data(ttl=5, show_spinner=False)
def carregar_filas() -> tuple[pd.DataFrame, list[dict]]:
    """Uma linha por estágio de cada fila, e os alertas do CrevoMQ."""
    linhas, alertas = [], []
    for nome, fila in pipeline.filas().items():
        stats = fila.estatisticas()
        alertas += [{**a, "fila": nome} for a in fila.alertas(stats)]
        for estagio, s in stats.items():
            linhas.append({
                "fila": nome, "estágio": NOMES_ESTAGIO.get(estagio, estagio),
                "pendentes": s["pendente"], "processando": s["processando"], "erro": s["erro"],
                "workers": s["workers_vivos"], "por min": s["avancos_min"],
                "tempo/item (s)": s["duracao_media_s"],
                "esvazia em": _fmt_duracao(s["eta_s"]) if s["backlog"] else "fila vazia",
                "pausado": "sim" if s["pausa"] else "",
            })
    return pd.DataFrame(linhas), alertas


@st.cache_data(ttl=30, show_spinner=False)
def carregar_motivos(db_path: str) -> pd.DataFrame:
    with conectar(db_path) as conn:
        linhas = conn.execute(
            "SELECT COALESCE(motivo, '(sem motivo)') AS motivo, COUNT(*) AS n FROM descartados "
            "GROUP BY 1 ORDER BY n DESC LIMIT 12"
        ).fetchall()
    return pd.DataFrame([dict(linha) for linha in linhas])


@st.cache_data(ttl=30, show_spinner=False)
def carregar_descartados(db_path: str, busca: str | None, limite: int, offset: int) -> tuple[int, pd.DataFrame]:
    where, params = "1=1", []
    if busca:
        where = "(id LIKE ? OR titulo LIKE ? OR autor LIKE ? OR motivo LIKE ?)"
        params = [f"%{busca}%"] * 4
    with conectar(db_path) as conn:
        (total,) = conn.execute(f"SELECT COUNT(*) FROM descartados WHERE {where}", params).fetchone()
        linhas = conn.execute(
            f"SELECT id, titulo, autor, motivo, descartado_em, url FROM descartados WHERE {where} "
            "ORDER BY descartado_em DESC, id LIMIT ? OFFSET ?",
            [*params, limite, offset],
        ).fetchall()
    return total, pd.DataFrame([dict(linha) for linha in linhas])


def _fmt_duracao(segundos: float | None) -> str:
    if segundos is None:
        return "—"
    segundos = int(segundos)
    d, resto = divmod(segundos, 86400)
    h, resto = divmod(resto, 3600)
    m, s = divmod(resto, 60)
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


# ---------------------------------------------------------------- barra lateral

with st.sidebar:
    st.html(
        '<div class="ui-eyebrow">⚙️ textos-similares</div>'
        '<p class="ui-sub" style="font-size:.85rem;margin-top:.3rem">Filas no CrevoMQ; catálogo e descartes no SQLite.</p>'
    )
    st.link_button("Abrir painel do CrevoMQ", CREVOMQ_URL, icon=":material/open_in_new:", width="stretch",
                   help="Workers, erros, pausar/retomar estágio, reenfileirar e vazão de cada fila.")
    st.divider()
    auto_refresh = st.toggle("Atualizar ao vivo", value=True, key="ao_vivo")
    intervalo = st.select_slider("Intervalo", [2, 5, 10, 15, 30], value=5, format_func=lambda s: f"{s}s",
                                 disabled=not auto_refresh)

    st.subheader("Ferramentas")
    with st.expander("Limpar dados mock", icon=":material/delete_sweep:"):
        st.caption("Apaga só `origem='mock'` (textos + embeddings). Nunca mexe em `origem='real'`.")
        confirmar_limpeza = st.checkbox("Confirmo a limpeza dos dados mock", key="confirma_limpeza_mock")
        if st.button("Limpar dados mock", disabled=not confirmar_limpeza, type="primary", width="stretch"):
            with conectar(DB_PATH) as conn:
                ids_mock = [
                    linha["id"] for linha in conn.execute("SELECT id FROM contos WHERE origem = 'mock'").fetchall()
                ]
                if ids_mock:
                    marcadores = ",".join("?" * len(ids_mock))
                    conn.execute(f"DELETE FROM embeddings WHERE conto_id IN ({marcadores})", ids_mock)
                conn.execute("DELETE FROM contos WHERE origem = 'mock'")
            st.cache_data.clear()
            st.toast(f"{ui.num(len(ids_mock))} itens mock removidos.", icon="🧹")


# ---------------------------------------------------------------- parte ao vivo

@st.fragment(run_every=intervalo if auto_refresh else None)
def painel_ao_vivo() -> None:
    modo = ui.pill("MOCK", "warn") if MOCK_MODE else ui.pill("REAL", "good")
    vivo = ui.pill(f"ao vivo · {intervalo}s", "accent", vivo=True) if auto_refresh else ui.pill("pausado", "")
    agora = datetime.now().strftime("%H:%M:%S")
    ui.hero(
        "Pipeline de coleta",
        "Descoberta → Extração → Embeddings → Indexação. Texto fora do interesse sai na extração "
        "e vai pra fila de deleção.",
        eyebrow="Filas",
        extra_eyebrow=f"&nbsp;{modo}{vivo}<span style='text-transform:none;letter-spacing:0;font-weight:500;"
                      f"color:var(--ui-faint)'>atualizado às {agora}</span>",
    )

    catalogo = carregar_catalogo(DB_PATH)
    df, alertas = carregar_filas()
    erros = int(df["erro"].sum()) if not df.empty else 0

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("No catálogo", ui.num(catalogo["concluido"]), border=True, height="stretch",
              help="Textos que atravessaram as filas e aparecem no app de busca.")
    k2.metric("Em andamento", ui.num(catalogo["andamento"]), border=True, height="stretch",
              help="Descobertos que ainda não chegaram ao catálogo (inclui os que estão em erro).")
    k3.metric("Descartados", ui.num(catalogo["descartados"]), border=True, height="stretch",
              help="Removidos pela regra de descarte (descarte.py); a descoberta não os baixa de novo.")
    k4.metric("Jobs em erro", ui.num(erros), border=True, height="stretch",
              help="Esgotaram as tentativas ou tiveram erro definitivo (ex.: 404). Reenfileire no painel do CrevoMQ.")

    if alertas:
        tons = {"critico": "crit", "aviso": "warn", "info": ""}
        st.html('<div style="display:flex;flex-wrap:wrap;gap:.4rem;margin:.6rem 0 .2rem">'
                + "".join(ui.pill(f"{a['fila']} · {a['mensagem']}", tons[a["nivel"]], ponto=False) for a in alertas)
                + "</div>")

    ui.secao("Filas", "Resumo por estágio. Para operar (pausar, reenfileirar, ver erros), use o painel do CrevoMQ.")
    if df.empty:
        ui.vazio("📭", "Nenhuma fila registrada", "Rode scripts/init_schema.py primeiro.")
        return
    st.dataframe(
        df, hide_index=True, width="stretch",
        column_config={
            "por min": st.column_config.NumberColumn(format="%.1f"),
            "tempo/item (s)": st.column_config.NumberColumn(format="%.2f"),
        },
    )


painel_ao_vivo()


@st.fragment(run_every=intervalo if auto_refresh else None)
def painel_busca_v2() -> None:
    painel_v2.render()


painel_busca_v2()


# ---------------------------------------------------------------- descartados (sem auto-refresh)

ui.secao("Descartados", "Textos removidos pela regra de descarte — sem atualização automática.")
motivos = carregar_motivos(DB_PATH)
if motivos.empty:
    ui.vazio("🧺", "Nenhum texto descartado ainda", "Simule com: uv run python scripts/descartar.py")
else:
    st.html(
        '<div style="display:flex;flex-wrap:wrap;gap:.4rem;margin:.25rem 0 .9rem">'
        + "".join(ui.pill(f"{ui.num(r.n)}× {r.motivo}", "", ponto=False) for r in motivos.itertuples())
        + "</div>"
    )
    busca = st.text_input("Buscar", key="descartados_busca", type="search", live="400ms", icon=":material/search:",
                          placeholder="id, título, autor ou tag…", label_visibility="collapsed")
    total, _ = carregar_descartados(DB_PATH, busca or None, 0, 0)
    pagina, total_paginas = ui.pagina_atual("pg_descartados", total, ITENS_POR_PAGINA, assinatura=busca)
    if total == 0:
        ui.vazio("🔎", "Nenhum descartado com essa busca")
    else:
        _, df_desc = carregar_descartados(DB_PATH, busca or None, ITENS_POR_PAGINA, (pagina - 1) * ITENS_POR_PAGINA)
        st.dataframe(
            df_desc, hide_index=True, width="stretch",
            column_config={
                "id": st.column_config.TextColumn("id", width="medium"),
                "titulo": st.column_config.TextColumn("título", width="large"),
                "motivo": "tag",
                "descartado_em": st.column_config.DatetimeColumn("descartado em", format="DD/MM HH:mm"),
                "url": st.column_config.LinkColumn("url", display_text="abrir ↗", width="small"),
            },
        )
        with st.container(horizontal=True, vertical_alignment="center"):
            st.space("stretch")
            ui.resumo_pagina(pagina, ITENS_POR_PAGINA, total, "descartados")
        ui.controles_paginacao("pg_descartados", pagina, total_paginas)
