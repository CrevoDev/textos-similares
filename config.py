import os

from dotenv import load_dotenv

load_dotenv()

MOCK_MODE = os.getenv("MOCK_MODE", "false").lower() in ("1", "true", "yes")

DB_PATH = os.getenv("DB_PATH", "contos.db")

QDRANT_URL = os.getenv("QDRANT_URL")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")
QDRANT_LOCAL_PATH = os.getenv("QDRANT_LOCAL_PATH", "./qdrant_local")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "contos")
# Séries (series.py): um ponto por série, vetor = média das partes.
QDRANT_COLLECTION_SERIES = os.getenv("QDRANT_COLLECTION_SERIES", "series")

EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "paraphrase-multilingual-MiniLM-L12-v2")

# Site de origem: só no .env (não versionado). Vazio = só modo mock funciona.
SITE_BASE_URL = os.getenv("SITE_BASE_URL", "").rstrip("/")
SITEMAP_INDEX_URL = os.getenv("SITEMAP_INDEX_URL") or f"{SITE_BASE_URL}/static/sitemap.index.xml.gz"
REQUEST_INTERVAL_SECONDS = float(os.getenv("REQUEST_INTERVAL_SECONDS", "1.5"))
USER_AGENT = os.getenv(
    "USER_AGENT",
    "textos-similares/0.1 (uso pessoal; +https://github.com/)",
)

MAX_TENTATIVAS = int(os.getenv("MAX_TENTATIVAS", "5"))
LOTE_PADRAO = int(os.getenv("LOTE_PADRAO", "50"))

# Estado das filas (CrevoMQ — ver pipeline.py) num SQLite próprio: o claim de
# jobs trava o banco para escrita a cada pegar/avancar, e o contos.db (GBs de
# texto) fica livre disso. Painel/API: `crevomq-servidor FILA_DB_PATH`.
FILA_DB_PATH = os.getenv("FILA_DB_PATH", "data/fila.db")
CREVOMQ_URL = os.getenv("CREVOMQ_URL", "http://127.0.0.1:8420")

# Descarte de textos (descarte.py): regex aplicada a cada tag normalizada
# (minúsculas, sem acento). Uma tag que case com DESCARTE_TAGS descarta o
# texto, a menos que outra tag case com DESCARTE_PROTEGIDAS. Vazio = padrão.
DESCARTE_TAGS = os.getenv("DESCARTE_TAGS", "")
DESCARTE_PROTEGIDAS = os.getenv("DESCARTE_PROTEGIDAS", "")

# --- Busca v2 (trechos do texto completo) e resumos — ver busca_v2/ ---------
# `amostra` (padrão) trabalha só com os primeiros BUSCA_V2_LIMITE textos já
# extraídos, numa coleção/estado separados. `completo` processa a base
# inteira e só roda depois que a extração terminar (busca_v2/fonte.py).
BUSCA_V2_MODO = os.getenv("BUSCA_V2_MODO", "amostra")
BUSCA_V2_LIMITE = int(os.getenv("BUSCA_V2_LIMITE", "2000"))
# Estado da v2 (progresso da indexação, resumos) num SQLite próprio: o
# contos.db só é lido, nunca alterado, enquanto os workers estão escrevendo nele.
BUSCA_V2_DB_PATH = os.getenv("BUSCA_V2_DB_PATH", "data/busca_v2.db")
# Pacote da indexação na GPU alugada (exportar.py / importar.py) — ao lado do
# busca_v2.db, pra funcionar igual no host (data/) e no Docker (/data).
BUSCA_V2_GPU_PASTA = os.getenv("BUSCA_V2_GPU_PASTA", os.path.join(os.path.dirname(BUSCA_V2_DB_PATH), "gpu_v2"))
QDRANT_V2_LOCAL_PATH = os.getenv("QDRANT_V2_LOCAL_PATH", "./qdrant_v2_local")
QDRANT_COLLECTION_TRECHOS = os.getenv("QDRANT_COLLECTION_TRECHOS", "contos_trechos")

EMBEDDING_MODEL_V2 = os.getenv("EMBEDDING_MODEL_V2", "intfloat/multilingual-e5-small")
RERANKER_MODEL = os.getenv("RERANKER_MODEL", "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1")
TRECHO_TOKENS = int(os.getenv("TRECHO_TOKENS", "400"))
TRECHO_SOBREPOSICAO = int(os.getenv("TRECHO_SOBREPOSICAO", "50"))
# 0 = todos os trechos; >0 limita por texto (distribuídos ao longo do texto)
# pra caber em CPU quando o tempo de indexação for um problema.
TRECHOS_MAX_POR_TEXTO = int(os.getenv("TRECHOS_MAX_POR_TEXTO", "0"))

# Textos com qualquer tag contendo um desses trechos (sem diferenciar
# maiúsculas) ficam fora da indexação v2 e dos resumos. Separados por vírgula.
# Obrigatório (fica no .env): sem ele a importação falha em vez de indexar tudo.
_temas_excluidos = os.getenv("TEMAS_EXCLUIDOS")
if _temas_excluidos is None:
    raise RuntimeError("Defina TEMAS_EXCLUIDOS no .env (ver .env.example)")
TEMAS_EXCLUIDOS = [p.strip().lower() for p in _temas_excluidos.split(",") if p.strip()]

# Resumos: qualquer servidor compatível com a API da OpenAI (llama.cpp
# `llama-server`, Ollama, vLLM numa GPU alugada...). `mock` não chama rede.
LLM_PROVEDOR = os.getenv("LLM_PROVEDOR", "mock")  # mock | openai
LLM_URL = os.getenv("LLM_URL", "http://localhost:8080/v1")
LLM_MODELO = os.getenv("LLM_MODELO", "qwen3-4b")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MAX_CHARS = int(os.getenv("LLM_MAX_CHARS", "12000"))
LLM_TIMEOUT_S = float(os.getenv("LLM_TIMEOUT_S", "300"))

# --- Leitura em voz alta (Fase 11) — ver busca/audio.py ---------------------
# Local, custo zero. `kokoro` (voz pf_dora, ~1,1× tempo real na CPU) ou
# `piper` (plano B, ~10×; voz = faber | cadu | jeff).
TTS_MOTOR = os.getenv("TTS_MOTOR", "kokoro")
TTS_VOZ = os.getenv("TTS_VOZ", "pf_dora")
TTS_VELOCIDADE = float(os.getenv("TTS_VELOCIDADE", "1.0"))
TTS_MODELO_DIR = os.getenv("TTS_MODELO_DIR", "data/tts")
# Kokoro: kokoro-v1.0.fp16.onnx (padrão desde 29/09/2026, qualidade igual ao
# fp32 de ouvido e ~20% mais rápido com 2 sessões) ou kokoro-v1.0.onnx (fp32,
# o original). O int8 ficou 5× MAIS LENTO nesta máquina — não usar.
TTS_MODELO = os.getenv("TTS_MODELO", "kokoro-v1.0.fp16.onnx")
# Blocos gerados ao mesmo tempo (uma sessão do Kokoro cada, CPU dividida entre
# elas): 2 rende ~+35% de vazão sobre 1; cada sessão a mais custa RAM.
TTS_PARALELO = int(os.getenv("TTS_PARALELO", "2"))
TTS_CACHE_DIR = os.getenv("TTS_CACHE_DIR", "data/audio")
TTS_CACHE_MAX_GB = float(os.getenv("TTS_CACHE_MAX_GB", "5"))
# Caracteres por bloco: o 1º curto pro áudio começar em segundos, os seguintes
# crescendo TTS_CRESCIMENTO vezes até TTS_BLOCO.
TTS_PRIMEIRO_BLOCO = int(os.getenv("TTS_PRIMEIRO_BLOCO", "220"))
# Blocos grandes rendem mais (medido: 300 chars 0,98×, 900 1,74×, 1500 1,94×
# o tempo real) mas demoram a ficar prontos; por isso crescem a partir do 1º.
TTS_BLOCO = int(os.getenv("TTS_BLOCO", "1500"))
TTS_CRESCIMENTO = float(os.getenv("TTS_CRESCIMENTO", "1.6"))
