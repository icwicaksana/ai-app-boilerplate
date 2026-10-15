"""Slot HITL policy untuk v1. v0: HITL_ENABLED=false, file ini tidak dipakai.

Rencana v1 (lihat Docs/05-advanced-patterns/01-human-in-the-loop-hitl.md):
    HITL_POLICY = {
        "process_refund": {"enabled": True, "threshold": 500_000},
    }
Lalu MiddlewareBuilderUtils.build_middleware() memasang:
    HumanInTheLoopMiddleware(interrupt_on={...})
"""

# v0 sengaja kosong — jangan hapus file ini, v1 akan mengisinya.
HITL_POLICY: dict = {}

# Daftar tool yang WAJIB approval saat HITL dinyalakan nanti.
INTERRUPT_ON: set[str] = set()
