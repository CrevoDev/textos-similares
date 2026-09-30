#!/usr/bin/env bash
# Gera os vetores da busca v2 numa VM com T4 no Google Cloud (projeto
# pessoal), no lugar da Vast.ai. Mesmo pacote e mesmos passos do LEIAME.txt:
#   envia data/gpu_v2/entrada/ -> gerar_vetores.py -> traz data/gpu_v2/saida/
# A VM é APAGADA no fim (trap), mesmo com erro ou Ctrl+C. Depois rode:
#   DB_PATH=data/contos.db uv run python busca_v2/importar.py
#
#   bash scripts/gpu_gcloud.sh
#
# Usa a configuração `pessoal` do gcloud — nunca a da empresa.
set -euo pipefail

PROJETO=${PROJETO:-scrap-busca-v2-cleve}
VM=${VM:-busca-v2-gpu}
ZONAS=${ZONAS:-"us-central1-a us-central1-b us-central1-c us-central1-f"}
# SPOT sai por ~metade do preço mas pode ser preemptada no meio (já
# aconteceu); STANDARD (~US$ 0,55/h com T4) não cai.
PROVISIONAMENTO=${PROVISIONAMENTO:-STANDARD}
# T4: n1-standard-4 + nvidia-tesla-t4 · L4: g2-standard-4 + nvidia-l4 (~US$ 0,70/h, mais rápida)
MAQUINA=${MAQUINA:-n1-standard-4}
GPU=${GPU:-nvidia-tesla-t4}
IMAGEM_FAMILIA=pytorch-2-9-cu129-ubuntu-2204-nvidia-580
PASTA=data/gpu_v2
G=(gcloud --configuration=pessoal --project="$PROJETO")

[ -f "$PASTA/entrada/manifesto.json" ] || { echo "sem pacote em $PASTA/entrada — rode busca_v2/exportar.py"; exit 1; }

ZONA=""
apagar() {
  if [ -n "$ZONA" ]; then
    echo "[gpu] apagando a VM $VM ($ZONA)"
    "${G[@]}" compute instances delete "$VM" --zone="$ZONA" --quiet || \
      echo "[gpu] ATENÇÃO: falhou ao apagar — confira no console, ela continua cobrando"
  fi
}
trap apagar EXIT

extra=()
[ "$PROVISIONAMENTO" = SPOT ] && extra=(--instance-termination-action=DELETE)
for z in $ZONAS; do
  echo "[gpu] criando VM $PROVISIONAMENTO $MAQUINA + $GPU em $z"
  if "${G[@]}" compute instances create "$VM" --zone="$z" \
      --machine-type="$MAQUINA" --accelerator=type="$GPU",count=1 \
      --provisioning-model="$PROVISIONAMENTO" "${extra[@]}" \
      --maintenance-policy=TERMINATE \
      --image-family="$IMAGEM_FAMILIA" --image-project=deeplearning-platform-release \
      --boot-disk-size=100GB --boot-disk-type=pd-balanced \
      --metadata=install-nvidia-driver=True; then
    ZONA=$z; break
  fi
done
[ -n "$ZONA" ] || { echo "[gpu] sem $GPU ($PROVISIONAMENTO) em nenhuma zona agora — tente mais tarde"; exit 1; }

SSH=("${G[@]}" compute ssh "$VM" --zone="$ZONA" --quiet --command)
echo "[gpu] esperando o SSH"
for i in $(seq 1 30); do "${SSH[@]}" "true" 2>/dev/null && break; sleep 10; done
"${SSH[@]}" "nvidia-smi --query-gpu=name,memory.total --format=csv,noheader && mkdir -p ~/entrada ~/saida"

echo "[gpu] enviando o pacote"
"${G[@]}" compute scp --zone="$ZONA" --quiet "$PASTA"/entrada/* "$VM":entrada/

echo "[gpu] gerando vetores"
"${SSH[@]}" "pip install -q sentence-transformers && sudo pip uninstall -y -q torchaudio; nohup python3 entrada/gerar_vetores.py entrada saida > log.txt 2>&1 &"
until "${SSH[@]}" "test -f saida/CONCLUIDO" 2>/dev/null; do
  "${SSH[@]}" "tail -n 1 log.txt; pgrep -f gerar_vetores >/dev/null || { echo PAROU; tail -n 20 log.txt; exit 3; }" || \
    { echo "[gpu] o processo parou sem CONCLUIDO (VM spot preemptada?)"; exit 1; }
  sleep 60
done
"${SSH[@]}" "tail -n 2 log.txt"

echo "[gpu] trazendo o resultado"
mkdir -p "$PASTA/saida"
"${SSH[@]}" "tar czf saida.tgz saida"
"${G[@]}" compute scp --zone="$ZONA" --quiet "$VM":saida.tgz "$PASTA/"
tar xzf "$PASTA/saida.tgz" -C "$PASTA" && rm "$PASTA/saida.tgz"
ls "$PASTA/saida" | wc -l
echo "[gpu] pronto — agora: DB_PATH=data/contos.db uv run python busca_v2/importar.py"
