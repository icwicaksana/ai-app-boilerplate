"""Metrik RED Prometheus (V1-F/1): request + sinyal aplikasi.

Registry SENDIRI (bukan REGISTRY global) agar test deterministik dan
import ganda aman. Proses tunggal (Dockerfile: 1 worker) — multi-replika
diagregasi via sum() di Prometheus/Grafana.
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

REGISTRY = CollectorRegistry()

HTTP_REQUESTS = Counter(
    "http_requests_total", "Total respons HTTP.",
    ["method", "route", "status"], registry=REGISTRY)
HTTP_DURATION = Histogram(
    "http_request_duration_seconds", "Durasi respons HTTP.",
    ["method", "route"], registry=REGISTRY,
    # SSE/stream panjang: bucket sampai 10 mnt.
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0,
             10.0, 30.0, 60.0, 120.0, 300.0, 600.0))
RAG_DEGRADED = Counter(
    "rag_degraded_total", "Retrieval terdegradasi.",
    ["reason"], registry=REGISTRY)
APPROVALS = Counter(
    "approvals_total", "Keputusan approval.",
    ["decision"], registry=REGISTRY)
LLM_ERRORS = Counter(
    "llm_errors_total", "Kegagalan panggil LLM.",
    ["agent", "code"], registry=REGISTRY)
WS_ACTIVE = Gauge(
    "ws_active_connections", "Koneksi WS aktif.", registry=REGISTRY)


def render_latest() -> tuple[bytes, str]:
    """Render exposition format. Return (body, content_type)."""
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST


class MetricsMiddleware:
    """Middleware RED: hitung tiap respons HTTP (ASGI murni).

    Label route = template path (kardinalitas rendah), BUKAN path mentah.
    /metrics dikecualikan (hindari noise scrape). ASGI murni (bukan
    BaseHTTPMiddleware) agar streaming SSE tak di-buffer.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        import time

        if scope.get("type") != "http" or scope.get("path") == "/metrics":
            await self.app(scope, receive, send)
            return
        start = time.perf_counter()
        status = "500"

        async def _send(message):
            nonlocal status
            if message.get("type") == "http.response.start":
                status = str(message.get("status", 500))
            await send(message)

        try:
            await self.app(scope, receive, _send)
        finally:
            try:
                route = scope.get("route")
                template = getattr(route, "path", None) or "unknown"
                labels = {"method": scope.get("method", "?"),
                          "route": template}
                HTTP_REQUESTS.labels(**labels, status=status).inc()
                HTTP_DURATION.labels(**labels).observe(
                    time.perf_counter() - start)
            except Exception:
                pass  # metrik tak boleh gagalkan request
