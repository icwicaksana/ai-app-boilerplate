FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# v0.7: Tesseract (+ data Indonesia & Inggris) untuk OCR worker.
# Pin via Debian snapshot? Tidak — apt mengikuti base image (tercatat di build
# log); yang dipin exact = versi Python paket (requirements.lock, ADR-02).
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr tesseract-ocr-ind tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /code

# Repro reproducible: install dari LOCK (bukan requirements unpinned) —
# semangat ADR-02 (pin exact, bump deliberate) berlaku juga untuk image.
#
# TORCH CPU DULU (temuan F7): docling menarik `torch` dan wheel PyPI untuk
# Linux = build CUDA + seluruh `nvidia-*` (~6 GB) yang TIDAK terpakai di
# server CPU-only. Index CPU resmi menyediakan `2.15.0+cpu` (~200 MB) dan
# memenuhi pin `torch==2.15.0` di lock, sehingga sisa instalasi tak menarik
# lib nvidia lagi. Image turun dari ~11 GB ke ~5 GB.
ARG TORCH_VERSION=2.15.0
RUN pip install --no-cache-dir \
    --index-url https://download.pytorch.org/whl/cpu \
    "torch==${TORCH_VERSION}"

COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock

# Kode + MIGRASI. `migrations/` WAJIB: `rag_migrate` membaca
# `<repo>/migrations/*.sql` relatif ke berkas modul — tanpa ini container
# gagal start ("007_rag.sql tidak ditemukan", temuan F7).
COPY app ./app
COPY migrations ./migrations

# Direktori runtime (di-mount volume di compose; dibuat lebih dulu agar
# user non-root bisa menulis walau volume kosong).
ENV RAG_STORAGE_DIR=/data/uploads \
    RAG_HF_CACHE=/cache/hf \
    HOST=0.0.0.0 \
    PORT=8000 \
    RELOAD=false
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data/uploads /cache/hf \
    && chown -R appuser:appuser /code /data /cache
USER appuser

EXPOSE 8000
# Entrypoint kanonis (HOST/PORT/RELOAD via ENV → Settings): JANGAN pakai
# `uvicorn ... --loop ...` — flag CLI --loop choices terbatas
# ([auto|asyncio|uvloop|zuvloop]) dan menolak nilai selector (temuan F-01).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/api/v1/livez')"
# API. Worker ingest = proses TERPISAH dari image yang sama:
#   docker run --rm --env-file .env ai-app-boilerplate:v0.7 python -m app.workers.ingest
CMD ["python", "-m", "app.main"]
