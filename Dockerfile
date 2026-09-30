FROM python:3.11-slim

# libgomp1: runtime dep do torch/sentence-transformers (CPU). curl: só pra
# instalar o uv (removido dos apt depois pra manter a imagem enxuta).
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

WORKDIR /app

# CrevoMQ: dependência por caminho (../CrevoMQ, relativo a /app). Vem do
# contexto extra `crevomq` do docker-compose.yml; só o que o build precisa.
COPY --from=crevomq pyproject.toml README.md /CrevoMQ/
COPY --from=crevomq crevomq /CrevoMQ/crevomq

# Camada separada só com as dependências — não invalida o cache do Docker
# a cada mudança de código, só quando pyproject.toml/uv.lock mudam.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

COPY . .
RUN uv sync --frozen

# Modelo do sentence-transformers baixa do HuggingFace Hub no primeiro uso —
# ver HF_HOME no compose (volume nomeado, compartilhado entre réplicas, pra
# não rebaixar ~470MB por container).
ENV PYTHONUNBUFFERED=1

CMD ["uv", "run", "python", "--version"]
