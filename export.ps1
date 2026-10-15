# export.ps1 — export 1 versi (tag) jadi zip bersih buat klien (tanpa .git).
# Pakai:  .\export.ps1 v0.1
param([Parameter(Mandatory = $true)][string]$Tag)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

git rev-parse $Tag -- 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Error "tag '$Tag' tidak ada. Cek: git tag"; exit 1 }

$out = Join-Path $root "dist-$Tag.zip"
git archive --format=zip --prefix="ai-app-$Tag/" -o $out $Tag
Write-Host ">> OK: $out"
