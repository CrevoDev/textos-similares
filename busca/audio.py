"""Leitura em voz alta (Fase 11): síntese local, em blocos, com cache em disco.

O Kokoro na CPU gera pouco mais rápido que o tempo real, então o texto não é
sintetizado inteiro antes de tocar: ele é dividido em blocos (o 1º bem curto,
pra começar a tocar em segundos) e uma thread em segundo plano grava um
arquivo por bloco em `TTS_CACHE_DIR/<motor>-<voz>/<texto_id>/NNN.ogg`. O app
toca o que já existe e emenda os blocos seguintes conforme aparecem. Um
arquivo `completo` marca o texto pronto; um texto interrompido continua de
onde parou (blocos já gravados não são refeitos).

Uma thread só, um texto por vez: pedir outro texto troca o pedido e a geração
muda para ele no fim do bloco atual. Fora do app, a fila `audio` do CrevoMQ
(`sintetizar_job`, ver pipeline.py) pré-gera a próxima parte da série e os
textos marcados com "Gostei". Os dois processos não geram o mesmo texto ao
mesmo tempo: quem está gerando mantém um arquivo `gerando` atualizado a cada
bloco, e o outro espera (ou assume, se ele ficar velho — processo morto). Os motores (Kokoro, Piper) ficam atrás de
`sintetizar(texto) -> (amostras float, taxa)`; os testes trocam por um falso."""

import logging
import os
import re
import shutil
import threading
import time
import queue
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

import config
from busca import leitura

log = logging.getLogger(__name__)

# ------------------------------------------------------------ normalização

_ABREVIACOES = {
    "dr": "doutor", "dra": "doutora", "sr": "senhor", "sra": "senhora", "srta": "senhorita",
    "av": "avenida", "prof": "professor", "profa": "professora",
}
_RE_ABREV = re.compile(r"\b(" + "|".join(_ABREVIACOES) + r")\.", re.IGNORECASE)
_RE_HORA = re.compile(r"\b(\d{1,2})h(\d{2})?\b")
_RE_MILHAR = re.compile(r"(?<=\d)\.(?=\d{3}\b)")
_RE_RISADA = re.compile(r"\b(?:k{3,}|(?:ha){3,}|(?:rs){2,})\b", re.IGNORECASE)


def _abreviacao(m: re.Match) -> str:
    extenso = _ABREVIACOES[m.group(1).lower()]
    return extenso.capitalize() if m.group(1)[0].isupper() else extenso


def _hora(m: re.Match) -> str:
    return f"{m.group(1)} e {m.group(2)}" if m.group(2) else f"{m.group(1)} horas"


def normalizar(texto: str) -> str:
    """Deixa o texto do jeito que o motor lê melhor: abreviações por extenso,
    "19h" como "19 horas", "2.500" sem o ponto (o espeak leria "dois ponto
    quinhentos"), travessão de diálogo virando pausa, risadas fora."""
    t = texto.strip()
    t = re.sub(r"^[—–-]\s*", "", t)                 # fala abrindo o parágrafo
    t = re.sub(r"\s+[—–]\s*", ", ", t)              # deixa do narrador: "— disse ela —"
    t = _RE_ABREV.sub(_abreviacao, t)
    t = _RE_HORA.sub(_hora, t)
    t = _RE_MILHAR.sub("", t)
    t = _RE_RISADA.sub("", t)
    t = re.sub(r"([!?])[!?]+", r"\1", t)            # "!!!" / "?!?"
    t = re.sub(r"\.{4,}", "...", t)
    t = re.sub(r",\s*([,.!?])", r"\1", t)           # vírgula sobrando antes de pontuação
    t = re.sub(r"([.!?…])\s*,", r"\1", t)          # ...ou depois: "tempo?, perguntou"
    t = re.sub(r"^[,\s]+", "", t)
    return re.sub(r"\s+", " ", t).strip()


# ------------------------------------------------------------------ blocos

_RE_FRASE = re.compile(r"(?<=[.!?…])\s+")


def _frases(paragrafo: str, limite: int) -> list[str]:
    """Quebra um parágrafo maior que `limite` em frases (e, se uma frase
    sozinha passar do limite, nas vírgulas)."""
    if len(paragrafo) <= limite:
        return [paragrafo]
    saida = []
    for frase in _RE_FRASE.split(paragrafo):
        if len(frase) <= limite:
            saida.append(frase)
        else:
            saida.extend(p.strip() for p in re.split(r"(?<=,)\s+", frase) if p.strip())
    return saida


def _montar(texto: str | None, primeiro: int | None, demais: int | None) -> list[list[tuple[int, str]]]:
    """Blocos como listas de (índice do parágrafo em leitura.paragrafos, pedaço
    normalizado) — o índice é o que liga o áudio ao <p> na página de leitura."""
    primeiro = primeiro or config.TTS_PRIMEIRO_BLOCO
    demais = demais or config.TTS_BLOCO
    pedacos = []
    for i, p in enumerate(leitura.paragrafos(texto)):
        p = normalizar(p)
        if p:
            pedacos.extend((i, f) for f in _frases(p, demais))
    saida: list[list[tuple[int, str]]] = []
    atual: list[tuple[int, str]] = []
    tamanho = 0
    for i, pedaco in pedacos:
        # blocos crescentes: o 1º curto (som em segundos), cada um ~1,6× o
        # anterior até o teto — o próximo fica pronto antes do atual acabar de
        # tocar, e o resto do texto aproveita os blocos grandes (mais rápidos)
        limite = min(demais, int(primeiro * config.TTS_CRESCIMENTO ** len(saida)))
        if atual and tamanho + 1 + len(pedaco) > limite:
            saida.append(atual)
            atual, tamanho = [], 0
        atual.append((i, pedaco))
        tamanho += len(pedaco) + (1 if tamanho else 0)
    if atual:
        saida.append(atual)
    return saida


def blocos(texto: str | None, primeiro: int | None = None, demais: int | None = None) -> list[str]:
    """Texto normalizado dividido em blocos crescentes: o 1º com até
    `primeiro` caracteres, cada seguinte TTS_CRESCIMENTO vezes maior, até
    `demais`; cortes só em fim de parágrafo ou de frase."""
    return [" ".join(p for _, p in b) for b in _montar(texto, primeiro, demais)]


def mapa(texto: str | None) -> list[list[list[int]]]:
    """Para o destaque na leitura: por bloco, [[parágrafo, caracteres], ...]
    (pedaços seguidos do mesmo parágrafo somados). O player estima o
    parágrafo que está sendo lido pela fração já tocada do bloco."""
    saida = []
    for bloco in _montar(texto, None, None):
        partes: list[list[int]] = []
        for i, pedaco in bloco:
            if partes and partes[-1][0] == i:
                partes[-1][1] += len(pedaco) + 1
            else:
                partes.append([i, len(pedaco)])
        saida.append(partes)
    return saida


# ------------------------------------------------------------------ motores

class Kokoro:
    def __init__(self, threads: int = 0) -> None:
        import onnxruntime as rt
        from kokoro_onnx import Kokoro as _Kokoro
        pasta = Path(config.TTS_MODELO_DIR)
        modelo, vozes = pasta / config.TTS_MODELO, pasta / "voices-v1.0.bin"
        if not modelo.exists() or not vozes.exists():
            raise FileNotFoundError(f"Modelo Kokoro não encontrado em {pasta} — rode scripts/baixar_voz.py")
        opcoes = rt.SessionOptions()
        opcoes.intra_op_num_threads = threads  # 0 = o onnxruntime decide
        sessao = rt.InferenceSession(str(modelo), opcoes, providers=["CPUExecutionProvider"])
        self._k = _Kokoro.from_session(sessao, str(vozes))

    def sintetizar(self, texto: str) -> tuple[np.ndarray, int]:
        return self._k.create(texto, voice=config.TTS_VOZ, speed=config.TTS_VELOCIDADE, lang="pt-br")


class Piper:
    """Não paraleliza (TTS_PARALELO é ignorado) — já é ~10× o tempo real.
Plano B (mais rápido, voz menos natural). Precisa de `uv add piper-tts`
    e da voz `pt_BR-<voz>-medium.onnx` (+ .json) em TTS_MODELO_DIR."""

    def __init__(self, threads: int = 0) -> None:
        from piper import PiperVoice, SynthesisConfig
        caminho = Path(config.TTS_MODELO_DIR) / f"pt_BR-{config.TTS_VOZ}-medium.onnx"
        if not caminho.exists():
            raise FileNotFoundError(f"Voz Piper não encontrada: {caminho}")
        self._voz = PiperVoice.load(str(caminho))
        self._cfg = SynthesisConfig(length_scale=1 / config.TTS_VELOCIDADE)

    def sintetizar(self, texto: str) -> tuple[np.ndarray, int]:
        partes = list(self._voz.synthesize(texto, syn_config=self._cfg))
        if not partes:
            return np.zeros(0, dtype=np.float32), self._voz.config.sample_rate
        return np.concatenate([p.audio_float_array for p in partes]), partes[0].sample_rate


_MOTORES = {"kokoro": Kokoro, "piper": Piper}


@lru_cache(maxsize=1)
def motores() -> list:
    """Uma instância por bloco gerado em paralelo, carregadas só no primeiro
    "Ouvir". Uma inferência do Kokoro não escala com threads (medido: 8
    threads rendem menos que o automático), mas sessões paralelas sim — na
    CPU de 8 núcleos, 1 sessão ≈ 1,5× o tempo real, 2 × 4 threads ≈ 2×,
    3 × 3 ≈ 2,2×. Cada sessão custa ~300–500 MB de RAM."""
    n = 1 if config.TTS_MOTOR == "piper" else max(1, config.TTS_PARALELO)
    threads = 0 if n == 1 else max(1, (os.cpu_count() or 4) // n)
    return [_MOTORES[config.TTS_MOTOR](threads) for _ in range(n)]


# ------------------------------------------------------------------- cache

def pasta(texto_id: str) -> Path:
    return Path(config.TTS_CACHE_DIR) / f"{config.TTS_MOTOR}-{config.TTS_VOZ}" / texto_id


# Opus só aceita estas taxas; outra (a do Piper é 22050) vai em Vorbis.
_TAXAS_OPUS = {8000, 12000, 16000, 24000, 48000}
_PAUSA_S = 0.25  # silêncio no fim de cada bloco: a emenda entre eles não fica colada


def _gravar(destino: Path, amostras: np.ndarray, taxa: int) -> None:
    import soundfile as sf
    amostras = np.concatenate([np.asarray(amostras, dtype=np.float32),
                               np.zeros(int(taxa * _PAUSA_S), dtype=np.float32)])
    # nome do .tmp por processo/thread: app e worker podem gravar na mesma pasta
    tmp = destino.with_name(f"{destino.stem}.{os.getpid()}-{threading.get_ident()}.tmp")
    sf.write(tmp, amostras, taxa, format="OGG", subtype="OPUS" if taxa in _TAXAS_OPUS else "VORBIS")
    # No Windows o antivírus/indexador às vezes segura o arquivo recém-criado
    # por um instante (WinError 32) — tenta de novo antes de desistir.
    for tentativa in range(10):
        try:
            os.replace(tmp, destino)
            return
        except PermissionError:
            if tentativa == 9:
                raise
            time.sleep(0.2 * (tentativa + 1))


@dataclass
class Estado:
    blocos: list[Path]          # prontos, em ordem (só os contíguos desde o 1º)
    total: int | None           # None = ainda não dividiu o texto
    completo: bool
    gerando: bool
    erro: str | None


def estado(texto_id: str) -> Estado:
    p = pasta(texto_id)
    total = None
    if (p / "total").exists():
        total = int((p / "total").read_text())
    prontos = []
    while (p / f"{len(prontos):03d}.ogg").exists():
        prontos.append(p / f"{len(prontos):03d}.ogg")
    completo = (p / "completo").exists()
    if completo:
        try:
            os.utime(p)  # "usado agora" para a limpeza por LRU
        except OSError:
            pass
    with _lock:
        gerando = _pedido is not None and _pedido[0] == texto_id
        erro = _erros.get(texto_id)
    gerando = gerando or (not completo and ocupado(texto_id))
    return Estado(prontos, total, completo, gerando, erro)


# Um bloco leva até ~1 min no Kokoro; sem sinal de vida por 3 min, o dono morreu.
TRAVA_VELHA_S = 180


def _dono() -> str:
    return f"{os.getpid()}-{threading.get_ident()}"


def ocupado(texto_id: str) -> bool:
    """Outro processo/thread está gerando este texto agora."""
    trava = pasta(texto_id) / "gerando"
    try:
        return (time.time() - trava.stat().st_mtime < TRAVA_VELHA_S
                and trava.read_text() != _dono())
    except OSError:
        return False


def limpar_cache(manter: str | None = None) -> int:
    """Apaga os textos menos usados até o cache caber em TTS_CACHE_MAX_GB.
    Devolve quantos textos saíram."""
    raiz = Path(config.TTS_CACHE_DIR)
    if not raiz.exists():
        return 0
    docs = [d for voz in raiz.iterdir() if voz.is_dir() for d in voz.iterdir() if d.is_dir()]
    tamanhos = {d: sum(f.stat().st_size for f in d.iterdir() if f.is_file()) for d in docs}
    total = sum(tamanhos.values())
    teto = config.TTS_CACHE_MAX_GB * 1024 ** 3
    removidos = 0
    for d in sorted(docs, key=lambda d: d.stat().st_mtime):
        if total <= teto:
            break
        if d.name == manter:
            continue
        shutil.rmtree(d, ignore_errors=True)
        total -= tamanhos[d]
        removidos += 1
    return removidos


# ------------------------------------------------------------------ geração

_lock = threading.Lock()
_evento = threading.Event()
_pedido: tuple[str, str] | None = None
_thread: threading.Thread | None = None
_erros: dict[str, str] = {}


def iniciar(texto_id: str, texto: str) -> None:
    """Pede a geração do texto (não bloqueia). Sem efeito se já está pronto."""
    global _pedido, _thread
    if (pasta(texto_id) / "completo").exists():
        return
    with _lock:
        _erros.pop(texto_id, None)
        _pedido = (texto_id, texto)
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_trabalhar, daemon=True, name="tts")
            _thread.start()
    _evento.set()


def _trabalhar() -> None:
    global _pedido
    while True:
        _evento.wait()
        with _lock:
            pedido = _pedido
            _evento.clear()
        if pedido is None:
            continue
        try:
            gerar(*pedido, parar=lambda: _pedido is not pedido)
        except Exception as e:  # noqa: BLE001 — o erro vai pra tela
            log.exception("TTS falhou para %s", pedido[0])
            with _lock:
                _erros[pedido[0]] = str(e) or e.__class__.__name__
        with _lock:
            if _pedido is pedido:
                _pedido = None


def _sintetizar_bloco(m, p: Path, i: int, parte: str) -> None:
    amostras, taxa = m.sintetizar(parte)
    _gravar(p / f"{i:03d}.ogg", amostras, taxa)


def gerar(texto_id: str, texto: str, parar=lambda: False) -> bool:
    """Gera os blocos que faltam; devolve True se o texto ficou completo.
    `parar()` é consultado entre blocos (troca de texto). Se outro processo
    já está gerando o texto, não faz nada e devolve False."""
    p = pasta(texto_id)
    if (p / "completo").exists():
        return True
    if ocupado(texto_id):
        return False
    p.mkdir(parents=True, exist_ok=True)
    trava = p / "gerando"
    trava.write_text(_dono())
    try:
        partes = blocos(texto)
        (p / "total").write_text(str(len(partes)))
        inicio = time.perf_counter()
        faltam = [(i, parte) for i, parte in enumerate(partes) if not (p / f"{i:03d}.ogg").exists()]
        # fila contínua: cada sessão pega o próximo bloco (em ordem) assim que
        # termina o seu — em janelas, uma sessão ficava parada esperando a outra
        livres: queue.Queue = queue.Queue()
        for m in motores():
            livres.put(m)
        parou = threading.Event()

        def tarefa(i: int, parte: str) -> None:
            m = livres.get()
            try:
                if parou.is_set() or parar():
                    parou.set()
                    return
                _sintetizar_bloco(m, p, i, parte)
            finally:
                livres.put(m)

        with ThreadPoolExecutor(livres.qsize()) as ex:
            for f in as_completed([ex.submit(tarefa, i, parte) for i, parte in faltam]):
                f.result()
                trava.write_text(_dono())  # sinal de vida
        if parou.is_set():
            return False
        (p / "completo").touch()
    finally:
        trava.unlink(missing_ok=True)
    log.info("TTS %s: %d blocos em %.0fs", texto_id, len(partes), time.perf_counter() - inicio)
    limpar_cache(manter=texto_id)
    return True


# ------------------------------------------------------------ fila `audio`

def sintetizar_job(job: dict) -> dict:
    """Handler do estágio `sintese` da fila `audio` (pipeline.py). Se o app
    estiver gerando o mesmo texto, espera ele terminar — levantar erro aqui
    gastaria as tentativas do job à toa."""
    from db import conectar

    texto_id = job["payload"]["conto_id"]
    with conectar(config.DB_PATH) as conn:
        linha = conn.execute("SELECT texto_completo FROM contos WHERE id = ?", (texto_id,)).fetchone()
    if not linha or not linha["texto_completo"]:
        return {**job["payload"], "ignorado": "sem texto"}
    while not gerar(texto_id, linha["texto_completo"]):
        time.sleep(15)
    return job["payload"]
