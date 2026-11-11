"""Worker proses terpisah (v0.7, ADR-21): `python -m app.workers.ingest`.

Bukan lifespan API, bukan TurnManager: queue durable Postgres + lease.
Lihat `app/workers/ingest.py`.
"""
