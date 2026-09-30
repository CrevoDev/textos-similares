# textos-similares

Pipeline pessoal para coletar textos de um site público, guardar uma cópia local
e encontrar textos parecidos com os que você gosta, usando busca semântica.

Projeto de estudo para **uso pessoal**. O conteúdo coletado não é redistribuído, e o
site de origem só é configurado no `.env`, que não vai para o repositório.

## Como funciona

```
descoberta (sitemap) ─► extração ─► embeddings ─► indexação (Qdrant)
                                 │
                                 └─► busca v2: trechos do texto completo + reranker + resumos por LLM
```

- **Filas:** [CrevoMQ](../CrevoMQ), com filas persistidas em SQLite, um supervisor que escala os workers
  conforme o backlog e um painel web.
- **Armazenamento:** SQLite (metadados, texto e busca textual FTS5) e Qdrant (vetores).
- **Embeddings:** `sentence-transformers` na CPU (MiniLM na v1, `multilingual-e5-small`
  na v2 com trechos de 400 tokens) e reranking com cross-encoder.
- **Resumos:** qualquer servidor compatível com a API da OpenAI (llama.cpp, Ollama, vLLM).
  Há também o modo `mock`, que não usa rede.
- **Leitura em voz alta:** TTS local com Kokoro (ONNX), gerado em blocos e guardado em cache.
- **Apps (Streamlit):** um app de busca (catálogo, busca semântica/híbrida, "mais
  parecidos", recomendação por perfil, favoritos, séries) e um painel das filas.

## Estrutura

| Pasta/arquivo | Conteúdo |
|---|---|
| `scraper/` | descoberta via sitemap, extração e parse do HTML |
| `embeddings/` | geração de vetores e envio ao Qdrant |
| `busca/` | catálogo, filtros, busca semântica, recomendação, leitura, áudio |
| `busca_v2/` | indexação por trechos, reranker, resumos, exportação/importação para GPU |
| `pipeline.py` | definição das filas e dos estágios |
| `descarte.py` | remoção de textos por regras de tag (definidas no `.env`) |
| `series.py` | agrupamento de textos em séries |
| `app.py`, `painel_filas.py`, `ui.py` | interfaces Streamlit |
| `mocks/` | descoberta e extração falsas, sem acessar a rede |
| `tests/` | testes (pytest) |

## Configuração

```bash
cp .env.example .env
```

Preencha pelo menos:

| Variável | Para quê |
|---|---|
| `SITE_BASE_URL` | URL base do site de origem (vazio = só o modo mock funciona) |
| `DESCARTE_TAGS` / `DESCARTE_PROTEGIDAS` | regex de descarte e de proteção por tag |
| `TEMAS_EXCLUIDOS` | trechos de tag excluídos da busca v2 e dos resumos (obrigatório) |

As demais variáveis têm valores padrão, comentados no `.env.example`.

## Como rodar

Requer [uv](https://docs.astral.sh/uv/) e o repositório do CrevoMQ clonado em `../CrevoMQ`.

**Docker (recomendado):**

```bash
mkdir -p data
docker compose up -d --build
```

| Serviço | URL |
|---|---|
| App de busca | http://localhost:8503 |
| Painel (Streamlit) | http://localhost:8501 |
| Painel das filas (CrevoMQ) | http://localhost:8420 |
| Qdrant | http://localhost:6333/dashboard |

**Local (Windows, sem Docker):** `scripts/subir_local.ps1`.

**Modo mock** (sem rede): defina `MOCK_MODE=true` e coloque páginas HTML de exemplo
em `fixtures/`.

```bash
uv run python scripts/rodar_pipeline_mock.py
```

## Testes

```bash
uv run pytest
```

## Boa convivência com o site

A coleta respeita um intervalo mínimo entre requisições (`REQUEST_INTERVAL_SECONDS`)
e usa um `USER_AGENT` identificável. Não reduza esses limites sem motivo.
