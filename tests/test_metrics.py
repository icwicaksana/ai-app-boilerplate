"""V1-F/1: RED middleware + /metrics ter-guard + sinyal aplikasi."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.metrics import RAG_DEGRADED, REGISTRY
from tests._auth_helpers import app_with_settings


def _counter(name: str, labels: dict) -> float:
    for fam in REGISTRY.collect():
        for s in fam.samples:
            if s.name == name and all(
                    s.labels.get(k) == v for k, v in labels.items()):
                return float(s.value)
    return 0.0


def test_red_tercatat_per_route_template():
    client = TestClient(app_with_settings(), raise_server_exceptions=False)
    # Label = template path router-relatif (tanpa prefix /api/v1).
    labels = {"method": "GET", "route": "/livez", "status": "200"}
    before = _counter("http_requests_total", labels)
    assert client.get("/api/v1/livez").status_code == 200
    after = _counter("http_requests_total", labels)
    assert after == before + 1


def test_metrics_tanpa_token_loopback_boleh():
    client = TestClient(app_with_settings(), raise_server_exceptions=False)
    res = client.get("/metrics")
    assert res.status_code == 200
    assert "http_requests_total" in res.text
    assert res.headers["content-type"].startswith("text/plain")


def test_metrics_dengan_token_bearer():
    client = TestClient(app_with_settings(METRICS_TOKEN="s3cr3t"),
                        raise_server_exceptions=False)
    assert client.get("/metrics").status_code == 403
    assert client.get("/metrics",
                      headers={"Authorization": "Bearer salah"}
                      ).status_code == 403
    ok = client.get("/metrics",
                    headers={"Authorization": "Bearer s3cr3t"})
    assert ok.status_code == 200
    assert "http_requests_total" in ok.text


@pytest.mark.asyncio
async def test_sinyal_degraded_tool_rag():
    from app.agents.tools.RetrieverTool import make_search_knowledge_tool

    class _Ret:
        async def search(self, query, *, k=5, filters=None):
            return {"chunks": [{"chunk_id": "c1", "content": "x",
                                "source": "s"}],
                    "status": "ok", "degraded": ["rerank_unavailable"],
                    "grounded": False}

    before = _counter("rag_degraded_total",
                      {"reason": "rerank_unavailable"})
    tool = make_search_knowledge_tool(_Ret())
    out = await tool.ainvoke({"query": "x?"})
    assert "PERINGATAN KUALITAS" in out
    after = _counter("rag_degraded_total",
                     {"reason": "rerank_unavailable"})
    assert after == before + 1
    assert RAG_DEGRADED is not None
