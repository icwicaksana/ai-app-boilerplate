"""Agent demo HITL (v0.6) â€” TANPA LLM, untuk mencoba alur approval.

Alur: node `propose` memanggil `interrupt()` dengan payload gaya
`HITLRequest` (aksi refund Rp 750.000), lalu menunggu keputusan. Setelah
`Command(resume={"decisions":[...]})`, node `execute` mencatat hasilnya.

Kegunaan: klien/QA bisa mencoba approval end-to-end tanpa API key LLM, dan
E2E lintas proses bisa menguji resume tanpa ketergantungan proxy.
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agents.BaseChatAgent import BaseChatAgent
from app.agents.state import BaseState

DEMO_AMOUNT = 750_000  # di atas threshold policy (Rp 500.000)


class _DemoState(BaseState, total=False):
    approved: bool
    executed: bool
    outcome: str


def _payload() -> dict[str, Any]:
    return {
        "action_requests": [{
            "name": "process_refund",
            "args": {"order_id": "ORD-DEMO-1", "amount": DEMO_AMOUNT,
                     "reason": "demo HITL"},
            "description": f"Refund Rp {DEMO_AMOUNT:,} untuk ORD-DEMO-1",
        }],
        "review_configs": [{
            "action_name": "process_refund",
            "allowed_decisions": ["approve", "reject"],
        }],
    }


class HitlDemoAgent(BaseChatAgent):
    AGENT_NAME = "hitl_demo"
    SYSTEM_PROMPT = "Demo HITL tanpa LLM."
    TOOLS: list = []

    def build(self, session_id: str | None = None):
        def propose(state: _DemoState):
            decision = interrupt(_payload())
            approved = any(d.get("type") == "approve"
                           for d in (decision or {}).get("decisions", []))
            return {"approved": approved}

        def execute(state: _DemoState):
            approved = bool(state.get("approved"))
            return {
                "executed": approved,
                "outcome": ("Refund dieksekusi" if approved
                            else "Refund ditolak â€” tidak ada aksi"),
            }

        g = StateGraph(_DemoState)
        g.add_node("propose", propose)
        g.add_node("execute", execute)
        g.add_edge(START, "propose")
        g.add_edge("propose", "execute")
        g.add_edge("execute", END)
        return g.compile(checkpointer=self.checkpointer)

