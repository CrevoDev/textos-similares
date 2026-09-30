"""Design system compartilhado pelos dois apps Streamlit (busca/lista em
`app.py` e painel das filas em `painel_filas.py`): CSS global, componentes em
HTML (hero, chips, pills, cards de estatística, estados vazios) e o controle de
paginação. O tema base (cores, fontes, raio) fica em `.streamlit/config.toml`;
aqui só entra o que o tema não cobre."""

import html
import json
import math
import re
from datetime import datetime

import streamlit as st

MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]

CSS = """
<style>
@import url("https://fonts.googleapis.com/css2?family=Literata:ital,opsz,wght@0,7..72,400;0,7..72,600;1,7..72,400&display=swap");
:root {
  --ui-bg: #0b0c10;
  --ui-card: #12141b;
  --ui-card-hover: #151722;
  --ui-border: rgba(255, 255, 255, 0.07);
  --ui-border-strong: rgba(255, 255, 255, 0.13);
  --ui-text: #e8e6f0;
  --ui-muted: #a09eb0;
  --ui-faint: #6f6d7d;
  --ui-accent: #8b7cf6;
  --ui-accent-soft: rgba(139, 124, 246, 0.14);
  --ui-accent-2: #f472b6;
  --ui-good: #0ca30c;
  --ui-warn: #fab219;
  --ui-crit: #d03b3b;
  --ui-neutral: #8a8a86;
}

/* ---------- estrutura ---------- */
.stApp {
  background:
    radial-gradient(1100px 520px at 8% -12%, rgba(139, 124, 246, 0.13), transparent 62%),
    radial-gradient(900px 480px at 100% -8%, rgba(244, 114, 182, 0.07), transparent 60%),
    var(--ui-bg);
}
[data-testid="stHeader"] { background: transparent; }
/* O cabeçalho com a navegação no topo tem ~3.5rem e fica por cima do
   conteúdo: o respiro tem que vir daqui, não da margem de cada elemento
   (o "← Séries" das páginas de detalhe, sem margem própria, ficava cortado). */
[data-testid="stMainBlockContainer"] { padding-top: 4rem; padding-bottom: 4rem; max-width: 1240px; }
[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] { padding-top: 1.25rem; }
hr { border-color: var(--ui-border) !important; }

/* ---------- navegação no topo ---------- */
[data-testid="stTopNavLinkContainer"] a,
[data-testid="stNavLink"] { border-radius: 999px !important; }

/* ---------- hero ---------- */
.ui-hero { margin: 0 0 1.25rem 0; }

/* "← Página" no topo das páginas de detalhe (série, leitura) */
[class*="st-key-voltar-"] { margin-bottom: .35rem; }
[class*="st-key-voltar-"] [data-testid="stElementContainer"] { margin: 0 !important; }
[class*="st-key-voltar-"] a {
  padding: .2rem .7rem .2rem .45rem !important; min-height: 0 !important; width: auto !important;
  border: 1px solid var(--ui-border) !important; border-radius: 999px !important; background: rgba(255,255,255,.02) !important;
}
[class*="st-key-voltar-"] a p { font-size: .8rem !important; font-weight: 600; color: var(--ui-muted) !important; }
[class*="st-key-voltar-"] a:hover { border-color: rgba(139, 124, 246, .45) !important; background: var(--ui-accent-soft) !important; }
[class*="st-key-voltar-"] a:hover p { color: #e0dbff !important; }
.ui-eyebrow {
  display: inline-flex; align-items: center; gap: .5rem;
  font-size: .75rem; font-weight: 600; letter-spacing: .08em; text-transform: uppercase;
  color: var(--ui-muted);
}
.ui-title {
  font-family: "Fraunces", serif; font-weight: 600; font-size: 2.5rem; line-height: 1.1;
  letter-spacing: -0.02em; margin: .35rem 0 .4rem 0; padding: 0;
  background: linear-gradient(92deg, #f3f1ff 0%, #c9c1ff 55%, #f5a8d0 100%);
  -webkit-background-clip: text; background-clip: text; color: transparent;
}
.ui-sub { color: var(--ui-muted); font-size: 1rem; margin: 0; max-width: 46rem; }
.ui-stats { display: flex; flex-wrap: wrap; gap: .5rem 1.5rem; margin-top: 1rem; }
.ui-stat { display: flex; align-items: baseline; gap: .4rem; }
.ui-stat b { font-size: 1.05rem; font-weight: 600; color: var(--ui-text); font-variant-numeric: tabular-nums; }
.ui-stat span { font-size: .85rem; color: var(--ui-faint); }

.ui-section { display: flex; align-items: baseline; justify-content: space-between; gap: 1rem; margin: 1.75rem 0 .75rem; }
.ui-section h3 { font-family: "Fraunces", serif; font-size: 1.3rem; font-weight: 600; margin: 0; padding: 0; }
.ui-section p { color: var(--ui-faint); font-size: .85rem; margin: 0; }

/* ---------- pills / badges ---------- */
.ui-pill {
  display: inline-flex; align-items: center; gap: .4rem; white-space: nowrap;
  padding: .2rem .6rem; border-radius: 999px; font-size: .75rem; font-weight: 600;
  border: 1px solid var(--ui-border-strong); color: var(--ui-muted); background: rgba(255,255,255,.03);
}
.ui-pill .ui-dot { width: .45rem; height: .45rem; border-radius: 50%; background: currentColor; }
.ui-pill.good { color: #3fcf3f; border-color: rgba(12,163,12,.35); background: rgba(12,163,12,.10); }
.ui-pill.warn { color: var(--ui-warn); border-color: rgba(250,178,25,.35); background: rgba(250,178,25,.10); }
.ui-pill.crit { color: #f06a6a; border-color: rgba(208,59,59,.4); background: rgba(208,59,59,.12); }
.ui-pill.accent { color: #c2b9ff; border-color: rgba(139,124,246,.4); background: var(--ui-accent-soft); }
.ui-pill.live .ui-dot { animation: ui-pulse 1.6s ease-out infinite; }
@keyframes ui-pulse {
  0% { box-shadow: 0 0 0 0 currentColor; }
  70% { box-shadow: 0 0 0 6px transparent; }
  100% { box-shadow: 0 0 0 0 transparent; }
}

/* ---------- cards (containers com key "card-...") ---------- */
[class*="st-key-card-"] {
  background: linear-gradient(180deg, rgba(255,255,255,.025), rgba(255,255,255,0) 40%), var(--ui-card);
  border: 1px solid var(--ui-border);
  border-radius: 18px;
  padding: 1.1rem 1.15rem .7rem;
  transition: border-color .18s ease, transform .18s ease, box-shadow .18s ease, background .18s ease;
}
[class*="st-key-card-"]:hover {
  border-color: rgba(139, 124, 246, .38);
  transform: translateY(-2px);
  box-shadow: 0 14px 40px -18px rgba(139, 124, 246, .45);
}
[class*="st-key-painel-"] {
  background: var(--ui-card);
  border: 1px solid var(--ui-border);
  border-radius: 18px;
  padding: 1.1rem 1.2rem 1rem;
}

.ui-texto-top { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: .3rem .75rem; min-height: 1.4rem; }
.ui-meta { white-space: nowrap; color: var(--ui-faint); font-size: .8rem; display: inline-flex; gap: .55rem; align-items: center; font-variant-numeric: tabular-nums; }
.ui-meta .ui-star { color: #f4c152; }
.ui-rank { color: var(--ui-faint); font-size: .78rem; font-weight: 600; font-variant-numeric: tabular-nums; }
.ui-match { white-space: nowrap; display: inline-flex; align-items: center; gap: .45rem; font-size: .75rem; font-weight: 600; color: #c2b9ff; }
.ui-match-bar { width: 40px; height: 5px; border-radius: 999px; background: rgba(255,255,255,.08); overflow: hidden; }
.ui-match-bar i { display: block; height: 100%; border-radius: 999px; background: linear-gradient(90deg, var(--ui-accent), var(--ui-accent-2)); }

a.ui-texto-titulo, .ui-texto-titulo {
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
  min-height: 2.6em; margin: .55rem 0 .2rem;
  font-family: "Fraunces", serif; font-size: 1.2rem; font-weight: 600; line-height: 1.3;
  color: var(--ui-text) !important; text-decoration: none !important;
}
a.ui-texto-titulo:hover { color: #cfc8ff !important; }
.ui-autor { font-size: .85rem; color: var(--ui-muted); margin-bottom: .6rem; }
.ui-autor b { color: var(--ui-text); font-weight: 600; }
.ui-trecho {
  display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden;
  min-height: 4.5em; font-size: .88rem; line-height: 1.5; color: #b9b7c6; margin: 0 0 .8rem;
}
.ui-lista .ui-trecho { -webkit-line-clamp: 2; min-height: 0; }
.ui-lista a.ui-texto-titulo { min-height: 0; -webkit-line-clamp: 1; }

.ui-chips { display: flex; flex-wrap: wrap; gap: .35rem; max-height: 1.7rem; overflow: hidden; }
.ui-chip {
  font-size: .72rem; font-weight: 500; padding: .18rem .55rem; border-radius: 999px; white-space: nowrap;
  color: #cdc8e6; background: rgba(139, 124, 246, .09); border: 1px solid rgba(139, 124, 246, .18);
}
.ui-chip.more { color: var(--ui-faint); background: transparent; border-color: var(--ui-border-strong); }

/* título e autor do card são st.page_link (navegação interna sem recarregar) */
[class*="st-key-titulo-"] a, [class*="st-key-autor-"] a {
  padding: 0 !important; margin: 0 !important; background: none !important; min-height: 0 !important;
  border: 0 !important; width: 100%;
}
[class*="st-key-titulo-"] [data-testid="stElementContainer"],
[class*="st-key-titulo-"] [data-testid="stPageLink"] > div { width: 100% !important; max-width: 100%; }
[class*="st-key-titulo-"] a *, [class*="st-key-autor-"] a * { white-space: normal !important; text-overflow: clip !important; }
/* o st.page_link vem com margem de -6px em cima e embaixo; com o título em
   duas linhas isso fazia o título invadir a linha do autor */
[class*="st-key-titulo-"] [data-testid="stElementContainer"],
[class*="st-key-autor-"] [data-testid="stElementContainer"] { margin: 0 !important; }
[class*="st-key-titulo-"] [data-testid="stPageLink"] { padding: .45rem 0 .2rem; }
[class*="st-key-autor-"] [data-testid="stPageLink"] { padding-bottom: .55rem; }
/* na página de leitura todo link para /ler ficaria com o fundo de "página ativa" */
[class*="st-key-acoes-"] a { background: none !important; }
[class*="st-key-titulo-"] a p {
  display: -webkit-box !important; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
  white-space: normal !important;
  min-height: 2.6em; margin: 0 !important;
  font-family: "Fraunces", serif; font-size: 1.2rem !important; font-weight: 600; line-height: 1.3;
  color: var(--ui-text) !important; transition: color .15s ease;
}
[class*="st-key-titulo-"] a:hover p { color: #cfc8ff !important; }
[class*="st-key-card-lista-"] [class*="st-key-titulo-"] a p { min-height: 0; -webkit-line-clamp: 1; }
.st-key-serie-nav a p { white-space: normal !important; }
[class*="st-key-autor-"] a p { font-size: .85rem !important; color: var(--ui-muted) !important; margin: 0 !important; }
[class*="st-key-autor-"] a p strong { color: var(--ui-text); font-weight: 600; transition: color .15s ease; }
[class*="st-key-autor-"] a:hover p strong { color: #cfc8ff; text-decoration: underline; text-underline-offset: 3px; }

/* "Série · parte N de M" nos cards de texto (st.page_link para a série) */
/* O espaçamento fica no container, não no parágrafo do rótulo: margem no
   texto o desloca em relação ao ícone (os dois são centralizados como
   caixas no link). Obs.: nada de sinal de menor/maior dentro deste CSS — o
   sanitizador do st.html lê como tag e descarta o bloco de estilo inteiro. */
[class*="st-key-serielink-"] { margin: -.3rem 0 .5rem; }
[class*="st-key-serielink-"] [data-testid="stElementContainer"] { margin: 0 !important; }
[class*="st-key-serielink-"] a {
  padding: 0 !important; margin: 0 !important; background: none !important; min-height: 0 !important; border: 0 !important;
  align-items: center !important; gap: .35rem;
}
[class*="st-key-serielink-"] a p { font-size: .78rem !important; line-height: 1.2 !important; font-weight: 600; color: #c2b9ff !important; margin: 0 !important; }
[class*="st-key-serielink-"] a [data-testid="stIconMaterial"] {
  font-size: .95rem !important; line-height: 1 !important; color: #c2b9ff;
}
[class*="st-key-serielink-"] a:hover p { color: #e0dbff !important; text-decoration: underline; text-underline-offset: 3px; }
.ui-origem { font-size: .72rem; font-weight: 600; letter-spacing: .04em; text-transform: uppercase; color: var(--ui-faint); }

/* ---------- card de série (página Séries) ---------- */
.ui-serie-partes { display: inline-flex; align-items: baseline; gap: .3rem; font-size: .78rem; color: var(--ui-muted); }
.ui-serie-partes b { font-family: "Fraunces", serif; font-size: 1.35rem; font-weight: 600; color: #c2b9ff; line-height: 1; }
.ui-serie-barra { display: flex; align-items: center; gap: 3px; margin: .5rem 0 .1rem; height: 5px; }
.ui-serie-barra i {
  flex: 1 1 0; max-width: 1.6rem; height: 100%; border-radius: 3px;
  background: linear-gradient(90deg, rgba(139,124,246,.75), rgba(244,114,182,.55));
}
.ui-serie-mais { font-size: .68rem; font-weight: 600; color: var(--ui-faint); margin-left: .2rem; line-height: 1; }
[class*="st-key-card-serie-"] .ui-parte-meta { margin: -.3rem 0 .45rem; }
.ui-serie-sinopse {
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
  min-height: 3em; font-size: .86rem; line-height: 1.5; color: #b9b7c6; margin: 0 0 .7rem;
}

/* ---------- perfil do autor ---------- */
.st-key-autor-hero { flex: 1 1 0 !important; min-width: 0; }
.st-key-autor-hero .ui-hero { margin-bottom: .6rem; }
.ui-atividade { width: 15rem; padding-bottom: .75rem; }
.ui-atividade-barras { display: flex; align-items: flex-end; gap: 3px; height: 3.2rem; border-bottom: 1px solid var(--ui-border-strong); }
.ui-atividade-barras i {
  flex: 1 1 0; min-width: 3px; border-radius: 3px 3px 0 0;
  background: linear-gradient(180deg, var(--ui-accent), rgba(139,124,246,.35)); transition: filter .15s ease;
}
.ui-atividade-barras i:hover { filter: brightness(1.35); }
.ui-atividade-eixo { display: flex; justify-content: space-between; gap: .6rem; margin-top: .3rem; font-size: .7rem; color: var(--ui-faint); font-variant-numeric: tabular-nums; }
.st-key-autor-acoes { flex-wrap: wrap !important; margin: .1rem 0 .4rem; }
/* pílulas lado a lado: o invólucro que o Streamlit põe em volta de cada
   container dividiria a linha em partes iguais */
.st-key-autor-acoes > *, [class*="st-key-grafia-"] { flex: none !important; width: auto !important; }
[class*="st-key-grafia-"] [data-testid="stElementContainer"] { margin: 0 !important; }
[class*="st-key-grafia-"] a {
  padding: .15rem .65rem !important; min-height: 0 !important; border-radius: 999px !important;
  border: 1px solid rgba(139, 124, 246, .3) !important; background: var(--ui-accent-soft) !important;
}
[class*="st-key-grafia-"] a p { font-size: .8rem !important; font-weight: 600; color: #c2b9ff !important; }
@media (max-width: 640px) {
  .st-key-autor-topo { flex-direction: column !important; align-items: stretch !important; }
  .st-key-autor-topo > * { width: 100% !important; }
  .ui-atividade { width: 100%; }
}

/* ---------- partes de uma série (página da série) ---------- */
[class*="st-key-linha-parte-"] {
  background: var(--ui-card); border: 1px solid var(--ui-border); border-radius: 14px;
  padding: .75rem 1rem .75rem .85rem; margin-bottom: .1rem;
  transition: border-color .15s ease, background .15s ease;
}
[class*="st-key-linha-parte-"]:hover { border-color: rgba(139, 124, 246, .38); background: var(--ui-card-hover); }
[class*="st-key-corpo-parte-"] { flex: 1 1 0 !important; min-width: 0; }
.ui-parte-num {
  width: 2.3rem; height: 2.3rem; border-radius: 50%; flex: none;
  display: flex; align-items: center; justify-content: center;
  font-size: .85rem; font-weight: 700; font-variant-numeric: tabular-nums;
  color: #c2b9ff; background: var(--ui-accent-soft); border: 1px solid rgba(139, 124, 246, .32);
}
.ui-parte-num.gostei { color: #fff; background: var(--ui-accent); border-color: var(--ui-accent); }
.ui-parte-num.nao-gostei { color: var(--ui-faint); background: transparent; border-style: dashed; border-color: var(--ui-border-strong); }
/* título: st.page_link alinhado à esquerda, sem fundo de botão */
[class*="st-key-corpo-parte-"] [data-testid="stElementContainer"] { margin: 0 !important; }
[class*="st-key-corpo-parte-"] a {
  padding: 0 !important; margin: 0 !important; background: none !important; min-height: 0 !important;
  border: 0 !important; justify-content: flex-start !important;
}
[class*="st-key-corpo-parte-"] a p {
  font-family: "Fraunces", serif; font-size: 1.05rem !important; font-weight: 600; line-height: 1.3;
  color: var(--ui-text) !important; margin: 0 !important; text-align: left;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
[class*="st-key-corpo-parte-"] a:hover p { color: #cfc8ff !important; }
.ui-parte-meta { display: flex; flex-wrap: wrap; gap: .45rem; margin-top: .15rem; font-size: .78rem; color: var(--ui-faint); font-variant-numeric: tabular-nums; }
.ui-parte-meta .ui-star { color: #f4c152; }
.ui-parte-trecho {
  margin: .35rem 0 0; font-size: .85rem; line-height: 1.45; color: #b9b7c6;
  display: -webkit-box; -webkit-line-clamp: 1; -webkit-box-orient: vertical; overflow: hidden;
}
/* "Ler" à direita: botão discreto que acende no hover da linha */
[class*="st-key-linha-parte-"] > [data-testid="stElementContainer"]:last-child a {
  border: 1px solid var(--ui-border-strong) !important; border-radius: 999px !important;
  padding: .3rem .8rem !important; background: transparent !important;
}
[class*="st-key-linha-parte-"]:hover > [data-testid="stElementContainer"]:last-child a {
  border-color: rgba(139, 124, 246, .55) !important; background: var(--ui-accent-soft) !important;
}
@media (max-width: 640px) {
  .ui-parte-trecho { display: none; }
  [class*="st-key-corpo-parte-"] a p { white-space: normal; }
}

/* chips clicáveis (st.pills) — mesmo visual de .ui-chip */
[class*="st-key-chips-"] [data-testid="stButtonGroup"] { gap: .35rem; }
[class*="st-key-chips-"] button {
  min-height: 0 !important; height: auto !important; padding: .18rem .55rem !important; border-radius: 999px !important;
  font-size: .72rem !important; font-weight: 500; line-height: 1.35;
  color: #cdc8e6 !important; background: rgba(139, 124, 246, .09) !important; border: 1px solid rgba(139, 124, 246, .18) !important;
  transition: background .15s ease, border-color .15s ease, color .15s ease;
}
[class*="st-key-chips-"] button p { font-size: .72rem !important; }
[class*="st-key-chips-"] button:hover { color: #fff !important; background: rgba(139, 124, 246, .22) !important; border-color: rgba(139, 124, 246, .5) !important; }
[class*="st-key-chips-card"] { max-height: 1.75rem; overflow: hidden; }

/* linha de ações dentro do card */
[class*="st-key-acoes-"] { margin-top: .35rem; padding-top: .55rem; border-top: 1px solid var(--ui-border); }
[class*="st-key-acoes-"] { flex-wrap: nowrap !important; gap: .4rem !important; }
[class*="st-key-acoes-"] button, [class*="st-key-acoes-"] a { min-height: 2.1rem; padding: .25rem .5rem; font-size: .82rem; }
[class*="st-key-acoes-"] > [data-testid="stElementContainer"]:last-child { margin-left: auto; }

/* ---------- estatísticas (painel) ---------- */
.ui-kpi { padding: .15rem 0; }
.ui-kpi-label { font-size: .75rem; font-weight: 600; letter-spacing: .06em; text-transform: uppercase; color: var(--ui-faint); }
.ui-kpi-valor { font-size: 2rem; font-weight: 600; line-height: 1.15; margin-top: .3rem; color: var(--ui-text); letter-spacing: -0.01em; }
.ui-kpi-nota { font-size: .82rem; color: var(--ui-muted); margin-top: .15rem; }

/* ---------- barra de composição ---------- */
.ui-barra { display: flex; gap: 2px; height: 12px; border-radius: 999px; overflow: hidden; background: rgba(255,255,255,.05); }
.ui-barra > i { display: block; height: 100%; min-width: 3px; }
.ui-legenda { display: flex; flex-wrap: wrap; gap: .4rem 1.25rem; margin-top: .7rem; font-size: .82rem; color: var(--ui-muted); }
.ui-legenda span { display: inline-flex; align-items: center; gap: .45rem; }
.ui-legenda i { width: .65rem; height: .65rem; border-radius: 3px; display: inline-block; }
.ui-legenda b { color: var(--ui-text); font-weight: 600; font-variant-numeric: tabular-nums; }

/* ---------- estágio da fila (painel) ---------- */
.ui-estagio-head { display: flex; align-items: center; justify-content: space-between; gap: .5rem; }
.ui-estagio-num { font-size: .72rem; font-weight: 700; letter-spacing: .1em; color: var(--ui-faint); }
.ui-estagio-nome { font-family: "Fraunces", serif; font-size: 1.25rem; font-weight: 600; margin: .15rem 0 .1rem; }
.ui-estagio-fila { font-size: 2.1rem; font-weight: 600; line-height: 1.1; margin-top: .6rem; font-variant-numeric: tabular-nums; }
.ui-estagio-fila small { font-size: .85rem; font-weight: 500; color: var(--ui-faint); margin-left: .35rem; }
.ui-grade { display: grid; grid-template-columns: 1fr 1fr; gap: .55rem 1rem; margin: .9rem 0 .8rem; }
.ui-grade div { display: flex; flex-direction: column; gap: .1rem; }
.ui-grade span { font-size: .72rem; color: var(--ui-faint); text-transform: uppercase; letter-spacing: .05em; font-weight: 600; }
.ui-grade b { font-size: .95rem; font-weight: 600; color: var(--ui-text); font-variant-numeric: tabular-nums; }
.ui-ocupacao { height: 6px; border-radius: 999px; background: rgba(255,255,255,.06); overflow: hidden; margin-top: .25rem; }
.ui-ocupacao i { display: block; height: 100%; border-radius: 999px; background: linear-gradient(90deg, var(--ui-accent), var(--ui-accent-2)); }
.ui-ids { font-family: "JetBrains Mono", monospace; font-size: .74rem; color: var(--ui-faint);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-height: 1.1rem; }

.ui-rodape-fila { display: flex; align-items: center; gap: .5rem; margin-top: .35rem; min-width: 0;
  font-size: .85rem; color: var(--ui-faint); white-space: nowrap; }
.ui-rodape-fila > span:last-child { overflow: hidden; text-overflow: ellipsis; }
.ui-instancias { display: inline-flex; gap: 3px; flex-shrink: 0; }
.ui-instancias i { width: .5rem; height: .5rem; border-radius: 50%; background: var(--ui-neutral); }
.ui-instancias i.good { background: #3fcf3f; }
.ui-instancias i.crit { background: #f06a6a; }

/* ---------- estado vazio ---------- */
.ui-vazio {
  text-align: center; padding: 3.5rem 1.5rem; border: 1px dashed var(--ui-border-strong); border-radius: 18px;
  background: rgba(255,255,255,.015);
}
.ui-vazio .ui-vazio-icone { font-size: 2.2rem; margin-bottom: .6rem; }
.ui-vazio h4 { font-family: "Fraunces", serif; font-size: 1.2rem; margin: 0 0 .35rem; padding: 0; }
.ui-vazio p { color: var(--ui-muted); margin: 0 auto; max-width: 30rem; font-size: .92rem; }

/* ---------- paginação ---------- */
.ui-resumo-pagina { color: var(--ui-faint); font-size: .85rem; font-variant-numeric: tabular-nums; }
.ui-resumo-pagina b { color: var(--ui-muted); font-weight: 600; }
[class*="st-key-pag-"] button { min-width: 2.4rem; min-height: 2.3rem; font-variant-numeric: tabular-nums; }

/* ---------- página de leitura ---------- */
.st-key-leitor { max-width: 44rem; margin: 0 auto; }
.ui-leitura-meta { display: flex; flex-wrap: wrap; gap: .3rem .6rem; color: var(--ui-faint); font-size: .85rem; font-variant-numeric: tabular-nums; }
.ui-leitura-meta .ui-star { color: #f4c152; }
.st-key-leitor .ui-title { font-size: 2.3rem; }
.st-key-autor-leitor [data-testid="stPageLink"] { padding-bottom: .4rem; }
.st-key-autor-leitor a p { font-size: 1rem !important; }
.st-key-barra-leitura { padding: .6rem 0; border-top: 1px solid var(--ui-border); border-bottom: 1px solid var(--ui-border); flex-wrap: wrap !important; }
.st-key-barra-leitura > [data-testid="stElementContainer"]:last-child { margin-left: auto; }
.st-key-ajustes-leitura { padding: .6rem 0 .2rem; flex-wrap: wrap !important; }
/* folha: a "caixa de papel" do texto, com três cores */
.ui-folha { --papel: #12141b; --tinta: #dcd9e8; --capitular: #c9c1ff; --borda: var(--ui-border); --apagado: var(--ui-faint);
  background: var(--papel); color: var(--tinta); border: 1px solid var(--borda); border-radius: 18px;
  padding: 1.6rem 2rem; margin: 1rem 0 .5rem; scroll-margin-top: 64px; box-shadow: 0 20px 50px -30px rgba(0,0,0,.8); }
@media (max-width: 640px) { .ui-folha { padding: 1.1rem 1.1rem; border-radius: 14px; } }
.ui-folha.tema-sepia { --papel: #f3e9d2; --tinta: #3d3122; --capitular: #9a5b2c; --borda: #e2d3b1; --apagado: #8a7658; }
.ui-folha.tema-claro { --papel: #fbfaf7; --tinta: #1f1d24; --capitular: #6d5ce0; --borda: #e7e4dc; --apagado: #85828e; }
.ui-leitura {
  font-family: "Literata", "Fraunces", Georgia, serif; color: var(--tinta, #dcd9e8); line-height: 1.85;
  font-size: 1.12rem; hyphens: auto; -webkit-hyphens: auto; margin: 0;
}
.ui-leitura.tam-p { font-size: 1rem; }
.ui-leitura.tam-g { font-size: 1.3rem; }
.ui-leitura p { margin: 0 0 1.15em; text-wrap: pretty; font-size: inherit !important; line-height: inherit !important; color: inherit; }
.ui-leitura p.capitular::first-letter {
  float: left; font-family: "Fraunces", serif; font-weight: 600; font-size: 3.4em; line-height: .9;
  margin: .08em .12em 0 0; color: var(--capitular, #c9c1ff);
}
/* modo páginas: o texto vira colunas da largura da folha, e só uma aparece
   por vez (a altura da folha é calculada pelo script pra caber na tela) */
.ui-paginas { overflow: hidden; position: relative; }
.ui-paginas .ui-leitura { column-fill: auto; height: 100%; transition: transform .28s ease; }
.ui-paginas .ui-leitura p { orphans: 2; widows: 2; }
.ui-pag-rodape { display: flex; align-items: center; justify-content: space-between; gap: 1rem;
  margin-top: .9rem; padding-top: .7rem; border-top: 1px solid var(--borda); color: var(--apagado); font-size: .85rem;
  font-variant-numeric: tabular-nums; }
.ui-pag-rodape button { background: none; border: 1px solid var(--borda); color: var(--tinta); border-radius: 999px;
  padding: .35rem .9rem; font: inherit; cursor: pointer; }
.ui-pag-rodape button:disabled { opacity: .35; cursor: default; }
.ui-pag-rodape button:not(:disabled):hover { border-color: var(--capitular); color: var(--capitular); }
.ui-pag-barra { flex: 1; height: 3px; border-radius: 999px; background: var(--borda); overflow: hidden; max-width: 14rem; }
.ui-pag-barra i { display: block; height: 100%; width: 0; background: var(--capitular); transition: width .2s ease; }
.ui-leitura-fim { text-align: center; color: var(--apagado, var(--ui-faint)); letter-spacing: .6em; margin: 2rem 0 1rem; }
.ui-aviso { border: 1px solid rgba(250,178,25,.35); background: rgba(250,178,25,.08); color: #f3d38a;
  border-radius: 12px; padding: .7rem .9rem; font-size: .9rem; margin: 1rem 0; }
.ui-progresso { position: fixed; top: 0; left: 0; height: 3px; width: 0; z-index: 1000000;
  background: linear-gradient(90deg, var(--ui-accent), var(--ui-accent-2)); transition: width .1s linear; }
/* série no cabeçalho da leitura: link pra página da série + faixa com a
   parte atual acesa */
.st-key-serie-cabecalho { margin-bottom: .1rem; }
.st-key-serie-cabecalho [data-testid="stElementContainer"] { margin: 0 !important; }
.st-key-serie-cabecalho a { padding: 0 !important; margin: 0 !important; background: none !important; min-height: 0 !important; border: 0 !important; }
.st-key-serie-cabecalho a p {
  font-size: .75rem !important; font-weight: 600; letter-spacing: .06em; text-transform: uppercase;
  color: #c2b9ff !important; margin: 0 !important;
}
.st-key-serie-cabecalho a:hover p { color: #e0dbff !important; text-decoration: underline; text-underline-offset: 3px; }
.ui-serie-posicao { max-width: 22rem; margin: .45rem 0 .2rem; }
.ui-serie-posicao i { background: var(--ui-border-strong); }
.ui-serie-posicao i.antes { background: rgba(139, 124, 246, .45); }
.ui-serie-posicao i.atual { background: linear-gradient(90deg, var(--ui-accent), var(--ui-accent-2)); box-shadow: 0 0 10px rgba(139,124,246,.6); }

/* "Continue a série": dois cartões clicáveis por inteiro (o ::after do link
   cobre o cartão) */
.st-key-serie-nav { align-items: stretch !important; margin-bottom: .8rem; }
.st-key-serie-ant, .st-key-serie-prox, .st-key-serie-fim {
  flex: 1 1 0 !important; min-width: 0; position: relative;
  background: var(--ui-card); border: 1px solid var(--ui-border); border-radius: 16px; padding: .9rem 1.1rem;
  transition: border-color .15s ease, background .15s ease, transform .15s ease;
}
.st-key-serie-prox { border-color: rgba(139, 124, 246, .35); background: linear-gradient(135deg, rgba(139,124,246,.12), rgba(244,114,182,.05)), var(--ui-card); }
.st-key-serie-prox, .st-key-serie-prox .ui-nav-rotulo { text-align: right; }
.st-key-serie-ant:hover, .st-key-serie-prox:hover, .st-key-serie-fim:hover { border-color: rgba(139, 124, 246, .6); transform: translateY(-1px); }
.ui-nav-rotulo { font-size: .72rem; font-weight: 600; letter-spacing: .06em; text-transform: uppercase; color: var(--ui-faint); margin-bottom: .3rem; }
.st-key-serie-prox .ui-nav-rotulo { color: #c2b9ff; }
[class*="st-key-serie-"] [data-testid="stElementContainer"] { margin: 0 !important; }
.st-key-serie-ant a, .st-key-serie-prox a, .st-key-serie-fim a {
  position: static !important; padding: 0 !important; margin: 0 !important; background: none !important;
  min-height: 0 !important; border: 0 !important; width: 100%;
}
.st-key-serie-prox a { justify-content: flex-end !important; }
.st-key-serie-ant a::after, .st-key-serie-prox a::after, .st-key-serie-fim a::after { content: ""; position: absolute; inset: 0; border-radius: 16px; }
.st-key-serie-ant a p, .st-key-serie-prox a p, .st-key-serie-fim a p {
  font-family: "Fraunces", serif; font-size: 1.05rem !important; font-weight: 600; line-height: 1.3;
  color: var(--ui-text) !important; margin: 0 !important; white-space: normal !important;
  display: -webkit-box !important; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.st-key-serie-prox a p { text-align: right; }
.st-key-serie-pular { margin-bottom: .5rem; }
.st-key-serie-pular > [data-testid="stElementContainer"]:first-child { flex: 1 1 0; min-width: 0; }
@media (max-width: 640px) {
  .st-key-serie-nav { flex-direction: column !important; gap: .75rem !important; }
  /* empilhados, o flex: 1 1 0 dividiria a ALTURA entre os cartões — vale
     pro cartão e pro invólucro que o Streamlit põe em volta dele */
  .st-key-serie-nav > *, .st-key-serie-ant, .st-key-serie-prox, .st-key-serie-fim { flex: none !important; }
}

/* ---------- widgets ---------- */
[data-testid="stTextInputRootElement"] input { font-size: 1rem; }
.st-key-busca-principal [data-testid="stTextInputRootElement"] { min-height: 3.25rem; border-radius: 14px; }
.st-key-busca-principal input { font-size: 1.1rem !important; }
[data-testid="stMetric"] { background: var(--ui-card); }
[data-testid="stDialog"] [role="dialog"] { background: #10121a; border: 1px solid var(--ui-border-strong); }
</style>
"""


def aplicar_estilo() -> None:
    st.html(CSS)


# ---------------------------------------------------------------- formatação

def num(valor: float | int | None) -> str:
    """Número no formato pt-BR (1.234)."""
    if valor is None:
        return "—"
    return f"{int(valor):,}".replace(",", ".")


def data_curta(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        d = datetime.fromisoformat(iso)
    except ValueError:
        return ""
    return f"{d.day} {MESES[d.month - 1]} {d.year}"


def esc(texto: object) -> str:
    return html.escape(str(texto)) if texto is not None else ""


def md_esc(texto: object) -> str:
    """Escapa texto livre para rótulos de widget, que o Streamlit interpreta
    como Markdown (um título com `*`, `_` ou `$` viraria itálico/LaTeX)."""
    return re.sub(r"([\\`*_{}\[\]()#+\-.!|~<>$:])", r"\\\1", str(texto or ""))


def temas_de(item: dict) -> list[str]:
    bruto = item.get("temas")
    if not bruto:
        return []
    try:
        return json.loads(bruto)
    except ValueError:
        return []


# ---------------------------------------------------------------- componentes

def hero(titulo: str, subtitulo: str = "", eyebrow: str = "", stats: list[tuple[str, str]] | None = None,
         extra_eyebrow: str = "") -> None:
    stats_html = ""
    if stats:
        stats_html = '<div class="ui-stats">' + "".join(
            f'<div class="ui-stat"><b>{esc(v)}</b><span>{esc(r)}</span></div>' for v, r in stats
        ) + "</div>"
    st.html(
        f'<div class="ui-hero">'
        f'<div class="ui-eyebrow">{esc(eyebrow)}{extra_eyebrow}</div>'
        f'<div class="ui-title">{esc(titulo)}</div>'
        f'<p class="ui-sub">{esc(subtitulo)}</p>{stats_html}</div>'
    )


def secao(titulo: str, nota: str = "") -> None:
    st.html(f'<div class="ui-section"><h3>{esc(titulo)}</h3><p>{esc(nota)}</p></div>')


def pill(texto: str, tom: str = "", ponto: bool = True, vivo: bool = False) -> str:
    classes = " ".join(c for c in ["ui-pill", tom, "live" if vivo else ""] if c)
    dot = '<span class="ui-dot"></span>' if ponto else ""
    return f'<span class="{classes}">{dot}{esc(texto)}</span>'


def chips(temas: list[str], maximo: int = 5) -> str:
    visiveis = temas[:maximo]
    resto = len(temas) - len(visiveis)
    partes = [f'<span class="ui-chip">{esc(t)}</span>' for t in visiveis]
    if resto > 0:
        partes.append(f'<span class="ui-chip more">+{resto}</span>')
    return f'<div class="ui-chips">{"".join(partes)}</div>'


def vazio(icone: str, titulo: str, texto: str = "") -> None:
    st.html(
        f'<div class="ui-vazio"><div class="ui-vazio-icone">{icone}</div>'
        f"<h4>{esc(titulo)}</h4><p>{esc(texto)}</p></div>"
    )


def barra_composicao(partes: list[tuple[str, int, str]]) -> None:
    """Barra horizontal empilhada + legenda com rótulo e valor (a cor nunca
    carrega o significado sozinha). `partes` = [(rótulo, valor, cor)]."""
    total = sum(v for _, v, _ in partes) or 1
    segs = "".join(
        f'<i style="flex:{v / total:.6f} 1 0; background:{cor}" title="{esc(r)}: {num(v)}"></i>'
        for r, v, cor in partes if v > 0
    )
    legenda = "".join(
        f'<span><i style="background:{cor}"></i>{esc(r)} <b>{num(v)}</b> '
        f'<em style="font-style:normal;color:var(--ui-faint)">{v / total * 100:.1f}%</em></span>'
        for r, v, cor in partes
    )
    st.html(f'<div class="ui-barra">{segs}</div><div class="ui-legenda">{legenda}</div>')


# ---------------------------------------------------------------- paginação

def _janela_paginas(atual: int, total: int, vizinhos: int = 1) -> list[int | None]:
    """Números de página a mostrar, com None onde entra reticências:
    1 … 4 5 [6] 7 8 … 40."""
    if total <= 7:
        return list(range(1, total + 1))
    paginas: list[int | None] = [1]
    inicio = max(2, atual - vizinhos)
    fim = min(total - 1, atual + vizinhos)
    if atual <= 3:
        inicio, fim = 2, 4
    elif atual >= total - 2:
        inicio, fim = total - 3, total - 1
    if inicio > 2:
        paginas.append(None)
    paginas.extend(range(inicio, fim + 1))
    if fim < total - 1:
        paginas.append(None)
    paginas.append(total)
    return paginas


def pagina_atual(chave: str, total_itens: int, por_pagina: int, assinatura: object = None) -> tuple[int, int]:
    """Lê (e corrige) a página atual guardada em session_state. `assinatura`
    é qualquer coisa que identifique a consulta (texto + filtros): quando muda,
    a paginação volta pra página 1. Retorna (página, total de páginas)."""
    total_paginas = max(1, math.ceil(total_itens / por_pagina))
    chave_ass = f"{chave}__assinatura"
    if assinatura is not None and st.session_state.get(chave_ass) != assinatura:
        st.session_state[chave_ass] = assinatura
        st.session_state[chave] = 1
    pagina = min(max(1, st.session_state.get(chave, 1)), total_paginas)
    st.session_state[chave] = pagina
    return pagina, total_paginas


def resumo_pagina(pagina: int, por_pagina: int, total_itens: int, rotulo: str = "resultados") -> None:
    if total_itens == 0:
        return
    ini = (pagina - 1) * por_pagina + 1
    fim = min(pagina * por_pagina, total_itens)
    st.html(
        f'<div class="ui-resumo-pagina">Mostrando <b>{num(ini)}–{num(fim)}</b> de <b>{num(total_itens)}</b> {esc(rotulo)}</div>'
    )


def _ir_para(chave: str, pagina: int) -> None:
    st.session_state[chave] = pagina
    st.session_state[f"{chave}__rolar"] = True


def ancora_topo(chave: str) -> None:
    """Âncora no início da lista; depois de trocar de página pelos controles
    de baixo, rola a tela de volta pra cá (senão o usuário fica no rodapé da
    página nova)."""
    rolar = st.session_state.pop(f"{chave}__rolar", False)
    script = ""
    if rolar:
        script = (
            f"<script>setTimeout(function(){{var el=document.getElementById('topo-{chave}');"
            f"if(el){{el.scrollIntoView({{behavior:'smooth',block:'start'}});}}}},60);</script>"
        )
    st.html(f'<div id="topo-{chave}" style="scroll-margin-top:5rem"></div>{script}', unsafe_allow_javascript=bool(script))


def controles_paginacao(chave: str, pagina: int, total_paginas: int) -> None:
    if total_paginas <= 1:
        return
    with st.container(horizontal=True, horizontal_alignment="center", gap="small", key=f"pag-{chave}"):
        st.button("", icon=":material/chevron_left:", key=f"{chave}_ant", type="tertiary",
                  disabled=pagina <= 1, on_click=_ir_para, args=(chave, pagina - 1), help="Página anterior")
        for i, p in enumerate(_janela_paginas(pagina, total_paginas)):
            if p is None:
                st.button("…", key=f"{chave}_gap{i}", type="tertiary", disabled=True)
            else:
                st.button(num(p), key=f"{chave}_p{p}", type="primary" if p == pagina else "tertiary",
                          on_click=_ir_para, args=(chave, p))
        st.button("", icon=":material/chevron_right:", key=f"{chave}_prox", type="tertiary",
                  disabled=pagina >= total_paginas, on_click=_ir_para, args=(chave, pagina + 1), help="Próxima página")


# ---------------------------------------------------------------- leitura

def rolar_topo(marca: str) -> None:
    """Volta a página ao topo (troca de texto na leitura). Repete o comando
    porque o conteúdo novo ainda está sendo montado nos primeiros instantes.
    `marca` (o id do texto) muda o HTML a cada troca — com o mesmo HTML o
    elemento não é remontado e o script não roda de novo."""
    st.html(
        f"<script data-marca='{esc(marca)}'>[0,120,400].forEach(function(t){{setTimeout(function(){{"
        "var m=document.querySelector('[data-testid=\"stMain\"]');if(m)m.scrollTop=0;"
        "window.scrollTo(0,0);},t);});</script>",
        unsafe_allow_javascript=True,
    )


def modo_leitura() -> None:
    """Esconde a barra lateral só na página de leitura. Ela continua sendo
    renderizada (os filtros ficam no session_state/URL), apenas fora da tela."""
    st.html(
        "<style>"
        '[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"],'
        '[data-testid="stExpandSidebarButton"] { display: none !important; }'
        '[data-testid="stMainBlockContainer"] { max-width: 1100px; }'
        # menu do topo opaco: no modo páginas a folha encosta nele
        '[data-testid="stHeader"] { background: var(--ui-bg) !important; }'
        "</style>"
    )


def folha_paginada(corpo: str, classes: str, tema: str, marca: str) -> None:
    """Leitura em páginas: a folha ocupa a altura visível da tela e o texto é
    dividido em colunas CSS da largura da folha; ← / → (teclado, botões ou
    clique nas bordas) deslizam uma coluna por vez. A página atual fica no
    sessionStorage por texto, então um rerun (👍, trocar a fonte) não perde
    o lugar — `marca` muda o HTML quando fonte/cor mudam, pra remontar."""
    st.html(
        f'<div class="ui-folha tema-{esc(tema)}" id="ui-folha" data-marca="{esc(marca)}">'
        f'<div class="ui-paginas" id="ui-paginas"><article class="{esc(classes)}" id="ui-texto">{corpo}</article></div>'
        '<div class="ui-pag-rodape"><button id="ui-pag-ant" aria-label="Página anterior">← Anterior</button>'
        '<span id="ui-pag-num">1 / 1</span><span class="ui-pag-barra"><i id="ui-pag-barra"></i></span>'
        '<button id="ui-pag-prox" aria-label="Próxima página">Próxima →</button></div></div>'
    )
    # Script em st.html separado, e sem "<" no JS: o sanitizador do st.html lê
    # "a<b" como início de tag e descarta o script inteiro.
    st.html(
        # o Streamlit roda o script antes de inserir o HTML: espera os elementos
        # O Streamlit só executa o script quando o elemento é montado — mudar
        # fonte/cor atualiza o HTML sem rodá-lo de novo. Por isso um vigia
        # (instalado uma vez) reinicia a paginação quando a marca da folha muda.
        "<script>window.__uiPagIniciar=function(){"
        "var folha=document.getElementById('ui-folha'),vis=document.getElementById('ui-paginas'),"
        "txt=document.getElementById('ui-texto');"
        "if(!folha||!vis||!txt||folha.dataset.pronto===folha.dataset.marca)return;"
        "var MARCA=folha.dataset.marca;folha.dataset.pronto=MARCA;"
        "var chave='ui-pag-'+MARCA.split('-')[0],gap=48,pag=0,total=1,cab=64;"
        "try{pag=parseInt(sessionStorage.getItem(chave)||'0',10)||0;}catch(e){}"
        "function medir(){var topo=vis.getBoundingClientRect().top;"
        # altura: da barra de navegação até o rodapé da folha, cabendo na janela
        "var extra=folha.offsetHeight-vis.offsetHeight;"
        "var alt=Math.max(240,window.innerHeight-cab-extra-16);vis.style.height=alt+'px';"
        "var w=vis.clientWidth;txt.style.columnWidth=w+'px';txt.style.columnGap=gap+'px';txt.style.width=w+'px';"
        "var ult=txt.lastElementChild;var fim=ult?ult.getBoundingClientRect().right-txt.getBoundingClientRect().left:w;total=Math.max(1,Math.ceil((fim+gap/2)/(w+gap)));pag=Math.min(pag,total-1);mostrar();}"
        "function mostrar(){var w=vis.clientWidth;txt.style.transform='translateX('+(-pag*(w+gap))+'px)';"
        "document.getElementById('ui-pag-num').textContent=(pag+1)+' / '+total;"
        "document.getElementById('ui-pag-barra').style.width=((pag+1)/total*100)+'%';"
        "var bar=document.getElementById('ui-progresso');if(bar)bar.style.width=((pag+1)/total*100)+'%';"
        "document.getElementById('ui-pag-ant').disabled=0>=pag;"
        "var prox=document.getElementById('ui-pag-prox');prox.disabled=pag>=total-1;"
        "prox.textContent=pag>=total-1?'Fim ✦':'Próxima →';"
        "try{sessionStorage.setItem(chave,String(pag));}catch(e){}}"
        "function ir(d){var n=Math.max(0,Math.min(total-1,pag+d));if(n!==pag){pag=n;mostrar();}}"
        # o áudio (player_audio) vira a página até o trecho que está sendo lido;
        # x = distância do início do texto, sem o deslocamento da página atual
        "window.__uiPagIrX=function(x){if(!document.body.contains(folha))return false;var w=vis.clientWidth;"
        "var n=Math.max(0,Math.min(total-1,Math.floor((x+gap/2)/(w+gap))));if(n!==pag){pag=n;mostrar();}return true;};"
        "document.getElementById('ui-pag-ant').onclick=function(){ir(-1);};"
        "document.getElementById('ui-pag-prox').onclick=function(){ir(1);};"
        "vis.onclick=function(e){var r=vis.getBoundingClientRect();if(r.left+r.width*0.3>e.clientX)ir(-1);"
        "else if(e.clientX>r.left+r.width*0.7)ir(1);};"
        "var x0=null;vis.ontouchstart=function(e){x0=e.touches[0].clientX;};"
        "vis.ontouchend=function(e){if(x0===null)return;var dx=e.changedTouches[0].clientX-x0;x0=null;"
        "if(Math.abs(dx)>40)ir(dx>0?-1:1);};"
        # um único listener global de teclado/resize, trocado a cada montagem
        "if(window.__uiPagTecla)document.removeEventListener('keydown',window.__uiPagTecla);"
        "if(window.__uiPagResize)window.removeEventListener('resize',window.__uiPagResize);"
        "window.__uiPagTecla=function(e){if(!document.body.contains(folha))return;"
        "var t=e.target&&e.target.tagName;if(t==='INPUT'||t==='TEXTAREA')return;"
        "if(e.key==='ArrowRight'||e.key==='PageDown'){ir(1);e.preventDefault();}"
        "else if(e.key==='ArrowLeft'||e.key==='PageUp'){ir(-1);e.preventDefault();}};"
        "window.__uiPagResize=function(){if(document.body.contains(folha))medir();};"
        "document.addEventListener('keydown',window.__uiPagTecla);window.addEventListener('resize',window.__uiPagResize);"
        # fontes carregando mudam a quantidade de páginas
        "medir();if(document.fonts)document.fonts.ready.then(medir);setTimeout(medir,150);setTimeout(medir,400);setTimeout(medir,1000);"
        # traz a folha pra tela (o cabeçalho do texto fica acima)
        "function alinhar(){var m=document.querySelector('[data-testid=\"stMain\"]');"
        "if(m)m.scrollTop+=folha.getBoundingClientRect().top-cab;}"
        "setTimeout(alinhar,600);setTimeout(alinhar,1500);"
        "};"
        "if(!window.__uiPagVigia)window.__uiPagVigia=setInterval(function(){window.__uiPagIniciar();},250);"
        "window.__uiPagIniciar();</script>",
        unsafe_allow_javascript=True,
    )


def progresso_leitura() -> None:
    """Barra fina no topo que acompanha a rolagem. O script é idempotente:
    reruns não empilham listeners."""
    st.html(
        '<div class="ui-progresso" id="ui-progresso"></div>'
        "<script>(function(){if(window.__uiProgresso)return;window.__uiProgresso=true;"
        "function alvo(){return document.querySelector('[data-testid=\"stMain\"]');}"
        "function atualizar(){var bar=document.getElementById('ui-progresso');if(!bar||document.getElementById('ui-paginas'))return;"
        "var m=alvo(),se=document.scrollingElement;"
        "var top=Math.max(m?m.scrollTop:0,se?se.scrollTop:0);"
        "var alt=Math.max(m?m.scrollHeight-m.clientHeight:0,se?se.scrollHeight-se.clientHeight:0);"
        "bar.style.width=(alt>0?Math.min(100,top/alt*100):0)+'%';}"
        "document.addEventListener('scroll',atualizar,true);setInterval(atualizar,800);})();</script>",
        unsafe_allow_javascript=True,
    )


# ---------------------------------------------------------------- áudio (Fase 11)

def player_audio(texto_id: str, mapa: list | None = None) -> None:
    """Controles do "Ouvir". Os blocos de áudio são `st.audio` escondidos no
    container `tts-blocos` (renderizados por um fragmento que vai acrescentando
    os novos); este player toca um de cada vez e emenda o seguinte ao terminar
    — se ele ainda não foi gerado, espera e toca assim que aparecer.
    O estado mora em window.__tts; um vigia (instalado uma vez, como na folha
    paginada) sincroniza com o DOM, porque o Streamlit não roda o script de
    novo quando o HTML só é atualizado.
    Enquanto lê, o parágrafo atual ganha destaque (`mapa`: por bloco, os
    parágrafos e seus tamanhos — a posição é estimada pela fração tocada do
    bloco) e a leitura acompanha: rola até ele, ou vira a página no modo páginas. Sem "<" no JS (sanitizador do st.html)."""
    st.html(
        "<style>"
        ".st-key-tts-blocos { display: none !important; }"
        # a barra segue o rodapé da folha paginada: botões-pílula de borda fina,
        # números tabulares, trilho de 3px — com o acento do app no lugar da capitular
        ".ui-tts { display: flex; align-items: center; gap: .45rem; flex-wrap: wrap; padding: .6rem .75rem;"
        " margin: .35rem 0 .1rem; border: 1px solid var(--ui-border); border-radius: 14px;"
        " background: linear-gradient(180deg, var(--ui-card-hover), var(--ui-card));"
        " box-shadow: 0 14px 34px -26px rgba(0,0,0,.9); }"
        ".ui-tts button { background: none; border: 1px solid var(--ui-border-strong); color: var(--ui-text);"
        " border-radius: 999px; padding: .28rem .8rem; cursor: pointer; font: inherit; font-size: .85rem; line-height: 1.2;"
        " transition: border-color .15s ease, color .15s ease, background-color .15s ease; }"
        ".ui-tts button:hover { border-color: var(--ui-accent); color: var(--ui-accent); }"
        ".ui-tts .ui-tts-play { background: var(--ui-accent-soft); border-color: var(--ui-accent); color: var(--ui-text);"
        " min-width: 6.4rem; font-weight: 600; }"
        ".ui-tts .ui-tts-play:hover { background: var(--ui-accent); color: #fff; }"
        ".ui-tts-meio { flex: 1 1 12rem; display: flex; flex-direction: column; gap: .35rem; min-width: 10rem; margin: 0 .35rem; }"
        ".ui-tts-info { display: flex; align-items: center; gap: .45rem; color: var(--ui-muted); font-size: .8rem;"
        " font-variant-numeric: tabular-nums; }"
        ".ui-tts-barra { position: relative; height: 3px; border-radius: 999px; background: var(--ui-border-strong); overflow: hidden; }"
        # trilho: o que já foi gerado (claro) e o que já foi ouvido (acento)
        ".ui-tts-barra b, .ui-tts-barra i { position: absolute; left: 0; top: 0; height: 100%; width: 0; border-radius: 999px;"
        " transition: width .3s linear; }"
        ".ui-tts-barra b { background: var(--ui-accent-soft); }"
        ".ui-tts-barra i { background: linear-gradient(90deg, var(--ui-accent), var(--ui-accent-2)); }"
        # ondinha de "no ar", só enquanto toca
        ".ui-tts-onda { display: inline-flex; align-items: flex-end; gap: 2px; height: .8rem; }"
        ".ui-tts-onda s { width: 2px; height: 30%; border-radius: 1px; background: var(--ui-accent); text-decoration: none; opacity: .5; }"
        ".ui-tts.tocando .ui-tts-onda s { opacity: 1; animation: ui-tts-onda 1s ease-in-out infinite; }"
        ".ui-tts.tocando .ui-tts-onda s:nth-child(2) { animation-delay: -.35s; }"
        ".ui-tts.tocando .ui-tts-onda s:nth-child(3) { animation-delay: -.7s; }"
        "@keyframes ui-tts-onda { 0%, 100% { height: 30%; } 50% { height: 100%; } }"
        ".ui-tts select { background: none; color: var(--ui-muted); border: 1px solid var(--ui-border-strong);"
        " border-radius: 999px; padding: .25rem .5rem; font-size: .8rem; cursor: pointer; }"
        ".ui-tts select:hover { border-color: var(--ui-accent); color: var(--ui-text); }"
        "@media (prefers-reduced-motion: reduce) { .ui-tts.tocando .ui-tts-onda s { animation: none; }"
        " .ui-leitura p { transition: none !important; } }"
        # leitura acompanhada, nas cores de cada papel (escuro/sépia/claro): os
        # outros parágrafos desbotam em direção ao papel; o atual fica com a tinta
        # cheia, fundo levíssimo e um fio na cor da capitular à esquerda. Tudo em
        # sombra — margem/padding mudariam a quebra das páginas.
        ".ui-leitura p { transition: color .6s ease, background-color .6s ease, box-shadow .6s ease; border-radius: 2px; }"
        ".ui-leitura.ui-ouvindo p { color: color-mix(in srgb, var(--tinta, #dcd9e8) 42%, var(--papel, #12141b)); }"
        ".ui-leitura.ui-ouvindo p.capitular::first-letter { color: color-mix(in srgb, var(--capitular, #c9c1ff) 50%, var(--papel, #12141b)); }"
        ".ui-leitura p.ui-lendo, .ui-leitura.ui-ouvindo p.ui-lendo { color: var(--tinta, #dcd9e8);"
        " --realce: color-mix(in srgb, var(--capitular, #c9c1ff) 9%, var(--papel, #12141b));"
        " background: var(--realce); box-shadow: 0 0 0 .5em var(--realce), -3px 0 0 .5em var(--capitular, #c9c1ff); }"
        # no modo páginas a folha recorta o que passa da coluna; a margem de
        # recorte deixa o realce aparecer (a coluna vizinha está a 48px)
        ".ui-paginas { overflow: clip; overflow-clip-margin: .9em; }"
        ".ui-leitura.ui-ouvindo p.capitular.ui-lendo::first-letter { color: var(--capitular, #c9c1ff); }"
        "</style>"
        f'<div class="ui-tts" data-texto="{esc(texto_id)}" data-mapa="{esc(json.dumps(mapa or []))}">'
        '<button class="ui-tts-play" data-acao="alternar">▶ Ouvir</button>'
        '<button data-acao="voltar" title="Trecho anterior" aria-label="Trecho anterior">⏮</button>'
        '<button data-acao="avancar" title="Próximo trecho" aria-label="Próximo trecho">⏭</button>'
        '<div class="ui-tts-meio"><span class="ui-tts-info"><span class="ui-tts-onda"><s></s><s></s><s></s></span>'
        '<span class="ui-tts-texto">Preparando a voz…</span></span>'
        '<span class="ui-tts-barra"><b></b><i></i></span></div>'
        '<select data-acao="velocidade" title="Velocidade" aria-label="Velocidade">'
        '<option value="0.9">0,9×</option><option value="1" selected>1×</option>'
        '<option value="1.15">1,15×</option><option value="1.3">1,3×</option><option value="1.5">1,5×</option>'
        "</select></div>"
    )
    st.html(
        "<script>if(!window.__tts){var T=window.__tts={i:0,quer:false,vel:1,texto:null};"
        "T.lista=function(){return Array.from(document.querySelectorAll('.st-key-tts-blocos audio'));};"
        "T.parar=function(){T.lista().forEach(function(a){if(!a.paused)a.pause();});};"
        "T.tocar=function(i){T.fim=false;T.parar();T.i=Math.max(0,i);T.quer=true;var a=T.lista()[T.i];"
        "if(a){a.currentTime=0;a.playbackRate=T.vel;a.play().catch(function(){});}T.pintar();};"
        "T.alternar=function(){var a=T.lista()[T.i];"
        "if(T.quer){T.quer=false;if(a)a.pause();}else{T.quer=true;T.fim=false;if(a){a.playbackRate=T.vel;a.play().catch(function(){});}}T.pintar();};"
        "T.pular=function(d){T.tocar(T.i+d);};"
        # parágrafo sendo lido: fração tocada do bloco × tamanhos dos parágrafos dele
        "T.destacar=function(box){var art=document.querySelector('.ui-leitura');if(!art)return;"
        "if(T.mapaConto!==box.dataset.texto){try{T.mapa=JSON.parse(box.dataset.mapa||'[]');}catch(e){T.mapa=[];}T.mapaConto=box.dataset.texto;}"
        "var a=T.lista()[T.i],bl=T.mapa[T.i],alvo=null,fp=0;"
        "if(bl&&a&&!T.fim&&(T.quer||a.currentTime>0)){var tot=0;bl.forEach(function(x){tot+=x[1];});"
        "var d=a.duration,f=(d&&isFinite(d))?Math.min(1,a.currentTime/Math.max(0.1,d-0.25)):0,pos=f*tot,acc=0;"
        "for(var k=0;k!==bl.length;k++){if(acc+bl[k][1]>=pos||k===bl.length-1){alvo=bl[k][0];fp=bl[k][1]?Math.min(0.999,(pos-acc)/bl[k][1]):0;break;}acc+=bl[k][1];}}"
        "art.classList.toggle('ui-ouvindo',!!(T.quer&&alvo!==null));"
        "var ant=art.querySelector('p.ui-lendo'),el=alvo===null?null:art.querySelector('p[data-p=\"'+alvo+'\"]');"
        "if(ant&&ant!==el)ant.classList.remove('ui-lendo');if(!el)return;el.classList.add('ui-lendo');"
        "if(!T.quer)return;"
        # acompanhar: no modo páginas, vira até o pedaço do parágrafo (um retângulo
        # por coluna) que está sendo lido; na rolagem, centraliza quando sai da vista
        "var txt=document.getElementById('ui-texto');"
        "if(window.__uiPagIrX&&txt&&art===txt){var rs=el.getClientRects(),n=Math.max(1,rs.length),j=Math.floor(fp*n);"
        "var ch=alvo+':'+j;if(ch!==T.seguido){T.seguido=ch;var r=rs[Math.min(j,rs.length-1)]||el.getBoundingClientRect();"
        "window.__uiPagIrX(r.left-txt.getBoundingClientRect().left+1);}}"
        "else if(String(alvo)!==T.seguido){T.seguido=String(alvo);var b=el.getBoundingClientRect();"
        "if(b.top>window.innerHeight*0.7||90>b.top)el.scrollIntoView({behavior:'smooth',block:'center'});}};"
        "T.velocidade=function(v){T.vel=parseFloat(v);T.lista().forEach(function(a){a.playbackRate=T.vel;});};"
        # o sanitizador do st.html remove onclick/onchange: cliques por delegação
        "document.addEventListener('click',function(e){var b=e.target.closest&&e.target.closest('.ui-tts button[data-acao]');"
        "if(!b)return;var ac=b.dataset.acao;if(ac==='alternar')T.alternar();else T.pular(ac==='voltar'?-1:1);});"
        "document.addEventListener('change',function(e){if(e.target.matches&&e.target.matches('.ui-tts select'))T.velocidade(e.target.value);});"
        "document.addEventListener('ended',function(e){var a=e.target;"
        "if(a.closest&&a.closest('.st-key-tts-blocos')&&T.quer){T.tocar(T.lista().indexOf(a)+1);}},true);"
        "T.pintar=function(){var box=document.querySelector('.ui-tts');if(!box)return;"
        "var st=document.querySelector('.st-key-tts-blocos .ui-tts-status'),n=T.lista().length;"
        "var total=st?parseInt(st.dataset.total||'0',10):0,erro=st?st.dataset.erro:'';"
        "var info=box.querySelector('.ui-tts-texto'),bt=box.querySelector('.ui-tts-play');"
        # trilho: gerado (claro) e ouvido (acento, com a fração do trecho atual)
        "var a=T.lista()[T.i],fr=(a&&a.duration&&isFinite(a.duration))?Math.min(1,a.currentTime/a.duration):0;"
        "var tb=box.querySelector('.ui-tts-barra b'),ti=box.querySelector('.ui-tts-barra i');"
        "if(tb)tb.style.width=(total?n/total*100:0)+'%';"
        "if(ti)ti.style.width=(total?(T.fim?100:Math.min(total,T.i+fr)/total*100):0)+'%';"
        "box.classList.toggle('tocando',!!(T.quer&&a&&!a.paused));"
        # chegou ao fim: volta pro começo, parado
        "if(total&&n===total&&T.i>=total){T.quer=false;T.i=0;T.fim=true;box.classList.remove('tocando');}"
        "if(T.fim&&!T.quer){info.textContent='Fim ✦';bt.textContent='▶ Ouvir de novo';return;}"
        "bt.textContent=T.quer?'❚❚ Pausar':'▶ Ouvir';"
        "if(erro){info.textContent='Erro: '+erro;return;}"
        "if(!total){info.textContent='Preparando a voz…';return;}"
        "var txt='Trecho '+Math.min(T.i+1,total)+' de '+total;"
        "if(n!==total)txt+=' · gerando '+n+'/'+total;"
        "if(T.quer&&T.i>=n)txt+=' · aguardando o próximo trecho…';"
        "info.textContent=txt;};"
        "setInterval(function(){var box=document.querySelector('.ui-tts');"
        "if(!box){if(T.texto){T.parar();T.texto=null;T.quer=false;}return;}"
        "if(box.dataset.texto!==T.texto){T.parar();T.texto=box.dataset.texto;T.i=0;T.quer=false;T.fim=false;T.seguido=null;}"
        "var a=T.lista()[T.i];if(T.quer&&a&&a.paused&&!a.ended){a.playbackRate=T.vel;a.play().catch(function(){});}"
        "T.pintar();T.destacar(box);},300);}</script>",
        unsafe_allow_javascript=True,
    )


def status_audio(total: int | None, prontos: int, erro: str | None) -> None:
    """Estado da geração, lido pelo player (vai dentro do container escondido)."""
    st.html(f'<div class="ui-tts-status" data-total="{total or 0}" data-prontos="{prontos}" '
            f'data-erro="{esc(erro or "")}"></div>')
