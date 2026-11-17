"""Kontrak delegasi subagent: Brief masuk, Report keluar. MURNI — tanpa I/O.

ATURAN BESI: subagent yang gagal TIDAK BOLEH terlihat seperti sukses.
`Report` memaksanya secara struktural: status non-ok wajib membawa daftar
kegagalan, dan agen utama wajib menyampaikan apa yang tidak didapat.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ReportStatus = Literal["ok", "partial", "failed", "timeout"]

MAX_TASK_CHARS = 4_000
MAX_CONSTRAINTS = 8
MAX_CONSTRAINT_CHARS = 500


@dataclass(frozen=True)
class Brief:
    """Tugas untuk subagent. Satu-satunya konteks yang ia terima.

    Subagent TIDAK menerima riwayat percakapan — kalau ia menerima seluruh
    history, penghematan token terbalik dan ia bisa "mengingat" hal yang tak
    relevan. `budget_tokens`/`timeout_s` di sini hanya boleh MENURUNKAN
    batas kode (pola rate-limit); penegakannya di DelegationTool.
    """

    task: str
    constraints: tuple[str, ...] = ()
    budget_tokens: int | None = None
    timeout_s: int | None = None

    def __post_init__(self) -> None:
        task = (self.task or "").strip()
        if not task:
            raise ValueError("Brief.task wajib diisi.")
        if len(task) > MAX_TASK_CHARS:
            raise ValueError(
                f"Brief.task {len(task)} karakter melebihi {MAX_TASK_CHARS} — "
                f"ringkas tugasnya, bukan tempel seluruh konteks.")
        object.__setattr__(self, "task", task)
        if len(self.constraints) > MAX_CONSTRAINTS:
            raise ValueError(f"maksimum {MAX_CONSTRAINTS} constraints.")
        for c in self.constraints:
            if len(c) > MAX_CONSTRAINT_CHARS:
                raise ValueError("satu constraint melebihi "
                                 f"{MAX_CONSTRAINT_CHARS} karakter.")
        for key in ("budget_tokens", "timeout_s"):
            value = getattr(self, key)
            if value is not None and value <= 0:
                raise ValueError(f"Brief.{key} harus positif.")


@dataclass(frozen=True)
class ToolFailure:
    """Satu kegagalan tool di dalam run subagent."""

    tool: str
    error_class: str  # transient | permanent | timeout | breaker_open | budget
    attempts: int = 1
    detail: str = ""


@dataclass(frozen=True)
class Report:
    """Hasil delegasi. Selalu ada, bahkan saat gagal total."""

    status: ReportStatus
    answer: str = ""
    citations: tuple[str, ...] = ()
    failures: tuple[ToolFailure, ...] = ()
    tokens_used: int = 0
    ms: int = 0

    def __post_init__(self) -> None:
        if self.status not in ("ok", "partial", "failed", "timeout"):
            raise ValueError(f"Report.status tak dikenal: {self.status!r}")
        if self.status == "ok" and self.failures:
            raise ValueError("status 'ok' tidak boleh membawa failures — "
                             "pakai 'partial' bila ada yang gagal.")
        if self.status in ("partial", "failed") and not self.failures:
            raise ValueError(f"status {self.status!r} wajib menyebut apa "
                             f"yang gagal — jangan gagal diam-diam.")
        if self.tokens_used < 0 or self.ms < 0:
            raise ValueError("tokens_used/ms tidak boleh negatif.")

    def to_text(self) -> str:
        """Render untuk ToolMessage ke agen utama."""
        lines = [f"[Subagent: {self.status}]"]
        if self.answer:
            lines.append(self.answer)
        for f in self.failures:
            lines.append(f"- GAGAL {f.tool} ({f.error_class}, "
                         f"{f.attempts}x coba{f': ' + f.detail if f.detail else ''})")
        if self.status != "ok":
            lines.append("Sampaikan ke user apa yang TIDAK bisa didapat — "
                         "jangan jawab seolah data lengkap.")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "answer": self.answer,
            "citations": list(self.citations),
            "failures": [
                {"tool": f.tool, "class": f.error_class,
                 "attempts": f.attempts, "detail": f.detail}
                for f in self.failures
            ],
            "tokens_used": self.tokens_used,
            "ms": self.ms,
        }
