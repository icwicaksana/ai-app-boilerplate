"""Test arsitektur RAG (D1): `app/rag` murni — tanpa import infra/eksternal.

Alasan: port + pipeline + chunker + sitasi + grounding harus bisa di-unit-test
tanpa Docker/model/GPU. Pelanggaran = FAIL (kontrak beku).
"""

import ast
from pathlib import Path

RAG_DIR = Path(__file__).resolve().parent.parent / "app" / "rag"

# Modul dilarang: infra sendiri + lib eksternal ber-I/O/berat.
FORBIDDEN_TOPS = {
    "app.infra", "app.workers", "app.api", "app.controllers",
    "httpx", "requests", "urllib3",
    "qdrant_client", "psycopg", "psycopg_pool",
    "fastapi", "starlette", "pydantic_settings",
    "torch", "transformers", "sentence_transformers", "optimum", "onnxruntime",
    "langchain", "langgraph",
}
# Bahkan urllib stdlib dilarang: app/rag tak boleh HTTP sama sekali
# (itu tugas adapter di app/infra/rag).
FORBIDDEN_TOPS |= {"urllib", "http", "socket", "asyncio"}


def _imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                found.add(a.name.split(".")[0] if "." not in a.name or not a.name.startswith("app")
                          else ".".join(a.name.split(".")[:2]))
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").split(".")
            if mod[0] == "app" and len(mod) > 1:
                found.add("app." + mod[1])
            elif mod[0]:
                found.add(mod[0])
    return found


def test_rag_package_is_pure():
    assert RAG_DIR.is_dir(), "app/rag belum ada"
    violations: dict[str, set[str]] = {}
    for path in sorted(RAG_DIR.rglob("*.py")):
        bad = _imports_of(path) & FORBIDDEN_TOPS
        if bad:
            violations[path.name] = bad
    assert not violations, f"app/rag mengimpor modul terlarang: {violations}"


def test_ports_expose_frozen_contracts():
    """Semua protocol + DTO beku (D1/D2) tersedia dan stabil namanya."""
    import app.rag.ports as p

    for name in ("EmbeddingsPort", "RerankerPort", "VectorStorePort",
                 "BoundRetriever", "SourceConnector", "OCREngine",
                 "DocumentParser", "GraphStore", "EntityExtractor",
                 "GraphRanker"):
        assert hasattr(p, name), f"protocol {name} hilang dari ports.py"
    for name in ("CitedChunk", "ChunkIn", "RerankedDoc", "RetrievalResult",
                 "IndexInfo", "OCRResult", "ParsedDocument", "TableMeta",
                 "SourceRecord", "KgEntityIn", "KgEdgeIn", "KgHit",
                 "GraphInfo", "Extraction"):
        assert hasattr(p, name), f"DTO {name} hilang dari ports.py"
    # BoundRetriever.search TANPA argumen tenant (D2: factory-bound).
    import inspect

    sig = inspect.signature(p.BoundRetriever.search)
    assert "tenant" not in sig.parameters
    assert "tenant_id" not in sig.parameters
    # VectorStorePort.search_* juga tanpa tenant.
    for meth in ("search_dense", "search_lexical"):
        ps = inspect.signature(getattr(p.VectorStorePort, meth)).parameters
        assert "tenant" not in ps and "tenant_id" not in ps
    # v0.8 D2: GraphStore juga factory-bound (tanpa tenant per-call).
    for meth in ("upsert_subgraph", "expand", "delete_subgraph"):
        ps = inspect.signature(getattr(p.GraphStore, meth)).parameters
        assert "tenant" not in ps and "tenant_id" not in ps, meth


def test_no_llm_in_rag_package():
    """Garis merah §4: 0 LLM call/query — app/rag tak boleh impor klien LLM."""
    import re

    hits = []
    for path in sorted(RAG_DIR.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if re.search(r"\blitellm\b|\bLiteLLM\b|\bChatLiteLLM\b", text):
            hits.append(path.name)
    assert not hits, f"klien LLM bocor ke app/rag: {hits}"
