# setup.ps1 — clone → jalan dalam 1 perintah.
# Pakai:  .\setup.ps1            (install + test)
#         .\setup.ps1 -SkipTests (install saja)
# Python: baca .python-version (3.12). Pakai `uv` bila ada (cepat),
# fallback ke `python -m venv/pip` biasa bila tidak.
# Catatan: bila execution policy memblokir, jalankan:
#   powershell -ExecutionPolicy Bypass -File .\setup.ps1
param([switch]$SkipTests)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$pyVer = "3.12"
if (Test-Path ".python-version") { $pyVer = (Get-Content ".python-version" -Raw).Trim() }
$useUv = [bool](Get-Command uv -ErrorAction SilentlyContinue)
if ($useUv) { Write-Host ">> uv terdeteksi, Python $pyVer" } else { Write-Host ">> uv tidak ada, pakai python biasa" }

if (-not (Test-Path ".venv/Scripts/python.exe")) {
    Write-Host ">> buat .venv (Python $pyVer) ..."
    if ($useUv) { uv venv --python $pyVer .venv }
    else {
        $py = Get-Command python -ErrorAction SilentlyContinue
        if (-not $py) { Write-Error "python tidak ditemukan di PATH."; exit 1 }
        python -m venv .venv
    }
}
$venvPy = Join-Path $root ".venv/Scripts/python.exe"

Write-Host ">> install -r requirements.txt ..."
if ($useUv) { uv pip install --python $venvPy -r requirements.txt }
else {
    & $venvPy -m pip install --upgrade pip
    & $venvPy -m pip install -r requirements.txt
}

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host ">> .env dibuat dari .env.example (ISI API KEY DULU)"
}

# v0.4: isi JWT_SECRET otomatis bila masih kosong (cegah start dengan auth mati).
$envText = Get-Content ".env" -Raw
if ($envText -match "(?m)^JWT_SECRET=\s*$") {
    $secret = ([guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N"))
    $envText = $envText -replace "(?m)^JWT_SECRET=\s*$", "JWT_SECRET=$secret"
    Set-Content ".env" -Value $envText -NoNewline -Encoding utf8
    Write-Host ">> JWT_SECRET digenerate otomatis di .env"
}

# v0.7: isi SOURCE_CONN_KEY otomatis bila masih kosong (Fernet 44ch untuk
# secret konektor; tanpa ini konektor DB menolak start saat dikonfigurasi).
$envText = Get-Content ".env" -Raw
if ($envText -match "(?m)^SOURCE_CONN_KEY=\s*$") {
    $connKey = (& $venvPy -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())").Trim()
    $envText = $envText -replace "(?m)^SOURCE_CONN_KEY=\s*$", "SOURCE_CONN_KEY=$connKey"
    Set-Content ".env" -Value $envText -NoNewline -Encoding utf8
    Write-Host ">> SOURCE_CONN_KEY digenerate otomatis di .env"
}

if (-not $SkipTests) {
    Write-Host ">> install -r requirements.dev.lock (pytest dkk) ..."
    if ($useUv) { uv pip install --python $venvPy -r requirements.dev.lock }
    else { & $venvPy -m pip install -r requirements.dev.lock }
    Write-Host ">> pytest (unit; integrasi butuh docker: pytest tests/ -m integration) ..."
    & $venvPy -m pytest tests/ -q -p no:cacheprovider -m "not integration"
}

Write-Host ">> OK. Langkah berikut:"
Write-Host "   1) cd ..\Infra; docker compose up -d app-db cache   (wajib v0.3+)"
Write-Host "      RAG (v0.7): docker compose -f docker-compose.embed.yml up -d  (Infinity BGE-M3+reranker)"
Write-Host "                  opsional: docker compose --profile qdrant up -d qdrant"
Write-Host "   2) isi DATABASE_URL + CACHE_URL + LITELLM_* di .env"
Write-Host "   3) buat akun: .venv\Scripts\python.exe -m app.scripts.create_user --workspace toko-a --username budi --password '...'"
Write-Host "   4) jalan API: .venv\Scripts\python.exe -m app.main"
Write-Host "      jalan worker RAG (proses TERPISAH): .venv\Scripts\python.exe -m app.workers.ingest"
