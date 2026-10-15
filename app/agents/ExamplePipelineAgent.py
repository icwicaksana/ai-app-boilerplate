"""ExamplePipelineAgent: contoh HIDUP static pipeline (validate -> calculate -> narrate).

Rel pipeline: route /jobs/example_pipeline -> JobController.run() -> run di sini.
Pedoman = TemplatePipelineAgent.py. Untuk pipeline baru: copy file INI (bukan template)
lalu ganti node + state, atau copy template bila mau mulai dari kerangka kosong.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, StateGraph

from app.agents.BasePipelineAgent import BasePipelineAgent
from app.agents.state import BaseState

NARRATE_PROMPT = (
    "Tulis ringkasan Bahasa Indonesia maksimal 5 baris dari JSON yang diberikan: "
    "1. Total pemasukan, pengeluaran, dan saldo. "
    "2. 1 anomali terbesar (kategori paling boros). "
    "3. 1 saran hemat konkret. "
    "Jika JSON kosong/nihil, jawab terus terang: data belum tersedia. "
    "Jangan karang angka di luar JSON."
)

INCOME = "income"
EXPENSE = "expense"


MAX_AMOUNT_ABS = Decimal("1000000000000000")  # 1e15: di atas ini tolak (cegah inf/presisi hilang)


def _finite_float(value: Decimal) -> float:
    try:
        if value.is_nan() or not value.is_finite():
            return 0.0
    except (InvalidOperation, AttributeError):
        return 0.0
    try:
        result = float(round(value, 2))
    except (InvalidOperation, ValueError, OverflowError):
        return 0.0
    if result != result or result in (float("inf"), float("-inf")):  # NaN/inf lolos
        return 0.0
    return result


def _finite_or_none(amt: Decimal) -> Decimal | None:
    try:
        if amt.is_nan() or not amt.is_finite():
            return None
    except (InvalidOperation, AttributeError):
        return None
    if abs(amt) > MAX_AMOUNT_ABS:
        return None
    return amt


def parse_amount(value: Any) -> Decimal | None:
    """Parse amount aman → Decimal. Return None jika invalid (bukan raise).

    Terima int/float/Decimal/str angka. Tolak list/dict/None/""/bool/format lokal
    seperti "12.000,50" (minta format mesin, bukan tampilan). Tolak juga
    NaN/±Infinity dan nilai >1e15 (cegah JSON invalid + presisi float hilang).
    """
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            return _finite_or_none(Decimal(str(value)))
        except (InvalidOperation, ValueError):
            return None
    if isinstance(value, str):
        s = value.strip().replace("_", "")
        if not s:
            return None
        try:
            return _finite_or_none(Decimal(s))
        except InvalidOperation:
            return None
    return None


class ExampleState(BaseState, total=False):
    raw_input: dict
    validated: dict
    totals: dict
    final: str


class ExamplePipelineAgent(BasePipelineAgent):
    AGENT_NAME = "example_pipeline"

    # --- build: denah kaku validate -> calculate -> narrate ---
    def build(self):
        g = StateGraph(ExampleState)
        g.add_node("validate", self._node_validate)    # Python: bersihkan
        g.add_node("calculate", self._node_calculate)  # Python: hitung presisi
        g.add_node("narrate", self._node_narrate)      # LLM: narasikan
        g.add_edge(START, "validate")
        g.add_edge("validate", "calculate")
        g.add_edge("calculate", "narrate")
        g.add_edge("narrate", END)
        return g.compile(checkpointer=self.checkpointer)

    def _node_validate(self, state: ExampleState) -> dict:
        raw = state.get("raw_input", {}) or {}
        if not isinstance(raw, dict):
            return {"validated": {"count": 0, "dropped": 1, "items": []}}
        items = raw.get("items", []) or []
        if not isinstance(items, list):
            return {"validated": {"count": 0, "dropped": 1, "items": []}}
        clean: list[dict] = []
        dropped = 0
        for item in items:
            if not isinstance(item, dict):
                dropped += 1
                continue
            amount = parse_amount(item.get("amount", 0))
            if amount is None or amount <= 0:
                dropped += 1
                continue
            normalized = dict(item)
            normalized["amount"] = float(amount)
            clean.append(normalized)
        return {"validated": {"count": len(clean), "dropped": dropped, "items": clean}}

    def _node_calculate(self, state: ExampleState) -> dict:
        validated = state.get("validated", {}) or {}
        items = validated.get("items", []) if isinstance(validated, dict) else []
        if not isinstance(items, list):
            items = []
        income = Decimal("0")
        expense = Decimal("0")
        ignored_unknown = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            amount = parse_amount(item.get("amount", 0))
            if amount is None:
                continue
            if item.get("tipe") == INCOME:
                income += amount
            elif item.get("tipe") == EXPENSE:
                expense += amount
            else:
                ignored_unknown += 1
        income_f = _finite_float(income)
        expense_f = _finite_float(expense)
        balance_f = _finite_float(income - expense)
        count = len([i for i in items if isinstance(i, dict)])
        return {"totals": {"total_income": income_f, "total_expense": expense_f,
                           "balance": balance_f, "count": count,
                           "ignored_unknown": ignored_unknown}}

    def _node_narrate(self, state: ExampleState) -> dict:
        totals = state.get("totals", {}) or {}
        resp = self.llm.invoke(
            f"{NARRATE_PROMPT}\n\nData (JANGAN karang angka di luar ini): "
            f"{json.dumps(totals, ensure_ascii=False)}"
        )
        text = self._coerce_narrate_text(resp)
        wrapped = self._wrap_narrate_response(resp)
        return {"final": text,
                "messages": [HumanMessage(content=json.dumps(totals, ensure_ascii=False)), wrapped]}

    @staticmethod
    def _coerce_narrate_text(resp: Any) -> str:
        from app.agents.utils.MessageUtils import coerce_llm_text

        return coerce_llm_text(getattr(resp, "content", resp))
