import pytest

import series


@pytest.mark.parametrize("titulo, base, temporada, numeros, final", [
    ("Mundo Encantado - Parte 2", "Mundo Encantado", None, (2,), False),
    ("Mundo Encantado - Parte 6 - FINAL", "Mundo Encantado", None, (6,), True),
    ("Mundo Encantado", "Mundo Encantado", None, (), False),
    ("Ela virou a minha vida 11", "Ela virou a minha vida", None, (11,), False),
    ("A sobrinha pt4 - Sou sua escravinha", "A sobrinha", None, (4,), False),
    ("Aventuras nas baladas Parte I", "Aventuras nas baladas", None, (1,), False),
    ("O marido da minha madrinha VII", "O marido da minha madrinha", None, (7,), False),
    ("EU, MINHA IRMÃ E NOSSA MÃE - XXVI", "EU, MINHA IRMÃ E NOSSA MÃE", None, (26,), False),
    ("Minha mãe e sua amiga III (final)", "Minha mãe e sua amiga", None, (3,), True),
    ("Depois do bate-papo - Final", "Depois do bate-papo", None, (), True),
    ("MEU ENTEADO E SUA NAMORADA -FIM", "MEU ENTEADO E SUA NAMORADA", None, (), True),
    ("Um dia de Workshop - parte 1/4", "Um dia de Workshop", None, (1,), False),
    ("Da internet para o mundo real 2 - A segunda vez", "Da internet para o mundo real", None, (2,), False),
    ("Garoto do EB – T2 – EP 06 | A Aliança", "Garoto do EB", 2, (6,), False),
    ("O que é o Amor! (3a Temporada - CAPÍTULO 04) - Um Amigo", "O que é o Amor!", 3, (4,), False),
    ("ESCOLHAS - TEMPORADA 2(1)", "ESCOLHAS", 2, (1,), False),
    ("Mãe asiática Jenny e seu filho Chris Série 2: Parte 5", "Mãe asiática Jenny e seu filho Chris", 2, (5,), False),
    ("Jornada - Capítulo 15 - Alvorecer - Parte 27", "Jornada", None, (15, 27), False),
    ("Comprador de Virgindades – 16 – Bel, 35 anos, geniosa e virgem 2/3", "Comprador de Virgindades", None, (16, 2),
     False),
    ("Reencontrei Minha Filha aos 3 Anos - 2", "Reencontrei Minha Filha aos 3 Anos", None, (2,), False),
    # números por extenso, final no meio do título, prólogo, 2°TEMPORADA
    ("O Futuro Presente em Nosso Passado. Sétimo Capítulo: Curiosidade ou Fascínio?",
     "O Futuro Presente em Nosso Passado", None, (7,), False),
    ("Formações e Transformações - Décimo Primeiro e Último Capítulo", "Formações e Transformações", None, (11,), True),
    ("A Pré-História das Três Marias - quarto e último capítulo", "A Pré-História das Três Marias", None, (4,), True),
    ("Deixa o coração mandar - segunda parte", "Deixa o coração mandar", None, (2,), False),
    ("A mente poluída de Jennifer - parte um", "A mente poluída de Jennifer", None, (1,), False),
    ("Uma geração de mulheres alfa. Parte Final: O grande encontro!", "Uma geração de mulheres alfa", None, (), True),
    ("O Único que Ficou de Fora - Último Capítulo: Tudo não Passou de um Sonho", "O Único que Ficou de Fora", None, (),
     True),
    ("O Único que Ficou de Fora (2°TEMPORADA) - Terceiro Capítulo: Onze Nomes", "O Único que Ficou de Fora", 2, (3,),
     False),
    ("Minha saga - 2ª parte", "Minha saga", None, (2,), False),
    ("Memorial de Dona Maroca - Prólogo", "Memorial de Dona Maroca", None, (0,), False),
    ("Minha saga - Epílogo", "Minha saga", None, (), True),
    ("Os Amantes da mamãe. 2°ato", "Os Amantes da mamãe", None, (2,), False),
])
def test_analisar(titulo, base, temporada, numeros, final):
    t = series.analisar(titulo)
    assert (t.base, t.temporada, t.numeros, t.final) == (base, temporada, numeros, final)


@pytest.mark.parametrize("titulo", [
    "Comi 2 mulheres na festa",       # número no meio da frase não é parte
    "Carnaval 2019",                  # ano não é parte
    "MINHA VIDA CIVIL",               # palavra de letras romanas não é número
    "COMI A MINHA TIA VIVI",
    "Parte da vida",
    "Memorial de Dona Maroca - Anos 50: Juventude",   # década, não parte
    "Minha vida aos 18",                              # idade, não parte
    "O último capítulo da minha vida",                # "último capítulo" sem base antes
    "Segundo encontro com a vizinha",                 # ordinal sem palavra de parte
    "Pego no ato pela sogra",                         # "ato" sem número
])
def test_sem_marcador(titulo):
    assert not series.analisar(titulo).marcado


def _c(i, titulo, data, serie=None, autor="A"):
    return {"id": str(i), "titulo": titulo, "autor": autor, "serie": serie, "data_pub": data}


def _ids(s):
    return [c["id"] for c in s.partes]


def test_agrupa_e_ordena_pelo_numero():
    (s,) = series.agrupar([
        _c(3, "Mundo Encantado - Parte 3", "2022-01-03"),
        _c(1, "Mundo Encantado", "2022-01-01"),
        _c(9, "Mundo Encantado - Final", "2022-01-02"),   # publicada fora de ordem
        _c(2, "Mundo Encantado - Parte 2", "2022-01-05"),
        _c(7, "Outra história", "2022-01-04"),
    ])
    assert (s.nome, s.origem) == ("Mundo Encantado", "titulo")
    assert _ids(s) == ["1", "2", "3", "9"]


def test_titulos_iguais_sem_numero_nao_sao_serie():
    assert series.agrupar([_c(1, "A vizinha", "2020"), _c(2, "A vizinha", "2021")]) == []


def test_parte_unica_nao_e_serie():
    assert series.agrupar([_c(1, "A vizinha 2", "2020"), _c(2, "Outra", "2021")]) == []


def test_site_absorve_partes_detectadas_com_o_mesmo_nome():
    (s,) = series.agrupar([
        _c(1, "PRECISA-SE MOTORISTA", "2025-05-07", serie="PRECISA-SE MOTORISTA"),
        _c(2, "PRECISA-SE MOTORISTA 2 - Um novo papel", "2025-05-08"),
        _c(3, "PRECISA-SE MOTORISTA 3 - Ciúme", "2025-05-08", serie="PRECISA-SE MOTORISTA"),
    ])
    assert s.origem == "site" and _ids(s) == ["1", "2", "3"]


def test_antologia_do_site_segue_a_data():
    (s,) = series.agrupar([
        _c(1, "Mamãe objeto de desejo pt1", "2024-06-25", serie="Histórias sobre mães"),
        _c(2, "Perdendo a virgindade", "2024-04-23", serie="Histórias sobre mães"),
        _c(3, "Segredos na cama pt1", "2025-02-21", serie="Histórias sobre mães"),
        _c(4, "Mamãe objeto de desejo pt2", "2024-06-26", serie="Histórias sobre mães"),
    ])
    assert _ids(s) == ["2", "1", "4", "3"]


def test_numero_igual_em_todas_e_parte_do_nome():
    (s,) = series.agrupar([
        _c(1, "Tudo começou com R$ 50 - cachoeira", "2020-01-02"),
        _c(2, "Tudo começou com R$ 50 - ao som", "2020-01-01"),
    ])
    assert s.nome == "Tudo começou com R$ 50"
    assert _ids(s) == ["2", "1"]


def test_dois_niveis_e_final_desempata():
    (s,) = series.agrupar([
        _c(1, "Diário - Capítulo 2 (Parte Final)", "2020-01-03"),
        _c(2, "Diário - Capítulo 2", "2020-01-02"),
        _c(3, "Diário - Capítulo 1", "2020-01-01"),
        _c(4, "Diário - Capítulo 1 - Parte 2", "2020-01-04"),
    ])
    assert _ids(s) == ["3", "4", "2", "1"]


def test_grafias_do_autor_na_mesma_serie():
    """Partes publicadas como '2H2O' (letra O) e '2H20' (zero): uma série só."""
    (s,) = series.agrupar([
        _c(1, "Pai, o homem que desejava demais", "2026-07-28", serie="Pai, o homem que desejava demais",
           autor="2H2O"),
        _c(2, "Pai, o homem que desejava de mais - 2", "2026-08-04", serie="Pai, o homem que desejava demais",
           autor="2H20"),
        _c(3, "Pai, o homem que desejava demais - 3", "2026-08-28", serie="Pai, o homem que desejava demais",
           autor="2H2O"),
        _c(4, "Pai, o homem que desejava demais - 4", "2026-09-05", serie="Pai, o homem que desejava demais",
           autor="2H20"),
    ])
    assert _ids(s) == ["1", "2", "3", "4"]
    assert s.autor == "2H2O"  # empate: a grafia da parte 1


@pytest.mark.parametrize("a, b", [("Biel", "BIEL"), ("Ana Beatriz S", "Ana Beatriz S."), ("2H2O", "2H20"),
                                  ("Loirinha dos olhos azuis", "Loirinhadosolhosazuis"), ("CáH", "Cah..")])
def test_chave_autor(a, b):
    assert series.chave_autor(a) == series.chave_autor(b)


def test_prefixo_comum_sem_numero():
    """Memorial: só o prólogo tem marcador; o resto é "prefixo - subtítulo"."""
    (s,) = series.agrupar([
        _c(1, "Memorial de Dona Maroca, a filha da luxúria - Prólogo", "2017-02-08T00"),
        _c(2, "Memorial de Dona Maroca, a filha da luxúria - Anos 50: Juventude e Casamento", "2017-02-08T18"),
        _c(3, "Memorial de Dona Maroca, a filha da luxúria - Anos 60: Liberdade", "2017-02-09T00"),
        _c(4, "Memorial de Dona Maroca, a filha da luxúria - Ultimos Tempos: A despedida", "2017-02-12"),
        _c(5, "Seu Floriano, o varão", "2016-12-15"),
    ])
    assert s.nome == "Memorial de Dona Maroca, a filha da luxúria"
    assert _ids(s) == ["1", "2", "3", "4"]


def test_prefixo_curto_ou_repostagem_nao_e_serie():
    assert series.agrupar([_c(1, "Relato real - A vizinha", "2020"), _c(2, "Relato real - O porteiro", "2021")]) == []
    assert series.agrupar([_c(1, "Minha primeira vez - no carro", "2020"),
                           _c(2, "Minha primeira vez - no carro", "2021")]) == []


def test_temporadas_na_mesma_serie():
    (s,) = series.agrupar([
        _c(1, "O Único que Ficou de Fora (2°TEMPORADA) - Primeiro Capítulo: O Passeio", "2026-07-24"),
        _c(2, "O Único que Ficou de Fora - Primeiro Capítulo: A Revelação", "2024-11-04"),
        _c(3, "O Único que Ficou de Fora - Último Capítulo: Tudo não Passou de um Sonho", "2024-11-10"),
        _c(4, "O Único que Ficou de Fora - Segundo Capítulo: Surubinha", "2024-11-05"),
    ])
    assert _ids(s) == ["2", "4", "3", "1"]


def test_id_estavel():
    a = series.agrupar([_c(1, "X y z 1", "2020"), _c(2, "X y z 2", "2021")])[0]
    b = series.agrupar([_c(5, "x Y Z - Parte 3", "2020"), _c(6, "X y z 4", "2021")])[0]
    assert a.id == b.id
