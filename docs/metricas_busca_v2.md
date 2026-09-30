# Busca v2: métricas da indexação da base completa (29/09/2026)

Indexação semântica de um acervo de textos em trechos do texto completo, com
o modelo `intfloat/multilingual-e5-small` (384 dimensões), em uma coleção do
Qdrant. Rodada completa após o descarte da base: exportação, geração dos
vetores na GPU (Google Cloud) e importação local.

## Volume

| Métrica | Valor |
|---|---|
| Textos no pacote | 136.233 (140.845 candidatos; 4.471 excluídos por tema, 141 sem texto) |
| Texto processado | ~1,24 bilhão de caracteres (média de 9.112 por texto) |
| Trechos gerados | **987.018** (média de 7,2 por texto; 400 tokens com 50 de sobreposição) |
| Pacote de entrada (texto, gzip) | 467 MB, 137 partes de 1.000 textos |
| Vetores gerados (float16, .npz) | 778 MB |
| Pontos na coleção do Qdrant | 987.018 |

## Infraestrutura: comparação

Todas as opções rodam o mesmo código de divisão e vetorização, então os
vetores saem idênticos.

| Onde | Vazão | Tempo (base inteira) | Custo |
|---|---|---|---|
| CPU local (4 núcleos físicos), 1 worker | ~75 textos/2 min | ~3 dias (estimado) | energia |
| CPU local, 2 workers (fila CrevoMQ) | ~4,7 trechos/s · ~5.900 caracteres/s | ~2,4 dias (estimado) | energia |
| **GPU NVIDIA L4 (GCP `g2-standard-4`, sob demanda)** | **~400 trechos/s** | **41 min** | **~US$ 0,70** (estimado; conferir na fatura) |

- **Ganho de velocidade:** a L4 foi ~85× mais rápida que a CPU local com 2
  workers, e cerca de 100× mais rápida que o ciclo total de ~57 h, com custo
  abaixo de US$ 1.
- **2º worker na CPU:** +28% de vazão (75 para 96 textos a cada 2 min), com a
  CPU em 100% e ~1,4 GB de RAM por worker. Um 3º worker não compensa, porque a
  CPU já satura e a memória acabaria.
- **Uso da GPU:** a L4 ficou em ~78% de utilização e usou só 2,3 GB de VRAM,
  com lote de 256. Ainda há espaço para aumentar o lote.

## Linha do tempo da rodada na GPU

| Etapa | Duração |
|---|---|
| Criação da VM, SSH e envio do pacote (467 MB) | ~5 min |
| Geração dos vetores (137 partes) | 41 min |
| Download do resultado e exclusão da VM | ~10 min |
| Importação no Qdrant local (upsert + payload do SQLite) | ~55 min |

A importação local (~0,05 parte/s) passou a ser o gargalo do fluxo.

## Fila inteligente (CrevoMQ)

A mesma indexação também foi medida como fila de jobs por texto
(`busca_v2_contos`), com ordenação "mais rápido primeiro" aprendida online:

- O modelo de duração da fila aprendeu sozinho que **o tamanho do texto
  explica ~90% da duração** de cada job, com 67 amostras.
- Na amostra de 500 textos: média de 1,4 s por texto (1 worker), 0 falhas e
  6,8 trechos por texto.
- Ressalva: com a ordenação "mais rápido primeiro", a vazão por texto sai
  otimista, porque os curtos são processados antes. Para projetar a base
  inteira, a métrica confiável é caracteres por segundo.

## Obstáculos e soluções

| Problema | Solução |
|---|---|
| Alugar GPU (Vast.ai) exigia US$ 10 de depósito antecipado | Google Cloud pós-pago em projeto pessoal; cotas de GPU pedidas pela API Cloud Quotas e aprovadas em minutos |
| VM **spot** preemptada ~1 min após iniciar a geração | VM sob demanda (cerca de 2× o preço, sem interrupção); o script aceita `PROVISIONAMENTO=SPOT` |
| **Sem T4 disponível** (`ZONE_RESOURCE_POOL_EXHAUSTED`) em 12 zonas e 4 regiões dos EUA | Troca para a L4 (`g2-standard-4`), disponível em `us-central1-b` |
| `torchaudio` da imagem Deep Learning VM incompatível com o `torch` quebrava o import do `transformers` | Remoção do `torchaudio` (a busca não usa áudio), já incluída no script |
| `pscp` do gcloud no Windows não expande `~` no caminho remoto | Caminhos remotos relativos ao diretório do usuário |
| Disco de boot menor que a imagem (100 GB) | `--boot-disk-size=100GB` |

Proteção de custo: o script apaga a VM em qualquer saída (sucesso, erro ou
Ctrl+C), via `trap`.

## Resultado

Busca semântica nos 987 mil trechos funcionando. Exemplo: a consulta "casal
viaja pra praia" trouxe os 5 primeiros resultados com similaridade entre 0,883
e 0,887, todos com trechos sobre viagem de casal à praia.

## Como reproduzir

    DB_PATH=data/contos.db uv run python busca_v2/exportar.py --modo completo
    MAQUINA=g2-standard-4 GPU=nvidia-l4 ZONAS="us-central1-b us-central1-a us-central1-c" bash scripts/gpu_gcloud.sh
    DB_PATH=data/contos.db uv run python busca_v2/importar.py
