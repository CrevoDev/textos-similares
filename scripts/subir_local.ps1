# Sobe o projeto no Windows, sem Docker/WSL: Qdrant nativo
# (tools/qdrant/qdrant.exe), painel do CrevoMQ e o app de busca. Ctrl+C
# encerra tudo o que este script subiu.
#   pwsh scripts/subir_local.ps1                # app de busca (porta 8503)
#   pwsh scripts/subir_local.ps1 -Painel        # painel do projeto (porta 8501)
#   pwsh scripts/subir_local.ps1 -Workers       # + supervisor dos workers (filas)
# Painel das filas (CrevoMQ): http://127.0.0.1:8420
param([switch]$Painel, [switch]$Workers)
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')

$qdrantExe = 'tools\qdrant\qdrant.exe'
if (-not (Test-Path $qdrantExe)) { throw "Qdrant não encontrado em $qdrantExe" }
if (-not (Test-Path 'qdrant_storage\collections')) {
    throw 'qdrant_storage/ vazio — extraia backup_volumes/scrap-project_qdrant_storage.tgz nele'
}

# Env herdado pelo qdrant.exe, pelo servidor/supervisor do CrevoMQ e pelo streamlit
$env:QDRANT__STORAGE__STORAGE_PATH = './qdrant_storage'
$env:QDRANT__STORAGE__SNAPSHOTS_PATH = './qdrant_storage/snapshots'
$env:QDRANT__SERVICE__HOST = '127.0.0.1'
$env:QDRANT__TELEMETRY_DISABLED = 'true'
$env:HF_HOME = (Resolve-Path 'hf_cache').Path

function Responde([string]$url) {
    try { Invoke-RestMethod $url -TimeoutSec 2 | Out-Null; return $true } catch { return $false }
}

function Esperar([string]$url, [string]$nome, [string]$log) {
    for ($i = 0; $i -lt 60; $i++) {
        if (Responde $url) { return }
        Start-Sleep -Milliseconds 500
    }
    throw "$nome não respondeu em 30s — veja $log"
}

$subidos = @()
try {
    New-Item -ItemType Directory -Force data | Out-Null

    if (Responde 'http://127.0.0.1:6333/readyz') {
        Write-Host 'Qdrant já está rodando na porta 6333 — reaproveitando.'
    } else {
        $subidos += Start-Process $qdrantExe -PassThru -WindowStyle Hidden `
            -RedirectStandardOutput 'data\qdrant.log' -RedirectStandardError 'data\qdrant.err.log'
        Write-Host "Qdrant iniciado (PID $($subidos[-1].Id)), aguardando..."
        Esperar 'http://127.0.0.1:6333/readyz' 'Qdrant' 'data/qdrant.err.log'
    }

    # Schema + registro das filas antes de qualquer worker/painel abrir o banco.
    uv run python scripts/init_schema.py
    if ($LASTEXITCODE) { throw 'scripts/init_schema.py falhou' }

    if (Responde 'http://127.0.0.1:8420/') {
        Write-Host 'Painel do CrevoMQ já está rodando na porta 8420 — reaproveitando.'
    } else {
        $subidos += Start-Process uv -ArgumentList 'run', 'crevomq-servidor', 'data/fila.db', '--porta', '8420' `
            -PassThru -WindowStyle Hidden -RedirectStandardOutput 'data\crevomq.log' -RedirectStandardError 'data\crevomq.err.log'
        Esperar 'http://127.0.0.1:8420/' 'Painel do CrevoMQ' 'data/crevomq.err.log'
        Write-Host 'Painel das filas: http://127.0.0.1:8420'
    }

    if ($Workers) {
        $subidos += Start-Process uv -ArgumentList 'run', 'crevomq-supervisor', 'supervisor.toml' `
            -PassThru -WindowStyle Hidden -RedirectStandardOutput 'data\supervisor.log' -RedirectStandardError 'data\supervisor.err.log'
        Write-Host "Supervisor dos workers iniciado (PID $($subidos[-1].Id)) — log em data/supervisor.log"
    }

    if ($Painel) { uv run streamlit run painel_filas.py --server.headless true --server.port 8501 }
    else { uv run streamlit run app.py --server.headless true --server.port 8503 }
} finally {
    foreach ($p in $subidos) {
        if (-not $p.HasExited) {
            # /T derruba a árvore: `uv run` abre o python como filho, e o
            # supervisor tem os workers como filhos.
            taskkill /PID $p.Id /T /F | Out-Null
        }
    }
    if ($subidos) { Write-Host 'Processos iniciados por este script encerrados.' }
}
