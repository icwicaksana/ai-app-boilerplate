"""Policy HITL (v0.6): tool mana yang butuh approval, kapan, dan batasnya.

Satu sumber kebenaran untuk:
- middleware (`HumanInTheLoopMiddleware.interrupt_on`),
- pencatatan approval (tool, snapshot aksi),
- TOCTOU guard (policy_version + action_hash).

Prinsip: **default deny untuk aksi berbahaya** (tool terdaftar dengan
`enabled=True`), dan threshold dievaluasi dari argumen tool (`when`).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

APPROVE = "approve"
REJECT = "reject"
EDIT = "edit"
DECISIONS = (APPROVE, REJECT, EDIT)


@dataclass(frozen=True)
class ToolPolicy:
    """Kebijakan satu tool. `threshold` dibandingkan ke `amount_field`."""

    enabled: bool = True
    amount_field: str | None = None
    threshold: float | None = None
    allowed_decisions: tuple[str, ...] = DECISIONS
    expires_in_seconds: int = 86_400
    description: str = "Tool execution requires approval"

    def requires_approval(self, args: dict[str, Any] | None) -> bool:
        """True bila panggilan ini harus dijeda untuk approval."""
        if not self.enabled:
            return False
        if self.threshold is None or not self.amount_field:
            return True  # semua panggilan tool ini butuh approval
        try:
            value = float((args or {}).get(self.amount_field, 0) or 0)
        except (TypeError, ValueError):
            return True  # argumen tak terbaca = perlakukan sebagai berisiko
        return value > float(self.threshold)


# Policy default template (UMKM): refund di atas Rp 500.000 butuh approval.
HITL_POLICY: dict[str, ToolPolicy] = {
    "process_refund": ToolPolicy(
        amount_field="amount", threshold=500_000,
        description="Refund di atas Rp 500.000 butuh persetujuan manusia",
    ),
}


def policy_for(tool_name: str) -> ToolPolicy | None:
    return HITL_POLICY.get(tool_name)


def interrupt_on_config() -> dict[str, Any]:
    """Bangun `interrupt_on` untuk HumanInTheLoopMiddleware.

    `when` menerima ToolCallRequest; kita evaluasi policy dari argumennya.
    """
    from langchain.agents.middleware import InterruptOnConfig

    config: dict[str, Any] = {}
    for name, pol in HITL_POLICY.items():
        if not pol.enabled:
            continue
        config[name] = InterruptOnConfig(
            allowed_decisions=list(pol.allowed_decisions),
            description=pol.description,
            when=(lambda req, _pol=pol: _args_of(req) is not None
                  and _pol.requires_approval(_args_of(req))),
        )
    return config


def _args_of(request: Any) -> dict[str, Any] | None:
    tool_call = getattr(request, "tool_call", None) or {}
    args = tool_call.get("args") if isinstance(tool_call, dict) else None
    return args if isinstance(args, dict) else None


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str,
                      separators=(",", ":"))


def action_hash(tool_name: str, args: dict[str, Any] | None) -> str:
    """Hash aksi yang disetujui — dihitung ulang saat eksekusi (TOCTOU guard)."""
    raw = canonical_json({"tool": tool_name, "args": args or {}})
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def policy_version() -> str:
    """Versi policy (hash) — dicatat di audit agar keputusan bisa direkonstruksi."""
    payload = {
        name: {
            "enabled": p.enabled, "amount_field": p.amount_field,
            "threshold": p.threshold,
            "allowed_decisions": list(p.allowed_decisions),
            "expires_in_seconds": p.expires_in_seconds,
        }
        for name, p in sorted(HITL_POLICY.items())
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()[:16]
