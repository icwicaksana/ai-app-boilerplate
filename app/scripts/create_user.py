"""CLI pembuatan user (v0.4). Tanpa UI (sesuai Scope OUT roadmap).

Contoh:
    python -m app.scripts.create_user --tenant toko-a --username budi \
        --password 'rahasia-kuat' --scopes "chat jobs threads"

Kenapa CLI, bukan endpoint: pembuatan akun adalah operasi admin/out-of-band;
mengeksposnya sebagai endpoint publik menambah permukaan serangan.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.core.config import get_settings
from app.core.security import hash_password
from app.infra import users
from app.infra.postgres import close_pool, create_pool, open_pool


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="create_user",
                                description="Buat user auth di app-db.")
    p.add_argument("--tenant", required=True)
    p.add_argument("--username", required=True)
    p.add_argument("--password", required=True)
    p.add_argument("--scopes", default=users.DEFAULT_SCOPES)
    p.add_argument("--inactive", action="store_true",
                   help="Buat user nonaktif (default aktif).")
    return p.parse_args(argv)


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    pool = create_pool(settings)
    await open_pool(pool)
    try:
        await users.ensure_schema(pool)
        if await users.get_user(pool, args.tenant, args.username):
            print(f"GAGAL: user '{args.username}'@{args.tenant} sudah ada.",
                  file=sys.stderr)
            return 1
        user_id = await users.create_user(
            pool, tenant=args.tenant, username=args.username,
            password_hash=hash_password(args.password), scopes=args.scopes,
            is_active=not args.inactive,
        )
        print(f"OK: user id={user_id} tenant={args.tenant} "
              f"username={args.username} scopes='{args.scopes}' "
              f"active={not args.inactive}")
        return 0
    finally:
        await close_pool(pool)


def main(argv: list[str] | None = None) -> int:
    # WAJIB di Windows: psycopg async menolak ProactorEventLoop (pola sama
    # seperti entrypoint `python -m app.main`).
    from app.main import ensure_selector_loop

    ensure_selector_loop()
    args = _parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
