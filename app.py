"""App de busca (Fase 6) — catálogo paginado, busca semântica/híbrida,
"mais parecidos", recomendação por perfil e favoritos. Lê o mesmo SQLite dos
workers; usa o Qdrant local só pelo tempo de uma busca (ver nota em
`_consultar_qdrant` sobre o lock compartilhado com o worker de indexação).

Navegação em 5 páginas (st.navigation no topo), com os filtros na barra
lateral valendo para todas elas e sincronizados com a URL. Três páginas
escondidas do menu: leitura do texto (`/ler?id=`), perfil do autor
(`/autor?nome=`) e série (`/serie?id=`), abertas pelo título, pelo nome do
autor e pelo "Série · parte N de M" nos cards."""

import json
import logging
import re

import streamlit as st

import pipeline
import ui
from busca import audio, catalogo, favoritos, leitura
from busca.filtros import invalidar_cache_ids
from busca.recomendar import SemFavoritos, busca_hibrida, recomendar_por_perfil
from busca.semantica import busca_semantica, busca_series, mais_parecidos, series_parecidas
from embeddings import enviar_qdrant

st.set_page_config(page_title="Textos Similares", layout="wide", page_icon="📖", initial_sidebar_state="expanded")
ui.aplicar_estilo()

# Resultados trazidos de uma vez do Qdrant e paginados localmente: uma ida só
# ao Qdrant por consulta (menos chance de colidir com o lock do worker) e a
# troca de página fica instantânea.
POOL_QDRANT = 120
POR_PAGINA_OPCOES = [12, 24, 48]
ESTRELAS_OPCOES = [0, 5, 10, 20, 50, 100, 250, 500]
ORDEM_ROTULOS = {"recentes": "Mais recentes", "estrelas": "Mais estrelados", "titulo": "Título (A–Z)", "antigos": "Mais antigos"}
SUGESTOES = [
    "reencontro com um amor antigo",
    "romance lento em cidade pequena",
    "suspense durante uma viagem",
    "segredo de família revelado",
]


# ---------------------------------------------------------------- dados (cache)

@st.cache_data(ttl=60, show_spinner=False)
def _resumo() -> dict:
    return catalogo.resumo()


@st.cache_data(ttl=300, show_spinner=False)
def _temas() -> list[tuple[str, int]]:
    return catalogo.temas_populares()


@st.cache_data(ttl=300, show_spinner=False)
def _autores() -> list[str]:
    return catalogo.autores()


@st.cache_data(ttl=30, show_spinner=False)
def _contar_catalogo(filtros_json: str, texto: str) -> int:
    return catalogo.contar(json.loads(filtros_json), texto)


@st.cache_data(ttl=30, show_spinner=False)
def _listar_catalogo(filtros_json: str, texto: str, ordem: str, limite: int, offset: int) -> list[dict]:
    return catalogo.listar(json.loads(filtros_json), texto, ordem, limite, offset)


@st.cache_data(ttl=300, show_spinner=False, max_entries=32)
def _texto_completo(texto_id: str) -> dict | None:
    return catalogo.obter_completo(texto_id)


@st.cache_data(ttl=60, show_spinner=False, max_entries=64)
def _perfil_autor(autor: str) -> dict | None:
    return catalogo.perfil_autor(autor)


@st.cache_data(ttl=300, show_spinner=False, max_entries=64)
def _capitulos_serie(serie_id: str) -> list[dict]:
    return catalogo.capitulos_serie(serie_id)


@st.cache_data(ttl=60, show_spinner=False)
def _resumo_series() -> dict:
    return catalogo.resumo_series()


@st.cache_data(ttl=30, show_spinner=False)
def _contar_series(filtros_json: str, texto: str) -> int:
    return catalogo.contar_series(json.loads(filtros_json), texto)


@st.cache_data(ttl=30, show_spinner=False)
def _listar_series(filtros_json: str, texto: str, ordem: str, limite: int, offset: int) -> list[dict]:
    return catalogo.listar_series(json.loads(filtros_json), texto, ordem, limite, offset)


@st.cache_data(ttl=300, show_spinner=False, max_entries=64)
def _serie(serie_id: str) -> dict | None:
    return catalogo.obter_series([serie_id]).get(serie_id)


@st.cache_data(ttl=300, show_spinner=False, max_entries=64)
def _consultar_qdrant(tipo: str, texto: str, filtros_json: str, texto_id: str, versao_perfil: tuple) -> list[dict]:
    """QdrantClient(path=...) local só permite um processo por vez — o worker
    de indexação fecha o cliente a cada job (ver embeddings/enviar_qdrant.py) pra
    abrir essa janela, mas ainda existe uma chance pequena de colisão. Exceção
    não entra no cache, então "tentar de novo" refaz a consulta de verdade.
    `versao_perfil` só existe pra invalidar o cache das recomendações quando
    os favoritos usados como base mudam."""
    filtros = json.loads(filtros_json) or None
    try:
        if tipo == "semantica":
            return busca_semantica(texto, limite=POOL_QDRANT, filtros=filtros)
        if tipo == "hibrida":
            return busca_hibrida(texto, limite=POOL_QDRANT, filtros=filtros)
        if tipo == "v2":
            # trechos do texto completo (busca_v2/), coleção da base inteira;
            # o card mostra o trecho que casou com a consulta
            from busca_v2.buscar import buscar as busca_v2

            return [{**r, "trecho": r.get("trecho_encontrado") or r.get("trecho")}
                    for r in busca_v2(texto, limite=POOL_QDRANT, filtros=filtros, modo="completo")]
        if tipo == "perfil":
            return recomendar_por_perfil(limite=POOL_QDRANT, filtros=filtros)
        if tipo == "parecidos":
            return mais_parecidos(texto_id, limite=POOL_QDRANT)
        if tipo == "series":
            return busca_series(texto, limite=POOL_QDRANT, filtros=filtros)
        if tipo == "series_parecidas":
            return series_parecidas(texto_id, limite=12)  # aqui `texto_id` é o id da série
        raise ValueError(tipo)
    finally:
        enviar_qdrant.fechar_client()


def _qdrant(tipo: str, *, texto: str = "", filtros: dict | None = None, texto_id: str = "",
            versao_perfil: tuple = ()) -> tuple[list[dict] | None, str | None]:
    """`SemFavoritos` propaga direto — não é uma falha do Qdrant, é um estado
    esperado (usuário ainda não marcou nenhum favorito)."""
    try:
        return _consultar_qdrant(tipo, texto, json.dumps(filtros or {}, sort_keys=True), texto_id, versao_perfil), None
    except SemFavoritos:
        raise
    except Exception as e:  # noqa: BLE001 — não sabemos o tipo exato que o backend local levanta pro lock
        return None, str(e)


def _erro_qdrant(detalhe: str, chave: str) -> None:
    with st.container(key=f"painel-erro-{chave}"):
        st.html(
            '<div class="ui-texto-top"><span class="ui-pill warn"><span class="ui-dot"></span>Qdrant ocupado</span></div>'
            '<p class="ui-sub" style="margin-top:.6rem">O índice local está em uso pelo worker de indexação neste '
            "instante. Isso costuma durar só alguns segundos.</p>"
        )
        c1, c2 = st.columns([1, 4], vertical_alignment="center")
        c1.button("Tentar de novo", icon=":material/refresh:", key=f"retry-{chave}", type="primary")
        c2.caption(f"Detalhe técnico: {detalhe[:180]}")


# ---------------------------------------------------------------- favoritos

def _alternar_favorito(texto_id: str, tipo: str) -> None:
    """Clicar de novo no mesmo botão desmarca."""
    if favoritos.obter(texto_id) == tipo:
        favoritos.desmarcar(texto_id)
    else:
        favoritos.marcar(texto_id, tipo)
        if tipo == "gostei":
            _pregerar_audio(texto_id, pipeline.PRIORIDADE_GOSTEI)


def _pregerar_audio(texto_id: str | None, prioridade: int) -> None:
    """Fila `audio` (Fase 11): o worker do supervisor gera o áudio antes de
    alguém apertar "Ouvir". Falha aqui (fila fora do ar) não atrapalha a página."""
    if not texto_id:
        return
    try:
        pipeline.enfileirar_audio(texto_id, prioridade)
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).exception("não enfileirou o áudio de %s", texto_id)


def _alternar_audio(texto_id: str) -> None:
    st.session_state["ouvindo"] = None if st.session_state.get("ouvindo") == texto_id else texto_id


def _player_audio(texto_id: str, texto: str, proxima: str | None = None) -> None:
    """Fase 11: pede a geração (thread em segundo plano, busca/audio.py) e
    mostra o player. Enquanto houver blocos por gerar, o fragmento se repete a
    cada 2s acrescentando os novos; ao terminar, um rerun da página desliga a
    repetição (senão ele continuaria reenviando os blocos para sempre)."""
    audio.iniciar(texto_id, texto)
    # quem está ouvindo uma série provavelmente vai querer a próxima parte
    _pregerar_audio(proxima, pipeline.PRIORIDADE_PROXIMA_PARTE)
    ui.player_audio(texto_id, audio.mapa(texto))
    e = audio.estado(texto_id)
    if not (e.completo or e.erro):
        st.session_state["_tts_poll"] = texto_id
    st.fragment(run_every=None if (e.completo or e.erro) else 2)(_blocos_audio)(texto_id, texto)


def _blocos_audio(texto_id: str, texto: str) -> None:
    e = audio.estado(texto_id)
    if not (e.completo or e.gerando or e.erro):
        # o worker da fila que estava gerando este texto parou (ou terminou
        # outro pedido do app): o app assume de onde ele parou
        audio.iniciar(texto_id, texto)
    with st.container(key="tts-blocos"):
        ui.status_audio(e.total, len(e.blocos), e.erro)
        for bloco in e.blocos:
            st.audio(str(bloco), format="audio/ogg")
    if (e.completo or e.erro) and st.session_state.get("_tts_poll") == texto_id:
        st.session_state["_tts_poll"] = None
        st.rerun(scope="app")


# ---------------------------------------------------------------- "mais parecidos" (diálogo)

def _abrir_parecidos(texto_id: str) -> None:
    st.session_state["parecidos_pilha"] = [texto_id]


def _navegar_parecidos(texto_id: str) -> None:
    st.session_state.setdefault("parecidos_pilha", []).append(texto_id)


def _voltar_parecidos() -> None:
    pilha = st.session_state.get("parecidos_pilha") or []
    if len(pilha) > 1:
        pilha.pop()


@st.dialog("Textos parecidos", width="large", icon=":material/auto_awesome:")
def dialogo_parecidos() -> None:
    pilha = st.session_state.get("parecidos_pilha") or []
    if not pilha:
        st.caption("Nada selecionado.")
        return
    texto_id = pilha[-1]
    origem = catalogo.obter(texto_id) or {"id": texto_id}

    if len(pilha) > 1:
        st.button("Voltar", icon=":material/arrow_back:", type="tertiary", key="dlg-voltar", on_click=_voltar_parecidos)
    titulo = origem.get("titulo") or texto_id
    st.html(
        f'<div class="ui-eyebrow">Parecidos com</div>'
        f'<div class="ui-texto-titulo" style="min-height:0;font-size:1.45rem;margin:.3rem 0 .2rem">{ui.esc(titulo)}</div>'
        f'<div class="ui-autor">por <b>{ui.esc(origem.get("autor") or "—")}</b></div>'
        f"{ui.chips(ui.temas_de(origem), maximo=8)}"
    )

    with st.spinner("Procurando histórias parecidas…"):
        resultados, erro = _qdrant("parecidos", texto_id=texto_id)
    if erro:
        _erro_qdrant(erro, "dlg")
        return
    if not resultados:
        ui.vazio("🔭", "Nada parecido por enquanto", "Esse texto pode ainda não ter sido indexado no Qdrant.")
        return

    por_pagina = 8
    pagina, total_paginas = ui.pagina_atual("pg_dlg", len(resultados), por_pagina, assinatura=texto_id)
    ui.ancora_topo("pg_dlg")
    ui.resumo_pagina(pagina, por_pagina, len(resultados), "textos parecidos")
    favs = favoritos.mapa()
    inicio = (pagina - 1) * por_pagina
    for item in resultados[inicio:inicio + por_pagina]:
        card_texto(item, "dlg", favs, score="similaridade", layout="lista", no_dialogo=True)
    ui.controles_paginacao("pg_dlg", pagina, total_paginas)


# ---------------------------------------------------------------- chips que filtram

def _adicionar_tema(chave: str, destino: str | None = None) -> None:
    """Callback dos chips: acrescenta o tema ao filtro da barra lateral. Roda
    antes dos widgets, então pode escrever em `temas` (o multiselect da
    sidebar). `destino` pede uma troca de página no próximo run — callbacks
    não podem chamar `st.switch_page`."""
    tema = st.session_state.get(chave)
    st.session_state[chave] = None
    if not tema:
        return
    atuais = list(st.session_state.get("temas") or [])
    if tema in atuais:
        st.toast(f"“{tema}” já está no filtro.", icon=":material/filter_alt:")
    else:
        st.session_state["temas"] = [*atuais, tema]
        st.toast(f"Filtro adicionado: {tema}", icon=":material/filter_alt:")
    if destino:
        st.session_state["_ir_para"] = destino


def chips_clicaveis(temas: list[str], chave: str, *, maximo: int | None = None, destino: str | None = None,
                    contagens: dict[str, int] | None = None) -> None:
    """Temas como `st.pills`: clicar adiciona o tema aos filtros. `chave`
    precisa começar com `chips-` (é o que o CSS de `ui.py` estiliza)."""
    opcoes = list(dict.fromkeys(temas))[:maximo]
    if not opcoes:
        return
    fmt = (lambda t: f"{t} · {ui.num(contagens[t])}") if contagens else None
    st.pills("Temas", opcoes, key=chave, label_visibility="collapsed", format_func=fmt,
             on_change=_adicionar_tema, args=(chave, destino))


def trocar_pagina(destino: str, **params: str) -> None:
    """`st.switch_page` sem `query_params` tenta limpar a URL e falha se houver
    filtro da barra lateral ativo (parâmetros com `bind="query-params"` não
    podem ser apagados nem escritos à mão). Com um dict — mesmo vazio — ele
    substitui a URL sem essa checagem, e os widgets ligados regravam os
    próprios valores a partir do session_state no run seguinte."""
    st.switch_page(PAGINAS[destino], query_params=params)


def _ir_para_pendente() -> None:
    """Troca de página pedida por um callback no run anterior."""
    destino = st.session_state.pop("_ir_para", None)
    if destino:
        trocar_pagina(destino)


# ---------------------------------------------------------------- card

def _trecho(item: dict, limite: int = 380) -> str:
    # " ".join(paragrafos) descola "ideia.E" -> "ideia. E" nos textos antigos
    texto = " ".join(leitura.paragrafos(item.get("trecho")))
    return texto[:limite].rsplit(" ", 1)[0] + "…" if len(texto) > limite else texto


def _html_topo(item: dict, score: str | None, rank: int | None) -> str:
    esquerda = ""
    if score == "similaridade" and item.get("score") is not None:
        pct = max(0, min(100, round(item["score"] * 100)))
        esquerda = (
            f'<span class="ui-match" title="similaridade cosseno {item["score"]:.3f}">'
            f'<span class="ui-match-bar"><i style="width:{pct}%"></i></span>{pct}% similar</span>'
        )
    elif rank:
        esquerda = f'<span class="ui-rank">#{rank} na busca</span>'

    meta = []
    if item.get("estrelas") is not None:
        meta.append(f'<span><span class="ui-star">★</span> {ui.num(item["estrelas"])}</span>')
    data = ui.data_curta(item.get("data_pub"))
    if data:
        meta.append(f"<span>{data}</span>")
    return f'<div class="ui-texto-top">{esquerda}<span class="ui-meta">{"<span>·</span>".join(meta)}</span></div>'


def link_autor(autor: str | None, chave: str) -> None:
    """"por **Nome**" levando ao perfil. `chave` vira a classe `st-key-autor-…`."""
    with st.container(key=f"autor-{chave}"):
        if autor:
            st.page_link(PAGINAS["autor"], label=f"por **{ui.md_esc(autor)}**", query_params={"nome": autor})
        else:
            st.html('<div class="ui-autor">por <b>—</b></div>')


def voltar(pagina, rotulo: str, chave: str, query_params: dict | None = None) -> None:
    """"← Página" no topo das páginas escondidas do menu, como migalha discreta."""
    with st.container(key=f"voltar-{chave}"):
        st.page_link(pagina, label=rotulo, icon=":material/arrow_back:", query_params=query_params or {})


def link_serie(item: dict, chave: str) -> None:
    """"Série · parte N de M" levando à página da série (nada fora de série)."""
    if not item.get("serie_id"):
        return
    with st.container(key=f"serielink-{chave}"):
        st.page_link(PAGINAS["serie"], label=f"Série · parte {item['serie_parte']} de {item['serie_total']}",
                     icon=":material/collections_bookmark:", query_params={"id": item["serie_id"]},
                     help=item.get("serie_nome"))


def _anos(serie: dict) -> str:
    a, b = (serie.get("primeira_pub") or "")[:4], (serie.get("ultima_pub") or "")[:4]
    return a if a == b else f"{a}–{b}"


SEGMENTOS_MAX = 24


def _barra_partes(n: int) -> str:
    """Uma marquinha por parte (até SEGMENTOS_MAX): o tamanho da série num
    relance, sem precisar ler o número."""
    visiveis = min(n, SEGMENTOS_MAX)
    resto = f'<span class="ui-serie-mais">+{n - visiveis}</span>' if n > visiveis else ""
    return f'<div class="ui-serie-barra">{"<i></i>" * visiveis}{resto}</div>'


def card_serie(serie: dict, ctx: str, *, score: bool = False) -> None:
    sid = serie["id"]
    # height="stretch": os cards de uma linha da grade ficam da mesma altura,
    # com as ações sempre no rodapé.
    with st.container(key=f"card-serie-{ctx}-{sid}", gap=None, height="stretch"):
        origem = "Série marcada pelo site" if serie["origem"] == "site" else "Série detectada pelo título das partes"
        esquerda = (f'<span class="ui-serie-partes" title="{origem}"><b>{ui.num(serie["n_partes"])}</b> partes</span>')
        if score and serie.get("score") is not None:
            pct = max(0, min(100, round(serie["score"] * 100)))
            esquerda += (f'<span class="ui-match"><span class="ui-match-bar"><i style="width:{pct}%"></i></span>'
                         f"{pct}%</span>")
        st.html(f'<div class="ui-texto-top">{esquerda}</div>{_barra_partes(serie["n_partes"])}')
        with st.container(key=f"titulo-serie-{ctx}-{sid}"):
            st.page_link(PAGINAS["serie"], label=ui.md_esc(serie["nome"]), query_params={"id": sid})
        link_autor(serie.get("autor"), f"serie-{ctx}-{sid}")
        meta = [_anos(serie)]
        if serie.get("estrelas"):
            meta.append(f'<span class="ui-star">★</span> {ui.num(serie["estrelas"])}')
        if serie.get("palavras"):
            meta.append(f'~{_minutos(serie["palavras"])} min')
        if serie.get("gostei"):
            meta.append(f'👍 {serie["gostei"]}')
        trecho = _trecho(serie, 170)
        st.html(f'<div class="ui-parte-meta">{"<span>·</span>".join(f"<span>{m}</span>" for m in meta if m)}</div>'
                f'<p class="ui-serie-sinopse">{ui.esc(trecho) if trecho else "<em>sem trecho</em>"}</p>'
                f"{ui.chips(ui.temas_de(serie), maximo=5)}")
        st.space("stretch")
        with st.container(horizontal=True, gap="small", vertical_alignment="center", key=f"acoes-serie-{ctx}-{sid}"):
            st.page_link(PAGINAS["serie"], label="Ver partes", icon=":material/format_list_numbered:",
                         query_params={"id": sid})
            if serie.get("primeiro_conto"):
                st.page_link(PAGINAS["ler"], label="Começar a ler", icon=":material/menu_book:",
                             query_params={"id": serie["primeiro_conto"]})


def grade_series(itens: list[dict], ctx: str, *, score: bool = False) -> None:
    for i in range(0, len(itens), 3):
        cols = st.columns(3, gap="medium")
        for j, serie in enumerate(itens[i:i + 3]):
            with cols[j]:
                card_serie(serie, ctx, score=score)


def card_texto(item: dict, ctx: str, favs: dict[str, str], *, score: str | None = None, rank: int | None = None,
               layout: str = "grade", no_dialogo: bool = False) -> None:
    cid = item["id"]
    lista = layout == "lista"
    with st.container(key=f"card-{'lista-' if lista else ''}{ctx}-{cid}", gap=None):
        st.html(_html_topo(item, score, rank))
        with st.container(key=f"titulo-{ctx}-{cid}"):
            st.page_link(PAGINAS["ler"], label=ui.md_esc(item.get("titulo") or "(sem título)"),
                         query_params={"id": cid})
        link_autor(item.get("autor"), f"{ctx}-{cid}")
        link_serie(item, f"{ctx}-{cid}")
        trecho = _trecho(item)
        st.html(f'<div class="{"ui-lista" if lista else ""}"><p class="ui-trecho">'
                f'{ui.esc(trecho) if trecho else "<em>sem trecho</em>"}</p></div>')
        # Dentro do diálogo (um fragmento) o callback não reroda a barra
        # lateral, então os chips de lá ficam só informativos.
        if no_dialogo:
            st.html(ui.chips(ui.temas_de(item), maximo=8))
        else:
            chips_clicaveis(ui.temas_de(item), f"chips-card-{ctx}-{cid}", maximo=8 if lista else 4)
        fav = favs.get(cid)
        with st.container(horizontal=True, gap="small", vertical_alignment="center", key=f"acoes-{ctx}-{cid}"):
            st.button("", icon=":material/thumb_up:", key=f"g-{ctx}-{cid}",
                      type="primary" if fav == "gostei" else "secondary",
                      help="Remover dos favoritos" if fav == "gostei" else "Gostei — usar como referência",
                      on_click=_alternar_favorito, args=(cid, "gostei"))
            st.button("", icon=":material/thumb_down:", key=f"n-{ctx}-{cid}",
                      type="primary" if fav == "nao_gostei" else "secondary",
                      help="Desfazer" if fav == "nao_gostei" else "Não gostei — evitar parecidos",
                      on_click=_alternar_favorito, args=(cid, "nao_gostei"))
            if no_dialogo:
                st.button("Parecidos", icon=":material/auto_awesome:", key=f"p-{ctx}-{cid}", type="tertiary",
                          on_click=_navegar_parecidos, args=(cid,))
            elif st.button("Parecidos", icon=":material/auto_awesome:", key=f"p-{ctx}-{cid}", type="tertiary",
                           on_click=_abrir_parecidos, args=(cid,)):
                dialogo_parecidos()
            st.page_link(PAGINAS["ler"], label="Ler", icon=":material/menu_book:", query_params={"id": cid})


def grade_textos(itens: list[dict], ctx: str, layout: str, *, score: str | None = None, rank_inicial: int | None = None) -> None:
    favs = favoritos.mapa()
    n_col = 3 if layout == "grade" else 1
    for i in range(0, len(itens), n_col):
        cols = st.columns(n_col, gap="medium")
        for j, item in enumerate(itens[i:i + n_col]):
            with cols[j]:
                rank = (rank_inicial + i + j) if rank_inicial is not None else None
                card_texto(item, ctx, favs, score=score, rank=rank, layout=layout)


def lista_paginada(itens: list[dict], ctx: str, *, assinatura: object, score: str | None = None,
                   rotulo: str = "resultados", com_rank: bool = False) -> None:
    """Pagina uma lista já carregada em memória (resultados do Qdrant/favoritos)."""
    layout, por_pagina = _preferencias_exibicao()
    pagina, total_paginas = ui.pagina_atual(f"pg_{ctx}", len(itens), por_pagina, assinatura=(assinatura, por_pagina))
    ui.ancora_topo(f"pg_{ctx}")
    ui.resumo_pagina(pagina, por_pagina, len(itens), rotulo)
    inicio = (pagina - 1) * por_pagina
    grade_textos(itens[inicio:inicio + por_pagina], ctx, layout, score=score,
                 rank_inicial=inicio + 1 if com_rank else None)
    ui.controles_paginacao(f"pg_{ctx}", pagina, total_paginas)


# ---------------------------------------------------------------- barra lateral / filtros

def _limpar_filtros(ano_min: int, ano_max: int) -> None:
    st.session_state["temas"] = []
    st.session_state["autor"] = None
    st.session_state["anos"] = (ano_min, ano_max)
    st.session_state["estrelas"] = 0
    st.session_state["so_series"] = False


def _barra_lateral(resumo: dict) -> dict:
    ano_min = int(resumo.get("ano_min") or 2002)
    ano_max = int(resumo.get("ano_max") or ano_min)
    with st.sidebar:
        st.html(
            '<div class="ui-eyebrow">📖 Textos Similares</div>'
            '<p class="ui-sub" style="font-size:.85rem;margin-top:.3rem">Busca semântica e recomendações '
            "sobre o seu índice local. Os filtros valem para todas as páginas.</p>"
        )
        st.divider()
        # Widgets com bind="query-params" ficam sem format_func: o bind grava
        # o rótulo formatado na URL, e um rótulo com contagem ou ícone viraria
        # um link ilegível (e deixaria de bater quando a contagem mudasse).
        # Só os temas populares viram opção (há dezenas de milhares), mas um
        # tema adicionado por um chip ou vindo da URL precisa existir na lista.
        opcoes_temas = [t for t, _ in _temas()]
        opcoes_temas += [t for t in st.session_state.get("temas") or [] if t not in opcoes_temas]
        temas = st.multiselect(
            "Temas", opcoes_temas, key="temas", bind="query-params",
            placeholder="Qualquer tema", help="Ordenados por popularidade. Mostra textos com qualquer um dos temas escolhidos.",
        )
        autor = st.selectbox("Autor", _autores(), index=None, key="autor", bind="query-params",
                             placeholder="Qualquer autor")
        anos = (ano_min, ano_max)
        if ano_max > ano_min:
            anos = st.select_slider("Publicado entre", options=list(range(ano_min, ano_max + 1)),
                                    value=(ano_min, ano_max), key="anos", bind="query-params")
        estrelas = st.select_slider("Estrelas mínimas ★", ESTRELAS_OPCOES, value=0, key="estrelas", bind="query-params")
        so_series = st.toggle("Só textos de séries", key="so_series", bind="query-params",
                              help="Mostra só textos que são parte de uma série (do site ou detectada pelo título).")
        filtros = {
            "temas": list(temas) or None,
            "autor": autor or None,
            "ano_min": anos[0] if anos[0] > ano_min else None,
            "ano_max": anos[1] if anos[1] < ano_max else None,
            "estrelas_min": estrelas or None,
            "so_series": so_series or None,
        }
        filtros = {k: v for k, v in filtros.items() if v}
        n_ativos = len(filtros)
        st.button(
            f"Limpar filtros ({n_ativos})" if n_ativos else "Sem filtros ativos",
            icon=":material/filter_alt_off:", disabled=not n_ativos, width="stretch",
            on_click=_limpar_filtros, args=(ano_min, ano_max),
        )
        st.divider()
        if st.button("Recarregar índice", icon=":material/sync:", type="tertiary",
                     help="Relê o SQLite e o mapa de IDs do Qdrant — use quando o pipeline indexou textos novos."):
            invalidar_cache_ids()
            st.cache_data.clear()
            st.toast("Índice recarregado — textos novos já aparecem.", icon="✅")
    return filtros


def _filtros_ativos(filtros: dict) -> None:
    partes = []
    for tema in filtros.get("temas") or []:
        partes.append(ui.pill(tema, "accent", ponto=False))
    if filtros.get("autor"):
        partes.append(ui.pill(f"autor: {filtros['autor']}", "accent", ponto=False))
    if filtros.get("ano_min") or filtros.get("ano_max"):
        partes.append(ui.pill(f"{filtros.get('ano_min') or '…'}–{filtros.get('ano_max') or '…'}", "accent", ponto=False))
    if filtros.get("estrelas_min"):
        partes.append(ui.pill(f"≥ {filtros['estrelas_min']} ★", "accent", ponto=False))
    if filtros.get("so_series"):
        partes.append(ui.pill("só séries", "accent", ponto=False))
    if partes:
        st.html('<div style="display:flex;flex-wrap:wrap;gap:.4rem;align-items:center;margin:.1rem 0 .6rem">'
                '<span class="ui-resumo-pagina" style="margin-right:.2rem">Filtros:</span>' + "".join(partes) + "</div>")


def _preferencias_exibicao() -> tuple[str, int]:
    return st.session_state.get("exibicao") or "grade", st.session_state.get("por_pagina") or POR_PAGINA_OPCOES[0]


def _controles_exibicao(col_layout, col_qtd) -> None:
    col_layout.segmented_control(
        "Exibição", ["grade", "lista"], default="grade", required=True, key="exibicao", persist_state="session",
        format_func=lambda v: ":material/grid_view:" if v == "grade" else ":material/view_agenda:",
        label_visibility="collapsed",
    )
    col_qtd.selectbox("Por página", POR_PAGINA_OPCOES, key="por_pagina", persist_state="session",
                      format_func=lambda n: f"{n} por página", label_visibility="collapsed")


# ---------------------------------------------------------------- páginas

def pagina_explorar() -> None:
    resumo = _resumo()
    ui.hero(
        "Explorar",
        "Todo o acervo já indexado, do mais novo ao mais antigo. Filtre, ordene e abra os parecidos de qualquer texto.",
        eyebrow="Biblioteca",
        stats=[
            (ui.num(resumo.get("total")), "contos"),
            (ui.num(resumo.get("autores")), "autores"),
            (ui.num(len(_temas())), "temas"),
            (f"{resumo.get('ano_min') or '—'}–{resumo.get('ano_max') or '—'}", "publicação"),
        ],
    )
    c_kw, c_ordem, c_layout, c_qtd = st.columns([5, 2, 1.1, 1.6], vertical_alignment="center")
    kw = c_kw.text_input(
        "Palavra-chave", key="kw", bind="query-params", type="search", live="400ms",
        placeholder="Filtrar por palavra no título, trecho ou tema…", icon=":material/search:",
        label_visibility="collapsed",
    )
    ordem = c_ordem.selectbox("Ordenar", list(ORDEM_ROTULOS), format_func=ORDEM_ROTULOS.get, key="ordem",
                              bind="query-params", label_visibility="collapsed")
    _controles_exibicao(c_layout, c_qtd)

    _filtros_ativos(FILTROS)
    catalogo_paginado(FILTROS, kw or "", ordem, "exp")


def catalogo_paginado(filtros: dict, kw: str, ordem: str, ctx: str, *, total_sem_filtro: int | None = None) -> None:
    """Lista do catálogo SQL com paginação real (LIMIT/OFFSET) — Explorar e
    perfil do autor. `total_sem_filtro` mostra quantos itens os filtros
    escondem (perfil: "12 de 859 textos do autor")."""
    layout, por_pagina = _preferencias_exibicao()
    filtros_json = json.dumps(filtros, sort_keys=True)
    total = _contar_catalogo(filtros_json, kw)
    pagina, total_paginas = ui.pagina_atual(f"pg_{ctx}", total, por_pagina,
                                            assinatura=(filtros_json, kw, ordem, por_pagina))
    itens = _listar_catalogo(filtros_json, kw, ordem, por_pagina, (pagina - 1) * por_pagina)

    if not itens:
        ui.vazio("🗂️", "Nenhum texto por aqui", "Nada combina com a palavra-chave e os filtros atuais. "
                 "Tente afrouxar os filtros na barra lateral.")
        return
    ui.ancora_topo(f"pg_{ctx}")
    ui.resumo_pagina(pagina, por_pagina, total, "contos")
    if total_sem_filtro and total < total_sem_filtro:
        st.caption(f"Os filtros ativos escondem {ui.num(total_sem_filtro - total)} de "
                   f"{ui.num(total_sem_filtro)} textos.")
    grade_textos(itens, ctx, layout)
    ui.controles_paginacao(f"pg_{ctx}", pagina, total_paginas)


def _usar_sugestao() -> None:
    escolha = st.session_state.get("sugestao")
    if escolha:
        st.session_state["q"] = escolha
    st.session_state["sugestao"] = None


MODOS_BUSCA = {"semantica": ":material/psychology: Semântica", "hibrida": ":material/join_inner: Híbrida",
               "v2": ":material/segment: Trechos (v2)"}


def pagina_buscar() -> None:
    ui.hero(
        "O que você quer ler hoje?",
        "Descreva o clima, o enredo ou o tipo de personagem — a busca semântica encontra histórias pelo sentido, "
        "não só pelas palavras.",
        eyebrow="Busca",
    )
    with st.container(key="busca-principal"):
        texto = st.text_input(
            "Busca", key="q", bind="query-params", type="search", icon=":material/search:",
            placeholder="ex.: romance lento em cidade pequena", label_visibility="collapsed",
        )
    c_modo, _, c_layout, c_qtd = st.columns([4.6, 1.3, 1.1, 1.6], vertical_alignment="center")
    modo = c_modo.segmented_control(
        "Modo", ["semantica", "hibrida", "v2"], default="semantica", required=True, key="modo", persist_state="session",
        format_func=MODOS_BUSCA.get,
        help="Semântica: só pelo sentido. Híbrida: combina sentido + palavras exatas (RRF). "
             "Trechos (v2): procura no texto completo e mostra o trecho que casou.",
        label_visibility="collapsed",
    )
    _controles_exibicao(c_layout, c_qtd)
    _filtros_ativos(FILTROS)

    if not texto:
        st.pills("Experimente", SUGESTOES, key="sugestao", on_change=_usar_sugestao)
        return

    with st.spinner("Buscando…"):
        resultados, erro = _qdrant(modo, texto=texto, filtros=FILTROS)
    if erro:
        _erro_qdrant(erro, "busca")
        return
    if not resultados:
        ui.vazio("🔍", "Nenhum resultado", "Tente descrever de outro jeito ou remover alguns filtros.")
        return
    hibrida = modo == "hibrida"
    lista_paginada(resultados, "busca", assinatura=(texto, modo, json.dumps(FILTROS, sort_keys=True)),
                   score=None if hibrida else "similaridade", com_rank=hibrida)


def pagina_para_voce() -> None:
    favs = favoritos.mapa()
    n_gostei = sum(1 for t in favs.values() if t == "gostei")
    n_nao = len(favs) - n_gostei
    ui.hero(
        "Para você",
        "Recomendações montadas a partir do que você marcou: aproxima dos 👍 e afasta dos 👎.",
        eyebrow="Recomendações",
        stats=[(ui.num(n_gostei), "gostei"), (ui.num(n_nao), "não gostei")],
    )
    if not favs:
        ui.vazio("✨", "Ainda não sei do que você gosta",
                 "Marque 👍 ou 👎 em alguns textos em Explorar ou Buscar e as recomendações aparecem aqui.")
        return

    # As recomendações usam uma "foto" dos favoritos: marcar 👍 aqui mesmo não
    # reembaralha a lista debaixo do cursor — o usuário atualiza quando quiser.
    # A foto é renovada toda vez que o usuário chega nesta página.
    versao_atual = tuple(sorted(favs.items()))
    if "perfil_versao" not in st.session_state or st.session_state.get("_trocou_pagina"):
        st.session_state["perfil_versao"] = versao_atual
    c_info, c_layout, c_qtd = st.columns([5.9, 1.1, 1.6], vertical_alignment="center")
    with c_info:
        if st.session_state["perfil_versao"] != versao_atual:
            if st.button("Seus favoritos mudaram — atualizar recomendações", icon=":material/refresh:", type="primary"):
                st.session_state["perfil_versao"] = versao_atual
                st.rerun()
    _controles_exibicao(c_layout, c_qtd)
    _filtros_ativos(FILTROS)

    try:
        with st.spinner("Montando recomendações…"):
            resultados, erro = _qdrant("perfil", filtros=FILTROS, versao_perfil=st.session_state["perfil_versao"])
    except SemFavoritos:
        resultados, erro = [], None
    if erro:
        _erro_qdrant(erro, "perfil")
        return
    if not resultados:
        ui.vazio("🧭", "Nenhuma recomendação com esses filtros", "Remova alguns filtros na barra lateral.")
        return
    lista_paginada(resultados, "perfil", assinatura=(st.session_state["perfil_versao"], json.dumps(FILTROS, sort_keys=True)),
                   score="similaridade", rotulo="recomendações")


def pagina_favoritos() -> None:
    todos = favoritos.listar()
    n_gostei = sum(1 for f in todos if f["tipo"] == "gostei")
    ui.hero(
        "Favoritos",
        "Tudo o que você marcou. É daqui que saem as recomendações da página Para você.",
        eyebrow="Sua coleção",
        stats=[(ui.num(len(todos)), "marcados"), (ui.num(n_gostei), "gostei"), (ui.num(len(todos) - n_gostei), "não gostei")],
    )
    if not todos:
        ui.vazio("❤️", "Nenhum favorito ainda", "Use os botões 👍 / 👎 nos cards de Explorar e Buscar.")
        return

    rotulos = {"todos": f"Todos · {len(todos)}", "gostei": f"Gostei · {n_gostei}",
               "nao_gostei": f"Não gostei · {len(todos) - n_gostei}"}
    c_tipo, c_busca, c_layout, c_qtd = st.columns([3.2, 2.7, 1.1, 1.6], vertical_alignment="center")
    tipo = c_tipo.segmented_control("Tipo", list(rotulos), format_func=rotulos.get, default="todos",
                                    required=True, key="fav_tipo", label_visibility="collapsed")
    busca = c_busca.text_input("Filtrar", key="fav_busca", type="search", live="250ms", icon=":material/search:",
                               placeholder="Filtrar por título ou autor…", label_visibility="collapsed")
    _controles_exibicao(c_layout, c_qtd)

    itens = [f for f in todos if tipo == "todos" or f["tipo"] == tipo]
    if FILTROS.get("so_series"):
        itens = [f for f in itens if f.get("serie_id")]
    if busca:
        termo = busca.casefold()
        itens = [f for f in itens if termo in (f.get("titulo") or "").casefold() or termo in (f.get("autor") or "").casefold()]
    if not itens:
        ui.vazio("🔎", "Nada com esse filtro")
        return
    lista_paginada(itens, "fav", assinatura=(tipo, busca), rotulo="favoritos")


# ---------------------------------------------------------------- leitura

TAMANHOS_FONTE = {"p": "A−", "m": "A", "g": "A+"}
MODOS_LEITURA = {"paginas": ":material/auto_stories: Páginas", "rolagem": ":material/swap_vert: Rolagem"}
TEMAS_LEITURA = {"escuro": ":material/dark_mode: Escuro", "sepia": ":material/coffee: Sépia", "claro": ":material/light_mode: Claro"}


def _secao_serie(doc: dict) -> tuple[list[dict], int]:
    """Partes da série e a posição do texto atual nela (-1 se não achar)."""
    if not doc.get("serie_id"):
        return [], -1
    capitulos = _capitulos_serie(doc["serie_id"])
    posicao = next((i for i, c in enumerate(capitulos) if c["id"] == doc["id"]), -1)
    return capitulos, posicao


def _corpo_html(paragrafos: list[str]) -> str:
    """Parágrafos do texto. A letra capitular só entra quando o texto começa
    com letra (aspas antes dela tudo bem): num "— fala" de abertura, o
    ::first-letter pega só o travessão e a capitular vira um traço solto."""
    saida = []
    for i, p in enumerate(paragrafos):
        capitular = i == 0 and p.lstrip("“\"'«( ")[:1].isalpha()
        # data-p: índice do parágrafo, usado pelo destaque do áudio (audio.mapa)
        saida.append(f'<p class="capitular" data-p="{i}">{ui.esc(p)}</p>' if capitular
                     else f'<p data-p="{i}">{ui.esc(p)}</p>')
    return "".join(saida)


def _barra_posicao(total: int, posicao: int) -> str:
    """Faixa da série com a parte atual acesa e as anteriores meio acesas
    (só até SEGMENTOS_MAX partes — acima disso, o "parte X de N" basta)."""
    if total > SEGMENTOS_MAX:
        return ""
    marcas = "".join(f'<i class="{"atual" if i == posicao else "antes" if posicao > i else ""}"></i>'
                     for i in range(total))
    return f'<div class="ui-serie-barra ui-serie-posicao">{marcas}</div>'


def _cabecalho_serie(doc: dict, total: int, posicao: int) -> None:
    """No lugar do eyebrow "Texto": a série (link pra página dela), a posição
    e a faixa das partes."""
    with st.container(key="serie-cabecalho", gap=None):
        st.page_link(PAGINAS["serie"], label=f"Série · {ui.md_esc(texto['serie_nome'])} · parte {posicao + 1} de {total}",
                     icon=":material/collections_bookmark:", query_params={"id": doc["serie_id"]},
                     help="Ver todas as partes")
        st.html(_barra_posicao(total, posicao))


def _navegacao_serie(doc: dict, capitulos: list[dict], posicao: int) -> None:
    """Depois do texto: parte anterior e próxima em dois cartões clicáveis
    por inteiro; na última parte, um cartão de fim de série."""
    texto_id = doc["id"]
    if len(capitulos) < 2 or posicao < 0:
        return
    total = len(capitulos)
    ui.secao("Continue a série", f"{texto['serie_nome']} · parte {posicao + 1} de {total}")
    with st.container(key="serie-nav", horizontal=True, gap="medium"):
        if posicao > 0:
            ant = capitulos[posicao - 1]
            with st.container(key="serie-ant", gap=None):
                st.html(f'<div class="ui-nav-rotulo">← Parte {posicao} · anterior</div>')
                st.page_link(PAGINAS["ler"], label=ui.md_esc(ant["titulo"] or f"Parte {posicao}"),
                             query_params={"id": ant["id"]})
        else:
            st.space("stretch")
        if posicao < total - 1:
            prox = capitulos[posicao + 1]
            with st.container(key="serie-prox", gap=None):
                minutos = f" · ~{_minutos(prox['palavras'])} min" if prox.get("palavras") else ""
                st.html(f'<div class="ui-nav-rotulo">Próxima · parte {posicao + 2}{minutos} →</div>')
                st.page_link(PAGINAS["ler"], label=ui.md_esc(prox["titulo"] or f"Parte {posicao + 2}"),
                             query_params={"id": prox["id"]})
        else:
            with st.container(key="serie-fim", gap=None):
                # "Fim da série" seria mentira nas que ainda estão saindo: só
                # sabemos que é a última parte que chegou ao acervo.
                st.html('<div class="ui-nav-rotulo">Última parte no acervo ✦</div>')
                st.page_link(PAGINAS["serie"], label="Ver todas as partes e séries parecidas",
                             query_params={"id": doc["serie_id"]})
    with st.container(horizontal=True, vertical_alignment="center", gap="small", key="serie-pular"):
        ids = [c["id"] for c in capitulos]
        titulos = {c["id"]: f"{i + 1}. {c['titulo'] or '(sem título)'}" for i, c in enumerate(capitulos)}
        escolha = st.selectbox("Ir para a parte", ids, index=posicao, format_func=titulos.get,
                               key=f"capitulo-{texto_id}", label_visibility="collapsed")
        st.page_link(PAGINAS["serie"], label="Todas as partes", icon=":material/format_list_numbered:",
                     query_params={"id": doc["serie_id"]})
    if escolha != texto_id:
        trocar_pagina("ler", id=escolha)


def pagina_ler() -> None:
    _ir_para_pendente()
    texto_id = st.query_params.get("id") or ""
    doc = _texto_completo(texto_id) if texto_id else None
    if not doc:
        ui.vazio("📕", "Texto não encontrado", "O link pode estar incompleto ou o texto ainda não foi coletado.")
        st.page_link(PAGINAS["explorar"], label="Voltar ao acervo", icon=":material/arrow_back:")
        return

    # Um page_link para a própria página (próxima parte, parecido…) troca o
    # conteúdo mas não atualiza a URL; sem isso, o rerun seguinte (um 👍, por
    # exemplo) releria o id antigo e voltaria para o texto anterior.
    st.query_params["id"] = texto_id
    if st.session_state.get("_lendo") != texto_id:
        st.session_state["_lendo"] = texto_id
        ui.rolar_topo(texto_id)
    ui.progresso_leitura()
    ui.modo_leitura()

    texto =doc.get("texto_completo") or ""
    capitulos, posicao = _secao_serie(doc)
    with st.container(key="leitor", gap=None):
        voltar(PAGINAS["explorar"], "Acervo", "ler")
        if doc.get("serie_id") and len(capitulos) > 1 and posicao >= 0:
            _cabecalho_serie(doc, len(capitulos), posicao)
            eyebrow = ""
        else:
            eyebrow = '<div class="ui-eyebrow">Texto</div>'
        st.html(f'<div class="ui-hero" style="margin-bottom:.4rem">{eyebrow}'
                f'<div class="ui-title">{ui.esc(texto.get("titulo") or "(sem título)")}</div></div>')
        link_autor(doc.get("autor"), "leitor")

        meta = []
        data = ui.data_curta(doc.get("data_pub"))
        if data:
            meta.append(data)
        if doc.get("estrelas") is not None:
            meta.append(f'<span class="ui-star">★</span> {ui.num(texto["estrelas"])}')
        if texto:
            meta.append(f"~{leitura.tempo_leitura(texto)} min de leitura")
            meta.append(f"{ui.num(len(texto.split()))} palavras")
        st.html('<div class="ui-leitura-meta">' + "<span>·</span>".join(f"<span>{m}</span>" for m in meta) + "</div>")
        st.space("small")
        chips_clicaveis(ui.temas_de(doc), f"chips-ler-{texto_id}", destino="explorar")
        st.space("small")

        fav = favoritos.mapa().get(texto_id)
        with st.container(horizontal=True, gap="small", vertical_alignment="center", key="barra-leitura"):
            st.button("Gostei", icon=":material/thumb_up:", key="ler-g",
                      type="primary" if fav == "gostei" else "secondary",
                      on_click=_alternar_favorito, args=(texto_id, "gostei"))
            st.button("", icon=":material/thumb_down:", key="ler-n",
                      type="primary" if fav == "nao_gostei" else "secondary",
                      help="Desfazer" if fav == "nao_gostei" else "Não gostei — evitar parecidos",
                      on_click=_alternar_favorito, args=(texto_id, "nao_gostei"))
            if texto:
                st.button("Ouvir", icon=":material/headphones:", key="ler-ouvir",
                          type="primary" if st.session_state.get("ouvindo") == texto_id else "secondary",
                          help="Fechar o áudio" if st.session_state.get("ouvindo") == texto_id else "Ouvir o texto em voz alta",
                          on_click=_alternar_audio, args=(texto_id,))
            if doc.get("url"):
                st.link_button("Abrir no site", doc["url"], icon=":material/open_in_new:",
                               icon_position="right", type="tertiary")
        if texto and st.session_state.get("ouvindo") == texto_id:
            proxima = capitulos[posicao + 1]["id"] if 0 <= posicao < len(capitulos) - 1 else None
            _player_audio(texto_id, texto, proxima)
        with st.container(horizontal=True, gap="small", vertical_alignment="center", key="ajustes-leitura"):
            st.segmented_control("Modo", list(MODOS_LEITURA), format_func=MODOS_LEITURA.get, default="paginas",
                                 required=True, key="leitura_modo", persist_state="session",
                                 label_visibility="collapsed")
            st.segmented_control("Cor do papel", list(TEMAS_LEITURA), format_func=TEMAS_LEITURA.get,
                                 default="escuro", required=True, key="leitura_tema", persist_state="session",
                                 label_visibility="collapsed")
            st.segmented_control("Tamanho do texto", list(TAMANHOS_FONTE), format_func=TAMANHOS_FONTE.get,
                                 default="m", required=True, key="leitura_fonte", persist_state="session",
                                 label_visibility="collapsed")

        tamanho = st.session_state.get("leitura_fonte") or "m"
        tema = st.session_state.get("leitura_tema") or "escuro"
        modo = st.session_state.get("leitura_modo") or "paginas"
        classes = f"ui-leitura tam-{tamanho}"
        if texto:
            corpo = _corpo_html(leitura.paragrafos(texto))
        else:
            st.html('<div class="ui-aviso">O texto completo deste texto não foi coletado — abaixo, só o início. '
                    "Use “Abrir no site” para ler inteiro.</div>")
            corpo = _corpo_html([doc.get("trecho") or ""])
        if modo == "paginas":
            ui.folha_paginada(corpo, classes, tema, marca=f"{texto_id}-{tamanho}-{tema}")
        else:
            st.html(f'<div class="ui-folha tema-{tema}"><article class="{classes}">{corpo}</article>'
                    '<div class="ui-leitura-fim">✦ ✦ ✦</div></div>')

        _navegacao_serie(doc, capitulos, posicao)

    _rodape_leitura(doc)


def _rodape_leitura(doc: dict) -> None:
    texto_id, autor = doc["id"], doc.get("autor")
    if autor:
        # Partes da mesma série já estão na navegação logo acima.
        mais = _listar_catalogo(json.dumps({"autor": autor}), "", "estrelas", 12, 0)
        outros = [c for c in mais if c["id"] != texto_id
                  and not (doc.get("serie_id") and c.get("serie_id") == doc["serie_id"])][:3]
        if outros:
            perfil = _perfil_autor(autor)
            ui.secao(f"Mais de {autor}", f"{ui.num(perfil['total'])} textos no acervo" if perfil else "")
            grade_textos(outros, "mais-autor", "grade")
            st.page_link(PAGINAS["autor"], label="Ver perfil completo", icon=":material/person:",
                         query_params={"nome": autor})

    ui.secao("Parecidos com este", "pelo sentido do texto")
    resultados, erro = _qdrant("parecidos", texto_id=texto_id)
    if erro and "not found" in erro.lower():  # texto ainda não chegou ao Qdrant
        st.caption("Este texto ainda não está no índice de similaridade.")
        return
    if erro:
        _erro_qdrant(erro, "ler")
        return
    if not resultados:
        st.caption("Nada parecido por enquanto — o texto pode ainda não estar no índice.")
        return
    grade_textos(resultados[:6], "ler-parecidos", "grade", score="similaridade")
    if len(resultados) > 6 and st.button("Ver todos os parecidos", icon=":material/auto_awesome:", type="tertiary",
                                         on_click=_abrir_parecidos, args=(texto_id,)):
        dialogo_parecidos()


# ---------------------------------------------------------------- perfil do autor

def _buscar_no_autor(autor: str) -> None:
    st.session_state["autor"] = autor
    st.session_state["_ir_para"] = "buscar"


SERIES_POR_PAGINA_AUTOR = 6


def _atividade(por_ano: list[tuple[str, int]]) -> str:
    """Mini-gráfico de barras: textos publicados por ano (anos sem texto
    entram zerados, pra pausa longa aparecer como pausa)."""
    if len(por_ano) < 2:
        return ""
    contagem = dict(por_ano)
    anos = list(range(int(por_ano[0][0]), int(por_ano[-1][0]) + 1))
    maximo = max(contagem.values())
    barras = "".join(
        f'<i style="height:{max(4, round(contagem.get(str(a), 0) / maximo * 100)) if contagem.get(str(a)) else 0}%"'
        f' title="{a}: {ui.num(contagem.get(str(a), 0))} textos"></i>'
        for a in anos
    )
    return (f'<div class="ui-atividade"><div class="ui-atividade-barras">{barras}</div>'
            f'<div class="ui-atividade-eixo"><span>{anos[0]}</span><span>textos por ano</span>'
            f'<span>{anos[-1]}</span></div></div>')


def pagina_autor() -> None:
    _ir_para_pendente()
    nome = st.query_params.get("nome") or ""
    perfil = _perfil_autor(nome) if nome else None
    if not perfil:
        ui.vazio("👤", "Autor não encontrado", "Nenhum texto deste autor está no acervo indexado.")
        st.page_link(PAGINAS["explorar"], label="Voltar ao acervo", icon=":material/arrow_back:")
        return

    st.query_params["nome"] = nome  # ver nota em pagina_ler
    # Estado do perfil (busca, tipo, ordem, páginas) é por autor: zera ao trocar.
    if st.session_state.get("_perfil_de") != nome:
        st.session_state["_perfil_de"] = nome
        for chave in ("kw_autor", "ordem_autor", "tipo_autor"):
            st.session_state.pop(chave, None)
        ui.rolar_topo(f"autor-{nome}")

    voltar(PAGINAS["explorar"], "Acervo", "autor")
    periodo = perfil["ano_min"] if perfil["ano_min"] == perfil["ano_max"] else f"{perfil['ano_min']}–{perfil['ano_max']}"
    stats = [(ui.num(perfil["total"]), "contos" if perfil["total"] != 1 else "conto")]
    if perfil["n_series"]:
        stats.append((ui.num(perfil["n_series"]), "séries" if perfil["n_series"] != 1 else "série"))
    stats += [(ui.num(perfil["estrelas"] or 0), "estrelas recebidas"), (periodo or "—", "publicando")]
    with st.container(key="autor-topo", horizontal=True, vertical_alignment="bottom", gap="large"):
        with st.container(key="autor-hero", gap=None):
            ui.hero(nome, "Tudo o que este autor publicou e já está no seu acervo.", eyebrow="Autor", stats=stats)
        atividade = _atividade(perfil["por_ano"])
        if atividade:
            st.html(atividade, width="content")

    with st.container(key="autor-acoes", horizontal=True, vertical_alignment="center", gap="small"):
        if nome in _autores():
            st.button("Buscar só nos textos deste autor", icon=":material/manage_search:",
                      on_click=_buscar_no_autor, args=(nome,))
        if perfil["grafias"]:
            st.html('<span class="ui-resumo-pagina">Também publica como</span>', width="content")
            for i, (grafia, n) in enumerate(perfil["grafias"]):
                with st.container(key=f"grafia-{i}"):
                    st.page_link(PAGINAS["autor"], label=f"{ui.md_esc(grafia)} · {ui.num(n)}",
                                 query_params={"nome": grafia}, help="Mesmo nome escrito de outro jeito")

    if perfil["temas"]:
        ui.secao("Temas frequentes", "clique para filtrar")
        chips_clicaveis([t for t, _ in perfil["temas"]], "chips-autor", contagens=dict(perfil["temas"]))

    series = perfil["series"]
    if series:
        n = len(series)
        ui.secao("Séries", f"{ui.num(n)} {'séries' if n != 1 else 'série'} · "
                           f"{ui.num(perfil['em_series'])} textos · da mais recente à mais antiga")
        pagina, total_paginas = ui.pagina_atual("pg_series_autor", n, SERIES_POR_PAGINA_AUTOR, assinatura=nome)
        inicio = (pagina - 1) * SERIES_POR_PAGINA_AUTOR
        grade_series(series[inicio:inicio + SERIES_POR_PAGINA_AUTOR], "autor")
        if total_paginas > 1:
            ui.controles_paginacao("pg_series_autor", pagina, total_paginas)

    ui.secao("Textos", "")
    tipos = {"todos": f"Todos {ui.num(perfil['total'])}", "avulsos": f"Avulsos {ui.num(perfil['avulsos'])}",
             "series": f"Em séries {ui.num(perfil['em_series'])}"}
    c_tipo, c_kw, c_ordem, c_layout, c_qtd = st.columns([3.7, 2.7, 1.8, 1.1, 1.6], vertical_alignment="center")
    tipo = c_tipo.segmented_control("Tipo", list(tipos) if perfil["em_series"] else ["todos"], format_func=tipos.get,
                                    default="todos", required=True, key="tipo_autor", label_visibility="collapsed",
                                    help="Avulsos: textos que não fazem parte de nenhuma série.")
    kw = c_kw.text_input("Palavra-chave", key="kw_autor", type="search", live="400ms", icon=":material/search:",
                         placeholder="Filtrar os textos deste autor…", label_visibility="collapsed")
    ordem = c_ordem.selectbox("Ordenar", list(ORDEM_ROTULOS), format_func=ORDEM_ROTULOS.get, key="ordem_autor",
                              label_visibility="collapsed")
    _controles_exibicao(c_layout, c_qtd)

    # Filtros da barra lateral continuam valendo; o autor da barra não (a
    # página já é de um autor).
    globais = {k: v for k, v in FILTROS.items() if k != "autor"}
    _filtros_ativos(globais)
    filtros = {**globais, "autor": nome, **({"so_avulsos": True} if tipo == "avulsos" else {}),
               **({"so_series": True} if tipo == "series" else {})}
    total_sem_filtro = {"todos": perfil["total"], "avulsos": perfil["avulsos"], "series": perfil["em_series"]}[tipo]
    catalogo_paginado(filtros, kw or "", ordem, "autor", total_sem_filtro=total_sem_filtro if globais or kw else None)


# ---------------------------------------------------------------- séries

ORDEM_SERIES_ROTULOS = {"recentes": "Atualizadas há pouco", "partes": "Mais partes", "estrelas": "Mais estreladas",
                        "titulo": "Nome (A–Z)"}
MODOS_SERIES = {"nome": ":material/title: Nome ou autor", "assunto": ":material/psychology: Pelo assunto"}


def pagina_series() -> None:
    resumo = _resumo_series()
    ui.hero(
        "Séries",
        "Histórias em várias partes — as que o site marca como série e as detectadas pelo título "
        "(“Parte 2”, “Cap. 3”, “T2 – EP 06”...). Abra uma para ler em ordem.",
        eyebrow="Coleção",
        stats=[(ui.num(resumo["series"]), "séries"), (ui.num(resumo["contos"]), "textos em séries"),
               (ui.num(resumo["do_site"]), "do site"), (ui.num(resumo["do_titulo"]), "detectadas")],
    )
    if not resumo["series"]:
        ui.vazio("📚", "Nenhuma série ainda", "Rode: uv run python scripts/series.py --executar --drenar")
        return
    c_busca, c_modo, c_ordem = st.columns([4.2, 2.8, 1.8], vertical_alignment="center")
    texto = c_busca.text_input("Buscar séries", key="q_series", bind="query-params", type="search", live="400ms",
                               icon=":material/search:", label_visibility="collapsed",
                               placeholder="nome da série ou autor… (ou descreva a história)")
    modo = c_modo.segmented_control("Modo", list(MODOS_SERIES), format_func=MODOS_SERIES.get, default="nome",
                                    required=True, key="modo_series", persist_state="session",
                                    label_visibility="collapsed",
                                    help="Nome ou autor: busca por palavra. Pelo assunto: semântica, sobre o vetor "
                                         "médio das partes de cada série.")
    ordem = c_ordem.selectbox("Ordenar", list(ORDEM_SERIES_ROTULOS), format_func=ORDEM_SERIES_ROTULOS.get,
                              key="ordem_series", label_visibility="collapsed", persist_state="session",
                              disabled=modo == "assunto" and bool(texto))
    # O "← Séries" da página de uma série volta com a mesma busca.
    st.session_state["_series_voltar"] = {"q_series": texto} if texto else {}
    filtros = {k: v for k, v in FILTROS.items() if k != "so_series"}
    _filtros_ativos(filtros)

    if modo == "assunto" and texto:
        with st.spinner("Procurando séries…"):
            resultados, erro = _qdrant("series", texto=texto, filtros=filtros)
        if erro:
            _erro_qdrant(erro, "series")
            return
        if not resultados:
            ui.vazio("🔍", "Nenhuma série sobre isso", "Tente descrever de outro jeito ou remover filtros.")
            return
        por_pagina = 12
        pagina, total_paginas = ui.pagina_atual("pg_series_q", len(resultados), por_pagina,
                                                assinatura=(texto, json.dumps(filtros, sort_keys=True)))
        ui.ancora_topo("pg_series_q")
        ui.resumo_pagina(pagina, por_pagina, len(resultados), "séries")
        inicio = (pagina - 1) * por_pagina
        grade_series(resultados[inicio:inicio + por_pagina], "q", score=True)
        ui.controles_paginacao("pg_series_q", pagina, total_paginas)
        return

    busca_nome = texto if modo == "nome" else ""
    filtros_json = json.dumps(filtros, sort_keys=True)
    por_pagina = 24
    total = _contar_series(filtros_json, busca_nome)
    pagina, total_paginas = ui.pagina_atual("pg_series", total, por_pagina, assinatura=(filtros_json, busca_nome, ordem))
    itens = _listar_series(filtros_json, busca_nome, ordem, por_pagina, (pagina - 1) * por_pagina)
    if not itens:
        ui.vazio("🗂️", "Nenhuma série com esses filtros", "Tente afrouxar a busca ou os filtros da barra lateral.")
        return
    ui.ancora_topo("pg_series")
    ui.resumo_pagina(pagina, por_pagina, total, "séries")
    grade_series(itens, "lista")
    ui.controles_paginacao("pg_series", pagina, total_paginas)


def _minutos(palavras: int) -> int:
    return max(1, round(palavras / leitura.PALAVRAS_POR_MINUTO))


def lista_partes(partes: list[dict], favs: dict[str, str]) -> None:
    """Uma linha por parte: número (destacado se você marcou 👍 ou 👎),
    título, data · estrelas · tempo de leitura, começo do texto e "Ler".
    Tudo via st.page_link, pra navegar sem recarregar a página."""
    for p in partes:
        cid, fav = p["id"], favs.get(p["id"])
        with st.container(key=f"linha-parte-{cid}", horizontal=True, vertical_alignment="center", gap="medium"):
            classe = {"gostei": " gostei", "nao_gostei": " nao-gostei"}.get(fav, "")
            dica = {"gostei": "Você marcou 👍", "nao_gostei": "Você marcou 👎"}.get(fav, f"Parte {p['parte']}")
            st.html(f'<div class="ui-parte-num{classe}" title="{dica}">{p["parte"]}</div>', width="content")
            with st.container(key=f"corpo-parte-{cid}", gap=None):
                st.page_link(PAGINAS["ler"], label=ui.md_esc(p["titulo"] or "(sem título)"),
                             query_params={"id": cid}, width="stretch")
                meta = [ui.data_curta(p["data_pub"]),
                        f'<span class="ui-star">★</span> {ui.num(p["estrelas"] or 0)}']
                if p["palavras"]:
                    meta.append(f'~{_minutos(p["palavras"])} min · {ui.num(p["palavras"])} palavras')
                trecho = _trecho(p, 200)
                st.html(f'<div class="ui-parte-meta">{"<span>·</span>".join(f"<span>{m}</span>" for m in meta if m)}'
                        f'</div>{f"<p class=ui-parte-trecho>{ui.esc(trecho)}</p>" if trecho else ""}')
            st.page_link(PAGINAS["ler"], label="Ler", icon=":material/menu_book:", query_params={"id": cid},
                         width="content")


def pagina_serie() -> None:
    _ir_para_pendente()
    serie_id = st.query_params.get("id") or ""
    serie = _serie(serie_id) if serie_id else None
    if not serie:
        ui.vazio("📚", "Série não encontrada", "Ela pode ter sido recalculada (partes novas ou removidas).")
        st.page_link(PAGINAS["series"], label="Ver todas as séries", icon=":material/arrow_back:")
        return
    st.query_params["id"] = serie_id  # ver nota em pagina_ler
    # Vindo da lista (ou de uma série parecida), a rolagem da página anterior
    # continuaria valendo e o topo — com o "← Séries" — ficaria fora de vista.
    if st.session_state.get("_vendo_serie") != serie_id:
        st.session_state["_vendo_serie"] = serie_id
        ui.rolar_topo(f"serie-{serie_id}")
    partes = _capitulos_serie(serie_id)
    origem = "Série do site" if serie["origem"] == "site" else "Série detectada pelo título"
    stats = [(ui.num(len(partes)), "partes"), (_anos(serie) or "—", "publicação")]
    if serie.get("estrelas"):
        stats.append((ui.num(serie["estrelas"]), "estrelas somadas"))
    voltar(PAGINAS["series"], "Séries", "serie", st.session_state.get("_series_voltar") or {})
    ui.hero(serie["nome"], "", eyebrow=origem, stats=stats)
    link_autor(serie["autor"], "serie-hero")
    if serie.get("temas"):
        chips_clicaveis(ui.temas_de(serie), "chips-serie", maximo=10, destino="explorar")
    if partes:
        st.page_link(PAGINAS["ler"], label="Começar pela parte 1", icon=":material/menu_book:",
                     query_params={"id": partes[0]["id"]})

    total_palavras = sum(p["palavras"] or 0 for p in partes)
    nota = "na ordem de leitura"
    if total_palavras:
        nota += f" · ~{_minutos(total_palavras)} min no total"
    ui.secao("Partes", nota)
    lista_partes(partes, favoritos.mapa())

    ui.secao("Séries parecidas", "pelo assunto das partes")
    resultados, erro = _qdrant("series_parecidas", texto_id=serie_id)
    if erro:
        st.caption("Esta série ainda não está no índice de similaridade.")
        return
    if resultados:
        grade_series(resultados[:6], "parecidas", score=True)
    else:
        st.caption("Nada parecido por enquanto.")


# ---------------------------------------------------------------- montagem

PAGINAS = {
    "explorar": st.Page(pagina_explorar, title="Explorar", icon=":material/explore:", default=True),
    "buscar": st.Page(pagina_buscar, title="Buscar", icon=":material/search:", url_path="buscar"),
    "para_voce": st.Page(pagina_para_voce, title="Para você", icon=":material/auto_awesome:", url_path="para-voce"),
    "series": st.Page(pagina_series, title="Séries", icon=":material/collections_bookmark:", url_path="series"),
    "favoritos": st.Page(pagina_favoritos, title="Favoritos", icon=":material/favorite:", url_path="favoritos"),
    # Fora do menu: abertas pelos links dos cards (título → leitura, autor →
    # perfil, "Série · parte N de M" → série).
    "ler": st.Page(pagina_ler, title="Leitura", url_path="ler", visibility="hidden"),
    "autor": st.Page(pagina_autor, title="Autor", url_path="autor", visibility="hidden"),
    "serie": st.Page(pagina_serie, title="Série", url_path="serie", visibility="hidden"),
}
pagina = st.navigation(list(PAGINAS.values()), position="top")
st.session_state["_trocou_pagina"] = st.session_state.get("_pagina_atual") != pagina.url_path
st.session_state["_pagina_atual"] = pagina.url_path
FILTROS = _barra_lateral(_resumo())
pagina.run()
