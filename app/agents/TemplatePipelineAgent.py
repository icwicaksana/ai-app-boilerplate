"""TEMPLATE pipeline kaku (StateGraph). PEDOMAN — copy dulu.

Pakai ini (bukan TemplateChatAgent) ketika:
- Urutan langkah TETAP (LLM tidak boleh ubah urutan).
- Ada hitungan presisi yang wajib Python, bukan narasi bebas.
- 1 transaksi = 1 thread_id, 1 checkpoint trail.
- Dijalankan via JobController.run() (sync). Pipeline tidak streaming.

Cara membuat pipeline baru:
  1. Copy ExamplePipelineAgent.py (hidup, ada isi) ATAU file ini (kerangka kosong).
  2. Ganti state (tambah field), isi 3 node: Python -> Python -> LLM.
  3. Aturan node: deterministik (validasi/hitung) = Python murni TANPA LLM;
     hanya node terakhir yang boleh panggil LLM (narasi).
"""

from __future__ import annotations

from abc import abstractmethod

from langgraph.graph import END, START, StateGraph

from app.agents.BasePipelineAgent import BasePipelineAgent
from app.agents.state import BaseState


class TemplateState(BaseState, total=False):
    """GANTI/TAMBAH field sesuai domain. messages wajib (jejak runner)."""

    raw_input: dict
    stage1: dict
    stage2: dict
    final: str


class TemplatePipelineAgent(BasePipelineAgent):
    AGENT_NAME = "template_pipeline"  # GANTI: mis. "refund_pipeline"

    # --- build: DENAH KAKU — INI satu-satunya hal yang kamu ubah ---
    def build(self):
        g = StateGraph(TemplateState)
        g.add_node("step_1", self._node_step_1)  # GANTI: Python (validasi/bersihkan)
        g.add_node("step_2", self._node_step_2)  # GANTI: Python (hitung presisi)
        g.add_node("step_3", self._node_step_3)  # GANTI: LLM (narasikan)
        g.add_edge(START, "step_1")
        g.add_edge("step_1", "step_2")
        g.add_edge("step_2", "step_3")
        g.add_edge("step_3", END)
        # CONTOH cabang (hapus komentar jika butuh):
        # g.add_conditional_edges("step_2", self._route_big_total,
        #                         {"need_approval": "step_3", "small": "step_3"})
        return g.compile(checkpointer=self.checkpointer)

    @abstractmethod
    def _node_step_1(self, state: TemplateState) -> dict:
        # GANTI dengan validasi domainmu. Contoh: lihat ExamplePipelineAgent._node_validate.
        raise NotImplementedError("Isi node Python pertamamu (lihat ExamplePipelineAgent).")

    @abstractmethod
    def _node_step_2(self, state: TemplateState) -> dict:
        # GANTI dengan hitungan domainmu. Contoh: lihat ExamplePipelineAgent._node_calculate.
        raise NotImplementedError("Isi node Python keduamu (lihat ExamplePipelineAgent).")

    @abstractmethod
    def _node_step_3(self, state: TemplateState) -> dict:
        # GANTI dengan narasi LLM domainmu. Contoh: lihat ExamplePipelineAgent._node_narrate.
        raise NotImplementedError("Isi node LLM-mu (lihat ExamplePipelineAgent).")
