"""ExamplePipelineAgent: contoh HIDUP static pipeline (validate -> calculate -> narrate).

Rel pipeline: route /jobs/example_pipeline -> JobController.run() -> run di sini.
Pedoman = TemplatePipelineAgent.py. Untuk pipeline baru: copy file INI (bukan template)
lalu ganti node + state, atau copy template bila mau mulai dari kerangka kosong.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from app.agents.BasePipelineAgent import BasePipelineAgent
from app.agents.state import BaseState
from app.core.logging import get_logger

log = get_logger(__name__)

NARRATE_PROMPT = (
    "Tulis ringkasan Bahasa Indonesia maksimal 5 baris dari JSON yang diberikan: "
    "1. Total pemasukan, pengeluaran, dan saldo. "
    "2. 1 anomali terbesar (kategori paling boros). "
    "3. 1 saran hemat konkret. "
    "Jika JSON kosong/nihil, jawab terus terang: data belum tersedia. "
    "Jangan karang angka di luar JSON."
)

INSIGHT_PROMPT = (
    "Dari JSON totals berikut, tulis (Bahasa Indonesia): "
    "1 kalimat anomali terbesar + 1 saran hemat konkret. "
    "JANGAN tulis angka — angka diisi sistem, bukan kamu."
)

INCOME = "income"
EXPENSE = "expense"


class NarrativeInsight(BaseModel):
    """Wawasan kualitatif dari LLM. ANGKA TIDAK BOLEH dari sini (dari Python)."""

    anomaly: str = Field(default="", description="1 kalimat anomali terbesar")
    suggestion: str = Field(default="", description="1 saran hemat konkret")


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
    structured: dict  # totals (Python) + insight (LLM tervalidasi) → output.data


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
        insight = self._extract_insight(totals)
        if insight is not None:
            # Jalur utama (v0.2): angka dari Python + wawasan tervalidasi.
            # reply dirakit deterministik — LLM tak bisa karang angka.
            text = self._render_summary(totals, insight)
            data = {**totals, "anomaly": insight.anomaly, "suggestion": insight.suggestion}
            wrapped: Any = AIMessage(content=text)
            return {"final": text, "structured": data,
                    "messages": [HumanMessage(content=json.dumps(totals, ensure_ascii=False)), wrapped]}
        # Fallback legacy: narasi bebas 1 panggilan (data=None di output).
        resp = self.llm.invoke(
            f"{NARRATE_PROMPT}\n\nData (JANGAN karang angka di luar ini): "
            f"{json.dumps(totals, ensure_ascii=False)}"
        )
        text = self._coerce_narrate_text(resp)
        wrapped = self._wrap_narrate_response(resp)
        return {"final": text,
                "messages": [HumanMessage(content=json.dumps(totals, ensure_ascii=False)), wrapped]}

    def _extract_insight(self, totals: dict) -> NarrativeInsight | None:
        """Ekstraksi terstruktur + self-correction tepat 1x. Gagal = None (lembut)."""
        prompt = f"{INSIGHT_PROMPT}\n\nData: {json.dumps(totals, ensure_ascii=False)}"
        try:
            extractor = self.llm.with_structured_output(NarrativeInsight)
        except Exception:
            log.exception("structured extractor tak tersedia, fallback narasi bebas")
            return None
        try:
            result = extractor.invoke(prompt)
            return result if isinstance(result, NarrativeInsight) else None
        except Exception as first_err:
            log.warning("structured gagal (%s), self-correction 1x", type(first_err).__name__)
        try:
            retry = extractor.invoke(
                f"{prompt}\n\nPERBAIKAN: respons sebelumnya tidak valid. "
                "Kembalikan HANYA JSON valid sesuai skema (anomaly, suggestion).")
            return retry if isinstance(retry, NarrativeInsight) else None
        except Exception:
            log.exception("structured gagal 2x, fallback narasi bebas")
            return None

    @staticmethod
    def _render_summary(totals: dict, insight: NarrativeInsight) -> str:
        income = totals.get("total_income", 0)
        expense = totals.get("total_expense", 0)
        balance = totals.get("balance", 0)
        lines = [f"Total pemasukan: {income}; pengeluaran: {expense}; saldo: {balance}."]
        if (insight.anomaly or "").strip():
            lines.append(f"Anomali: {insight.anomaly.strip()}")
        if (insight.suggestion or "").strip():
            lines.append(f"Saran: {insight.suggestion.strip()}")
        if len(lines) == 1:
            lines.append("Data belum tersedia.")
        return "\n".join(lines)

    @staticmethod
    def _coerce_narrate_text(resp: Any) -> str:
        from app.agents.utils.MessageUtils import coerce_llm_text

        return coerce_llm_text(getattr(resp, "content", resp))
