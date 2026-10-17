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

if (-not $SkipTests) {
    Write-Host ">> pytest (unit; integrasi butuh docker: pytest tests/ -m integration) ..."
    & $venvPy -m pytest tests/ -q -p no:cacheprovider -m "not integration"
}

Write-Host ">> OK. Jalan: .venv/Scripts/python.exe -m uvicorn app.main:app --reload --loop asyncio:SelectorEventLoop"
