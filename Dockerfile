FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /code

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000
# --loop eksplisit: no-op di Linux, WAJIB di Windows (Proactor ditolak psycopg).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000",
     "--loop", "asyncio:SelectorEventLoop"]
