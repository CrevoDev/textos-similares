import threading

import numpy as np
import pytest

import config
from busca import audio


@pytest.mark.parametrize("entrada, saida", [
    ("— Você vai sair? — perguntou a mãe.", "Você vai sair? perguntou a mãe."),
    ("O Dr. Paulo e a Sra. Ana.", "O Doutor Paulo e a Senhora Ana."),
    ("Na av. Paulista às 19h.", "Na avenida Paulista às 19 horas."),
    ("Saí às 7h30 em ponto.", "Saí às 7 e 30 em ponto."),
    ("Eram 2.500 metros.", "Eram 2500 metros."),
    ("Que isso!!! kkkkk", "Que isso!"),
    ("Espera.....", "Espera..."),
])
def test_normalizar(entrada, saida):
    assert audio.normalizar(entrada) == saida


def test_blocos_primeiro_curto_e_limite():
    texto = "\n\n".join(f"Frase número {i} do parágrafo, com algum texto a mais." for i in range(40))
    partes = audio.blocos(texto, primeiro=60, demais=300)
    assert len(partes[0]) <= 60
    assert all(len(p) <= 300 for p in partes)
    assert " ".join(partes).count("Frase número") == 40


def test_blocos_crescem_ate_o_teto(monkeypatch):
    monkeypatch.setattr(config, "TTS_CRESCIMENTO", 2.0)
    texto = " ".join(f"Frase curta de número {i}." for i in range(300))
    tamanhos = [len(p) for p in audio.blocos(texto, primeiro=100, demais=700)]
    assert tamanhos[0] <= 100 and tamanhos[1] <= 200 and tamanhos[2] <= 400
    assert tamanhos[1] > tamanhos[0] and tamanhos[2] > tamanhos[1]
    assert max(tamanhos) <= 700 and tamanhos[4] > 400  # chegou perto do teto


def test_blocos_paragrafo_gigante_quebra_em_frases():
    texto = " ".join(["Uma frase curta."] * 100)
    partes = audio.blocos(texto, primeiro=50, demais=200)
    assert len(partes) > 5 and all(len(p) <= 200 for p in partes)


def test_blocos_vazio():
    assert audio.blocos("") == [] and audio.blocos(None) == []


class MotorFalso:
    def __init__(self):
        self.chamadas = []

    def sintetizar(self, texto):
        self.chamadas.append(texto)
        return np.zeros(2400, dtype=np.float32), 24000


@pytest.fixture
def falso(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TTS_CACHE_DIR", str(tmp_path / "audio"))
    monkeypatch.setattr(config, "TTS_PRIMEIRO_BLOCO", 40)
    monkeypatch.setattr(config, "TTS_BLOCO", 80)
    m = MotorFalso()
    monkeypatch.setattr(audio, "motores", lambda: [m, m])
    return m


TEXTO = "\n\n".join(f"Parágrafo {i}, que tem umas palavras." for i in range(10))


def test_gerar_grava_blocos_e_marca_completo(falso):
    assert audio.gerar("c1", TEXTO)
    e = audio.estado("c1")
    assert e.completo and e.total == len(e.blocos) == len(falso.chamadas) > 1
    assert all(b.stat().st_size > 0 for b in e.blocos)


def test_gerar_retoma_sem_refazer(falso):
    n = [0]
    assert not audio.gerar("c1", TEXTO, parar=lambda: (n.__setitem__(0, n[0] + 1) or n[0] > 2))
    feitos = len(falso.chamadas)
    e = audio.estado("c1")
    assert not e.completo and len(e.blocos) == feitos == 2  # parar() cedeu no 3º bloco
    assert audio.gerar("c1", TEXTO)
    assert len(falso.chamadas) == audio.estado("c1").total


def test_iniciar_em_segundo_plano(falso):
    audio.iniciar("c2", TEXTO)
    for _ in range(200):
        if audio.estado("c2").completo:
            break
        threading.Event().wait(0.02)
    e = audio.estado("c2")
    assert e.completo and not e.gerando and e.erro is None


def test_erro_do_motor_aparece_no_estado(falso, monkeypatch):
    def quebra():
        raise FileNotFoundError("sem modelo")
    monkeypatch.setattr(audio, "motores", quebra)
    audio.iniciar("c3", TEXTO)
    for _ in range(200):
        if audio.estado("c3").erro:
            break
        threading.Event().wait(0.02)
    assert audio.estado("c3").erro == "sem modelo"


def test_limpar_cache_remove_os_mais_antigos(falso, monkeypatch):
    import os
    for i, c in enumerate(["velho", "meio", "novo"]):
        audio.gerar(c, TEXTO)
        os.utime(audio.pasta(c), (1000 + i, 1000 + i))
    tamanho = sum(f.stat().st_size for f in audio.pasta("novo").iterdir())
    monkeypatch.setattr(config, "TTS_CACHE_MAX_GB", tamanho * 1.5 / 1024 ** 3)
    assert audio.limpar_cache(manter="velho") == 2
    assert audio.pasta("velho").exists() and not audio.pasta("meio").exists() and not audio.pasta("novo").exists()


def test_nao_gera_o_que_outro_processo_esta_gerando(falso):
    p = audio.pasta("c4")
    p.mkdir(parents=True)
    (p / "gerando").write_text("outro-processo")
    assert audio.ocupado("c4") and audio.estado("c4").gerando
    assert not audio.gerar("c4", TEXTO) and falso.chamadas == []


def test_trava_velha_e_assumida(falso):
    import os
    p = audio.pasta("c5")
    p.mkdir(parents=True)
    (p / "gerando").write_text("morto")
    os.utime(p / "gerando", (1000, 1000))
    assert not audio.ocupado("c5")
    assert audio.gerar("c5", TEXTO) and not (p / "gerando").exists()


def test_sintetizar_job_le_o_texto_do_banco(falso, tmp_path, monkeypatch):
    import sqlite3
    banco = tmp_path / "contos.db"
    conn = sqlite3.connect(banco)
    conn.execute("CREATE TABLE contos (id TEXT PRIMARY KEY, texto_completo TEXT)")
    conn.executemany("INSERT INTO contos VALUES (?, ?)", [("c6", TEXTO), ("c7", None)])
    conn.commit()
    conn.close()
    monkeypatch.setattr(config, "DB_PATH", str(banco))
    assert audio.sintetizar_job({"payload": {"conto_id": "c6"}}) == {"conto_id": "c6"}
    assert audio.estado("c6").completo
    assert audio.sintetizar_job({"payload": {"conto_id": "c7"}})["ignorado"] == "sem texto"


def test_enfileirar_audio(falso, tmp_path):
    import pipeline
    fila_db = str(tmp_path / "fila.db")
    assert pipeline.enfileirar_audio("c8", db_path=fila_db)
    assert not pipeline.enfileirar_audio("c8", db_path=fila_db)  # já pendente
    audio.gerar("c9", TEXTO)
    assert not pipeline.enfileirar_audio("c9", db_path=fila_db)  # já no cache
    fila = pipeline.fila_audio(fila_db)
    assert fila.contagens()["sintese"]["pendente"] == 1


def test_mapa_liga_blocos_aos_paragrafos(monkeypatch):
    monkeypatch.setattr(config, "TTS_PRIMEIRO_BLOCO", 40)
    monkeypatch.setattr(config, "TTS_BLOCO", 80)
    m = audio.mapa(TEXTO)
    assert len(m) == len(audio.blocos(TEXTO))
    paragrafos = [p for bloco in m for p, _ in bloco]
    assert paragrafos == sorted(paragrafos) and set(paragrafos) == set(range(10))
