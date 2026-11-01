"""Tool registry: tambah tool baru = tambah 1 baris di sini, tanpa ubah agent/controller."""

from app.agents.tools.ExampleRefundTool import process_refund
from app.agents.tools.ExampleSearchTool import check_stock

# Kunci: nama yang dilihat LLM saat memilih tool.
TOOLS = [check_stock, process_refund]

TOOL_MAP = {t.name: t for t in TOOLS}
