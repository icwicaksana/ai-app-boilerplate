FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /code

# Repro reproducible: install dari LOCK (bukan requirements unpinned) —
# semangat ADR-02 (pin exact, bump deliberate) berlaku juga untuk image.
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock

COPY app ./app

EXPOSE 8000
# Entrypoint kanonis (HOST/PORT/RELOAD via ENV → Settings): JANGAN pakai
# `uvicorn ... --loop ...` — flag CLI --loop choices terbatas
# ([auto|asyncio|uvloop|zuvloop]) dan menolak nilai selector (temuan F-01).
ENV HOST=0.0.0.0 PORT=8000 RELOAD=false
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/api/v1/livez')"
CMD ["python", "-m", "app.main"]
