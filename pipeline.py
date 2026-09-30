"""Filas do projeto sobre o CrevoMQ (../CrevoMQ): claim atômico, lease com
heartbeat, retry com backoff, pausa e estatísticas ficam todos na lib.

- `textos`: extracao -> embedding -> indexacao. A descoberta (sitemap) é só
  produtora: insere o texto em `textos` e enfileira o job em `extracao`.
- `delecao`: remocao. Recebe os textos descartados (descarte.py) — tanto os
  novos, desviados na extração, quanto os da base existente
  (scripts/descartar.py).
- `series`: agrupar -> vetorizar (series.py). Job por lote de autores:
  recalcula as séries deles. Disparado por texto novo indexado, por texto
  descartado e, pra base inteira, por scripts/series.py.
- `audio`: sintese (busca/audio.py). Pré-gera a leitura em voz alta: a
  próxima parte da série de quem está ouvindo (prioridade alta) e os textos
  marcados com "Gostei". Disparado pelo app; a base de "Gostei" existente,
  por scripts/pregerar_audio.py.
- `busca_v2`: vetores -> importacao (busca_v2/fila.py). Job por parte do
  pacote do busca_v2/exportar.py, enfileirado por busca_v2/enfileirar.py.
- `busca_v2_contos`: indexacao. O mesmo trabalho, um job por texto (lido
  direto do contos.db) — pra ver no painel quanto a máquina entrega.

O id do job é derivado do slug do texto, então enfileirar de novo é
idempotente. Quem processa cada estágio são os handlers listados em
supervisor.toml.

`textos.estagio` deixou de ser o estado do pipeline (isso agora mora no
FILA_DB_PATH): só vale 'concluido' — o que o app de busca mostra — ou
'descoberta' — ainda atravessando as filas."""

import hashlib
import time
from pathlib import Path
from functools import cache

from crevomq import Fila, Worker
from crevomq.supervisor import carregar_handler

from config import DB_PATH, FILA_DB_PATH, LOTE_PADRAO, MAX_TENTATIVAS
from db import conectar

FILA_TEXTOS = "contos"
FILA_DELECAO = "delecao"
FILA_SERIES = "series"
ESTAGIOS_TEXTOS = ["extracao", "embedding", "indexacao"]
ESTAGIOS_DELECAO = ["remocao"]
ESTAGIOS_SERIES = ["agrupar", "vetorizar"]
FILA_AUDIO = "audio"
ESTAGIOS_AUDIO = ["sintese"]
PRIORIDADE_PROXIMA_PARTE = 10
PRIORIDADE_GOSTEI = 0
FILA_BUSCA_V2 = "busca_v2"
ESTAGIOS_BUSCA_V2 = ["vetores", "importacao"]
FILA_BUSCA_V2_TEXTOS = "busca_v2_contos"
ESTAGIOS_BUSCA_V2_TEXTOS = ["indexacao"]

# Mesmos handlers do supervisor.toml (lá como texto, pro crevomq-supervisor).
HANDLERS = {
    (FILA_TEXTOS, "extracao"): "scraper.extrair:extrair",
    (FILA_TEXTOS, "embedding"): "embeddings.gerar:gerar",
    (FILA_TEXTOS, "indexacao"): "embeddings.enviar_qdrant:indexar",
    (FILA_DELECAO, "remocao"): "descarte:remover",
    (FILA_SERIES, "agrupar"): "series:agrupar_autores",
    (FILA_SERIES, "vetorizar"): "series:vetorizar",
    (FILA_AUDIO, "sintese"): "busca.audio:sintetizar_job",
    (FILA_BUSCA_V2, "vetores"): "busca_v2.fila:vetores",
    (FILA_BUSCA_V2, "importacao"): "busca_v2.fila:importacao",
    (FILA_BUSCA_V2_TEXTOS, "indexacao"): "busca_v2.fila:conto",
}

# Onde retomar um texto que o banco ainda marca como fora do catálogo (ver
# `migrar_pendentes`). Só 'descoberta' é gravado hoje; os outros vêm de bancos
# da época da fila própria, que avançava textos.estagio a cada etapa.
_ESTAGIO_LEGADO = {"descoberta": "extracao", "extracao": "extracao",
                   "embedding": "embedding", "indexacao": "indexacao"}


@cache
def fila_textos(db_path: str = FILA_DB_PATH) -> Fila:
    # Disjuntor: se o site começar a recusar (bloqueio/WAF), a extração pausa
    # sozinha em vez de queimar as tentativas de todo o backlog.
    return Fila(db_path, FILA_TEXTOS, ESTAGIOS_TEXTOS, max_tentativas=MAX_TENTATIVAS,
                lease_s=300, disjuntor=True)


@cache
def fila_delecao(db_path: str = FILA_DB_PATH) -> Fila:
    return Fila(db_path, FILA_DELECAO, ESTAGIOS_DELECAO, max_tentativas=MAX_TENTATIVAS, lease_s=120)


@cache
def fila_series(db_path: str = FILA_DB_PATH) -> Fila:
    return Fila(db_path, FILA_SERIES, ESTAGIOS_SERIES, max_tentativas=MAX_TENTATIVAS, lease_s=300)


@cache
def fila_audio(db_path: str = FILA_DB_PATH) -> Fila:
    # Um texto longo leva ~20 min de CPU; o heartbeat do worker renova o lease.
    return Fila(db_path, FILA_AUDIO, ESTAGIOS_AUDIO, max_tentativas=3, lease_s=600)


@cache
def fila_busca_v2(db_path: str = FILA_DB_PATH) -> Fila:
    # Uma parte (1.000 textos) passa de 15 min na CPU; o heartbeat renova o lease.
    return Fila(db_path, FILA_BUSCA_V2, ESTAGIOS_BUSCA_V2, max_tentativas=3, lease_s=900)


@cache
def fila_busca_v2_contos(db_path: str = FILA_DB_PATH) -> Fila:
    # Ordenação inteligente: aprende a duração pelo payload (`tamanho` do
    # texto pesa quase tudo) e entrega os rápidos primeiro, com envelhecimento.
    return Fila(db_path, FILA_BUSCA_V2_TEXTOS, ESTAGIOS_BUSCA_V2_TEXTOS, max_tentativas=3, lease_s=300,
                ordenacao="rapido")


def registrar(db_path: str = FILA_DB_PATH) -> None:
    """Cria/atualiza as filas no banco — o supervisor e o painel as abrem
    pela configuração salva (`Fila.abrir`)."""
    filas(db_path)


def filas(db_path: str = FILA_DB_PATH) -> dict[str, Fila]:
    return {FILA_TEXTOS: fila_textos(db_path), FILA_DELECAO: fila_delecao(db_path),
            FILA_SERIES: fila_series(db_path), FILA_AUDIO: fila_audio(db_path),
            FILA_BUSCA_V2: fila_busca_v2(db_path), FILA_BUSCA_V2_TEXTOS: fila_busca_v2_contos(db_path)}


def restantes(db_path: str = FILA_DB_PATH) -> int:
    """Jobs pendentes ou em processamento em todas as filas (inclui os que
    esperam o backoff de uma nova tentativa)."""
    return sum(n for fila in filas(db_path).values() for contagem in fila.contagens().values()
               for status, n in contagem.items() if status in ("pendente", "processando"))


def drenar(lote: int = LOTE_PADRAO, espera_s: float = 1.0, log=print) -> None:
    """Roda todos os estágios em sequência, no próprio processo, até as filas
    esvaziarem — pra rodadas únicas (scraper/rodar.py, pipeline mock). O
    uso contínuo é o supervisor (supervisor.toml), com processos em paralelo."""
    while True:
        for (nome, estagio), handler in HANDLERS.items():
            fila = filas()[nome]
            antes = fila.contagens().get(estagio, {})
            if not antes.get("pendente") and not antes.get("processando"):
                continue
            Worker(fila, estagio, carregar_handler(handler), lote=lote,
                   espera_ociosa_s=0).rodar(max_ciclos_ociosos=1)
            depois = fila.contagens().get(estagio, {})
            if depois != antes:  # só o que ainda espera backoff não mexe na contagem
                log(f"[{nome}/{estagio}] pendentes={depois.get('pendente', 0)} erro={depois.get('erro', 0)}")
        if not restantes():
            return
        time.sleep(espera_s)  # o que sobrou está esperando o backoff


def enfileirar_texto(texto_id: str, url: str, estagio: str = "extracao",
                     db_path: str = FILA_DB_PATH) -> None:
    fila_textos(db_path).enfileirar({"url": url}, id=texto_id, estagio=estagio)


def enfileirar_delecao(texto_id: str, url: str | None, motivo: str, db_path: str = FILA_DB_PATH) -> None:
    """O id do job é único no banco inteiro, não por fila — o slug puro já é
    o do job em `contos` e o INSERT seria ignorado. Daí o sufixo; o slug vai
    no payload."""
    fila_delecao(db_path).enfileirar({"conto_id": texto_id, "url": url, "motivo": motivo},
                                     id=f"{texto_id}@{FILA_DELECAO}")


def enfileirar_delecao_lote(itens: list[tuple[str, str | None, str]], db_path: str = FILA_DB_PATH) -> None:
    """Um job pra vários textos (texto_id, url, motivo): o handler apaga o
    lote numa chamada ao Qdrant e numa transação no SQLite. Id derivado dos
    slugs, então o mesmo lote enfileirado de novo é ignorado."""
    ids = [texto_id for texto_id, _, _ in itens]
    assinatura = hashlib.sha1("\n".join(ids).encode()).hexdigest()[:16]
    fila_delecao(db_path).enfileirar(
        {"contos": [{"conto_id": c, "url": u, "motivo": m} for c, u, m in itens]},
        id=f"lote-{assinatura}@{FILA_DELECAO}",
    )


def enfileirar_series(autores: list[str], id: str | None = None, db_path: str = FILA_DB_PATH) -> None:
    """Recalcular as séries destes autores. Diferente das outras filas, o
    mesmo autor precisa ser recalculado de novo a cada texto que entra ou
    sai — por isso o id padrão leva o instante, não só os autores (um id já
    concluído seria ignorado)."""
    autores = sorted({a for a in autores if a})
    if not autores:
        return
    if id is None:
        assinatura = hashlib.sha1("\n".join(autores).encode()).hexdigest()[:12]
        id = f"lote-{assinatura}-{time.time_ns()}"
    fila_series(db_path).enfileirar({"autores": autores}, id=f"{id}@{FILA_SERIES}")


def migrar_pendentes(db_path: str = DB_PATH, fila_db_path: str = FILA_DB_PATH) -> int:
    """Enfileira textos fora do catálogo que ainda não têm job. Idempotente:
    quem já está na fila (em qualquer estágio/status) é ignorado pelo id."""
    fila = fila_textos(fila_db_path)
    with conectar(db_path) as conn:
        linhas = conn.execute(
            "SELECT id, url, estagio FROM contos WHERE estagio != 'concluido'"
        ).fetchall()
    novos = 0
    for linha in linhas:
        if fila.obter(linha["id"]) is None:
            fila.enfileirar({"url": linha["url"]}, id=linha["id"],
                            estagio=_ESTAGIO_LEGADO.get(linha["estagio"], "extracao"))
            novos += 1
    return novos


def enfileirar_audio(texto_id: str, prioridade: int = PRIORIDADE_GOSTEI,
                     db_path: str = FILA_DB_PATH) -> bool:
    """Pré-gerar o áudio do conto na voz atual. Não enfileira o que já está
    pronto no cache. O id leva motor e voz (trocar de voz gera de novo); se o
    job já foi concluído mas o áudio saiu do cache (limpeza LRU), um id novo
    — senão o INSERT seria ignorado. Devolve se enfileirou."""
    from busca import audio
    from config import TTS_MOTOR, TTS_VOZ

    if (audio.pasta(texto_id) / "completo").exists():
        return False
    fila = fila_audio(db_path)
    id = f"{texto_id}@{FILA_AUDIO}-{TTS_MOTOR}-{TTS_VOZ}"
    anterior = fila.obter(id)
    if anterior and anterior["status"] in ("pendente", "processando"):
        return False
    if anterior:
        id = f"{id}-{time.time_ns()}"
    fila.enfileirar({"conto_id": texto_id}, id=id, prioridade=prioridade)
    return True


def enfileirar_busca_v2(pasta: str, parte: int, db_path: str = FILA_DB_PATH) -> None:
    """Id pela pasta e parte: enfileirar o mesmo pacote de novo é ignorado."""
    fila_busca_v2(db_path).enfileirar({"pasta": pasta, "parte": parte},
                                      id=f"{Path(pasta).name}-{parte:05d}@{FILA_BUSCA_V2}")


def enfileirar_busca_v2_texto(texto_id: str, colecao: str, tamanho: int,
                              db_path: str = FILA_DB_PATH) -> None:
    """`tamanho` (caracteres do texto) é o que a ordenação inteligente usa
    pra prever a duração."""
    fila_busca_v2_contos(db_path).enfileirar({"conto_id": texto_id, "colecao": colecao, "tamanho": tamanho},
                                             id=f"{texto_id}@{FILA_BUSCA_V2_TEXTOS}")
