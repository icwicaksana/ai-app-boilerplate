"""Probe beban platform (V1-F): angka SLO nyata lapis platform.

Ned: server live di BASE (lihat load/k6/README — start manual).
Ukur: livez/ready/me p50/p95/err + chat SSE first-byte (bila LLM hidup).
Pakai: BASE=http://localhost:8123 K6_PASSWORD=... python probe.py
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys
import time

import httpx

BASE = os.environ.get("BASE", "http://localhost:8123")
V1 = f"{BASE}/api/v1"
N_FAST = int(os.environ.get("N_FAST", "200"))
N_CHAT = int(os.environ.get("N_CHAT", "10"))
CONC = int(os.environ.get("CONC", "10"))


def pct(lat: list[float], p: float) -> float:
    if not lat:
        return 0.0
    s = sorted(lat)
    return s[min(len(s) - 1, int(len(s) * p / 100))]


async def _get(client: httpx.AsyncClient, path: str,
               headers: dict | None = None) -> tuple[bool, float]:
    t0 = time.perf_counter()
    try:
        r = await client.get(f"{V1}{path}", headers=headers, timeout=10.0)
        return (200 <= r.status_code < 300,
                (time.perf_counter() - t0) * 1000)
    except Exception:
        return (False, (time.perf_counter() - t0) * 1000)


async def hammer(client: httpx.AsyncClient, path: str, n: int,
                 headers: dict | None = None) -> dict:
    sem = asyncio.Semaphore(CONC)

    async def _one():
        async with sem:
            return await _get(client, path, headers)

    outs = await asyncio.gather(*(_one() for _ in range(n)))
    oks = [ms for ok, ms in outs if ok]
    return {"n": n, "ok": len(oks),
            "err_pct": round(100 * (n - len(oks)) / n, 2),
            "p50": round(statistics.median(oks), 1) if oks else 0.0,
            "p95": round(pct(oks, 95), 1) if oks else 0.0}


async def chat_first_byte(client: httpx.AsyncClient,
                          token: str) -> dict:
    """First-byte SSE via streaming (tanpa menunggu body penuh)."""
    lat: list[float] = []
    fails = 0
    for _ in range(N_CHAT):
        t0 = time.perf_counter()
        try:
            async with client.stream(
                    "POST", f"{V1}/chat/stream",
                    json={"agent": "example_chat",
                          "input": "halo probe",
                          "thread_id": "toko-a:budi:probe-slo"},
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=60.0) as r:
                if r.status_code != 200:
                    fails += 1
                    continue
                async for _ in r.aiter_bytes():
                    lat.append((time.perf_counter() - t0) * 1000)
                    break
        except Exception:
            fails += 1
    return {"n": N_CHAT, "ok": len(lat),
            "err_pct": round(100 * fails / N_CHAT, 2),
            "p50": round(statistics.median(lat), 1) if lat else 0.0,
            "p95": round(pct(lat, 95), 1) if lat else 0.0}


async def main() -> int:
    out: dict = {"base": BASE, "n_fast": N_FAST, "n_chat": N_CHAT}
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(f"{V1}/livez", timeout=5.0)
            if r.status_code != 200:
                print(f"server tidak sehat: {r.status_code}", file=sys.stderr)
                return 2
        except Exception as exc:
            print(f"server tak terjangkau: {exc}", file=sys.stderr)
            return 2
        out["livez"] = await hammer(client, "/livez", N_FAST)
        out["ready"] = await hammer(client, "/ready", N_FAST)
        token = ""
        pw = os.environ.get("K6_PASSWORD", "")
        if pw:
            try:
                r = await client.post(
                    f"{V1}/auth/login",
                    json={"workspace": "toko-a", "username": "budi",
                          "password": pw}, timeout=10.0)
                if r.status_code == 200:
                    token = r.json()["access_token"]
            except Exception as exc:
                print(f"login gagal: {exc}", file=sys.stderr)
        if token:
            h = {"Authorization": f"Bearer {token}"}
            out["me"] = await hammer(client, "/auth/me", N_FAST, h)
            out["chat_first_byte"] = await chat_first_byte(client, token)
        else:
            out["me"] = {"skipped": "tanpa K6_PASSWORD/token"}
            out["chat_first_byte"] = {"skipped": "tanpa K6_PASSWORD/token"}
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
