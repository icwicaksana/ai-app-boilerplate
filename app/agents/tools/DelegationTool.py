"""Delegasi subagent: SATU-SATUNYA jalur sah membuat tool delegasi (F4).

Kenapa wajib lewat sini (bukan `create_agent` langsung di dalam tool):
  1. Tag `subagent` — satu-satunya mekanisme filter stream yang stabil (K9).
  2. `disable_streaming=True` — token child tak pernah dihasilkan (lapis 1).
  3. Middleware rem + error terpasang — child tanpa rem bisa loop.
  4. Larangan struktural: tool approval-gated (HITL_POLICY) dan tool delegasi
     lain DITOLAK saat build — subagent tak bisa lewati approval atau rekursi.
  5. Budget hanya boleh TURUN dari batas kode (pola rate-limit).

Roster statis (K1): spec didefinisikan di `subagents/registry.py`; LLM hanya
memilih nama + menulis Brief. Nama tool `subagent__<nama>` (garis bawah
ganda — titik dua ditolak API OpenAI; diverifikasi probe F4).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable

from langchain_core.messages import HumanMessage
from langchain_core.tools import StructuredTool, tool

from app.agents.subagents.registry import SUBAGENTS, get_spec
from app.agents.subagents.resilience import (
    BreakerOpen,
    CircuitBreaker,
    call_with_retry,
    error_class,
)
from app.core.hitl import HITL_POLICY
from app.core.logging import get_logger
from app.schemas.subagent import Brief, Report, ToolFailure

log = get_logger(__name__)

SUBAGENT_TOOL_PREFIX = "subagent__"
SUBAGENT_TAG = "subagent"

__all__ = [
    "SUBAGENT_TOOL_PREFIX",
    "SUBAGENT_TAG",
    "SubagentConfigError",
    "make_subagent_tool",
    "subagent_tool_name",
]


class SubagentConfigError(ValueError):
    """Kesalahan konfigurasi subagent (programmer, bukan input user).

    Dilempar saat BUILD (bukan runtime) supaya salah wiring ketahuan di
    test/deploy, bukan saat user sedang menunggu jawaban.
    """


def subagent_tool_name(spec_name: str) -> str:
    return f"{SUBAGENT_TOOL_PREFIX}{spec_name}"


#: Breaker per (spec, tool), hidup sepanjang proses. State tidak boleh hidup
#: di objek tool (build() jalan per request) kalau tidak breaker reset tiap
#: request dan tak pernah terbuka.
_BREAKERS: dict[tuple[str, str], CircuitBreaker] = {}


def _breaker_for(spec_name: str, tool_name: str) -> CircuitBreaker:
    key = (spec_name, tool_name)
    breaker = _BREAKERS.get(key)
    if breaker is None:
        breaker = CircuitBreaker()
        _BREAKERS[key] = breaker
    return breaker


def _wrap_tool(spec_name: str, orig, *, failures: list,
               max_attempts: int = 3, base_delay_s: float = 0.5,
               attempt_timeout_s: float = 10.0,
               sleep: Callable[[float], Awaitable[None]] | None = None):
    """Bungkus tool child dengan retry transien + pencatatan kegagalan.

    Nama dan skema dipertahankan (middleware limit cocok by-name).
    Kegagalan dicatat ke `failures` milik run ini, lalu di-raise lagi agar
    loop agen melihat ToolMessage error (loop = retry lapis kedua).
    """
    name = str(getattr(orig, "name", "?"))
    breaker = _breaker_for(spec_name, name)

    async def run(**kwargs: Any) -> Any:
        async def call() -> Any:
            return await orig.ainvoke(kwargs)

        try:
            result, _ = await call_with_retry(
                call, breaker=breaker, max_attempts=max_attempts,
                base_delay_s=base_delay_s,
                attempt_timeout_s=attempt_timeout_s, sleep=sleep)
            return result
        except BreakerOpen as exc:
            failures.append(ToolFailure(
                tool=name, error_class="breaker_open", attempts=0,
                detail=str(exc)))
            raise
        except Exception as exc:  # noqa: BLE001 - klasifikasi + catat
            cls = error_class(exc)
            failures.append(ToolFailure(
                tool=name, error_class=cls,
                attempts=1 if cls == "permanent" else max_attempts,
                detail=f"{type(exc).__name__}"))
            raise

    return StructuredTool(
        name=name,
        description=str(getattr(orig, "description", "") or ""),
        args_schema=getattr(orig, "args_schema", None),
        coroutine=run,
    )


def _child_tool_error(exc: Exception, request: Any) -> str:
    name = getattr(request, "tool_name", None) or getattr(request, "name", "?")
    return (f"Tool '{name}' gagal ({type(exc).__name__}). Coba sekali lagi "
            f"dengan argumen berbeda, atau lanjutkan tanpa data itu dan "
            f"laporkan apa yang tidak didapat.")


_CHILD_FOOTER = (
    "\n\nAturan pelaporan (WAJIB): jawab RINGKAS dalam 1-3 kalimat; sebutkan "
    "angka/fakta persis dari hasil tool (jangan karang); bila sebagian data "
    "tak didapat, katakan terus terang bagian mana.")

_SUBAGENT_DESCRIPTION = (
    "Delegasikan TUGAS ini ke pekerja {name}: {description} "
    "Pakai saat: {when_to_use} "
    "Input: task (tugas konkret, maks 4000 karakter) + constraints opsional "
    "(syarat terpisah koma, maks 8). Output: 1 laporan ringkas (status "
    "ok/partial/failed/timeout + jawaban + apa yang gagal). "
    "JANGAN delegasikan bila 1-2 tool call langsung cukup."
)


def make_subagent_tool(
    spec_name: str,
    *,
    tools: list,
    settings,
    session_id: str | None = None,
    model=None,
    _sleep: Callable[[float], Awaitable[None]] | None = None,
):
    """Bangun tool delegasi `subagent__<nama>` untuk satu spec roster.

    `tools` = daftar tool yang BOLEH dipakai child (dari build induk);
    spec memilih subset by-name. `model` hanya untuk test (model palsu);
    produksi selalu `get_llm(..., streaming=False)`.
    """
    spec = get_spec(spec_name)
    if spec is None:
        known = ", ".join(sorted(SUBAGENTS)) or "(roster kosong)"
        raise SubagentConfigError(
            f"subagent '{spec_name}' tidak ada di roster. "
            f"Yang terdaftar: {known}.")

    if int(getattr(settings, "SUBAGENT_MAX_DEPTH", 1)) < 1:
        raise SubagentConfigError("SUBAGENT_MAX_DEPTH < 1: subagent dimatikan.")

    by_name = {str(getattr(t, "name", "?")): t for t in tools}
    missing = [n for n in spec.tools if n not in by_name]
    if missing:
        raise SubagentConfigError(
            f"subagent '{spec_name}' butuh tool {missing} yang tak tersedia "
            f"di build induk.")

    gated = [n for n in spec.tools
             if (pol := HITL_POLICY.get(n)) is not None and pol.enabled]
    if gated:
        raise SubagentConfigError(
            f"subagent '{spec_name}' DILARANG memegang tool approval {gated}: "
            f"interrupt di dalam run bersarang tak sampai ke checkpoint induk, "
            f"sehingga approval bisa dilewati. Pindahkan aksi tulis ber-approval "
            f"ke tool induk.")

    nested = [n for n in spec.tools if n.startswith(SUBAGENT_TOOL_PREFIX)]
    if nested:
        raise SubagentConfigError(
            f"subagent '{spec_name}' DILARANG mendelegasikan lagi {nested} "
            f"(rekursi ditolak, K7).")

    max_tokens = min(int(spec.max_tokens),
                     int(getattr(settings, "SUBAGENT_MAX_TOKENS",
                                 spec.max_tokens)))
    timeout_s = min(int(spec.timeout_s),
                    int(getattr(settings, "SUBAGENT_TIMEOUT_S",
                                spec.timeout_s)))

    from langchain.agents import create_agent

    try:
        from langchain.agents.middleware import (
            ToolCallLimitMiddleware,
            ToolErrorMiddleware,
        )

        middleware: list = [
            # Rem khusus child (bukan build_middleware induk): tanpa
            # ContextEditing (konteks child kecil, jangan dihapus) dan tanpa
            # HITL (tool gated sudah ditolak di atas — middleware-nya inert).
            ToolCallLimitMiddleware(thread_limit=int(spec.thread_limit)),
            ToolErrorMiddleware(on_error=_child_tool_error),
        ]
    except ImportError:
        middleware = []

    if model is None:
        from app.infra.llm import get_llm

        model = get_llm(settings, session_id, streaming=False)

    failures: list[ToolFailure] = []
    wrapped = [_wrap_tool(spec_name, by_name[n], failures=failures,
                          sleep=_sleep)
               for n in spec.tools]
    child = create_agent(
        name=f"subagent-{spec.name}",
        model=model,
        tools=wrapped,
        system_prompt=spec.system_prompt + _CHILD_FOOTER,
        middleware=middleware,
    )
    tool_name = subagent_tool_name(spec.name)
    description = _SUBAGENT_DESCRIPTION.format(
        name=spec.name, description=spec.description,
        when_to_use=spec.when_to_use)

    @tool(tool_name, description=description)
    async def delegate(task: str, constraints: str = "") -> str:
        try:
            brief = Brief(
                task=task,
                constraints=tuple(c.strip() for c in constraints.split(",")
                                  if c.strip()),
            )
        except ValueError as exc:
            return (f"Tugas delegasi tidak valid: {exc} "
                    f"Tulis task konkret (maks 4000 karakter).")

        eff_timeout = timeout_s  # Brief tak bisa naikkan (clamp diam-diam)
        started = time.perf_counter()
        prompt = brief.task
        if brief.constraints:
            prompt += "\nSyarat:\n- " + "\n- ".join(brief.constraints)
        try:
            async with asyncio.timeout(eff_timeout):
                result = await child.ainvoke(
                    {"messages": [HumanMessage(content=prompt)]},
                    config={"tags": [SUBAGENT_TAG, f"subagent:{spec.name}"]},
                )
        except TimeoutError:
            ms = int((time.perf_counter() - started) * 1000)
            log.warning("subagent %s timeout (%ds)", spec.name, eff_timeout)
            return Report(status="timeout", failures=(
                ToolFailure(tool="*", error_class="timeout", attempts=1,
                            detail=f"melebihi {eff_timeout} dtk"),),
                          ms=ms).to_text()
        except Exception as exc:  # noqa: BLE001 - fail-soft ke Report
            ms = int((time.perf_counter() - started) * 1000)
            log.exception("subagent %s gagal", spec.name)
            fails = tuple(failures) or (ToolFailure(
                tool="*", error_class=error_class(exc), attempts=1,
                detail=type(exc).__name__),)
            return Report(status="failed", failures=fails, ms=ms).to_text()

        ms = int((time.perf_counter() - started) * 1000)
        answer = _last_ai_text(result)
        tokens = _sum_usage(result)
        fails = list(failures)
        if tokens > max_tokens:
            fails.append(ToolFailure(
                tool="*", error_class="budget", attempts=1,
                detail=f"{tokens} token melebihi anggaran {max_tokens}"))
        if not answer and not fails:
            fails.append(ToolFailure(
                tool="*", error_class="permanent", attempts=1,
                detail="respons kosong"))
        if not answer or fails:
            status = "failed" if not answer else "partial"
        else:
            status = "ok"
        log.info("subagent_end name=%s status=%s tokens=%d ms=%d",
                 spec.name, status, tokens, ms)
        return Report(status=status, answer=answer,
                      failures=tuple(fails), tokens_used=tokens,
                      ms=ms).to_text()

    return delegate


def _last_ai_text(result: Any) -> str:
    try:
        messages = (result.get("messages", []) if isinstance(result, dict)
                    else [])
    except Exception:
        return ""
    for message in reversed(messages):
        content = getattr(message, "content", "")
        cls = type(message).__name__
        if cls == "AIMessage" and isinstance(content, str) and content.strip():
            return content.strip()
    return ""


def _sum_usage(result: Any) -> int:
    total = 0
    try:
        messages = (result.get("messages", []) if isinstance(result, dict)
                    else [])
    except Exception:
        return 0
    for message in messages:
        usage = getattr(message, "usage_metadata", None) or {}
        total += int(usage.get("input_tokens", 0) or 0)
        total += int(usage.get("output_tokens", 0) or 0)
    return total
