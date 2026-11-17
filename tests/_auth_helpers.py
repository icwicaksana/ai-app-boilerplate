"""Helper auth untuk test HTTP (v0.4).

Kenapa ada: default v0.4 = `AUTH_MODE=jwt`, jadi test yang memanggil route
butuh Bearer token. Helper ini membangun Settings DETERMINISTIK (tidak
bergantung `.env`) lalu meng-override dependency `get_settings`, sehingga
token yang ditandatangani test pasti diterima aplikasi.
"""

from __future__ import annotations

from fastapi import FastAPI

from app.core.config import Settings, get_settings
from app.core.security import create_access_token
from app.main import create_app

TEST_SECRET = "test-secret-" + "0" * 32  # 43 char, > batas minimal 32


def test_settings(**kw) -> Settings:
    args = {
        "_env_file": None,
        "ENVIRONMENT": "dev",
        "AUTH_MODE": "jwt",
        "JWT_SECRET": TEST_SECRET,
        "CACHE_URL": "",
        "ENABLE_DOCS": False,
        "PHOENIX_ENABLED": False,  # test TIDAK boleh menembak collector
        "DATABASE_URL": "postgresql://test/test",
    }
    args.update(kw)
    return Settings(**args)


def app_with_settings(**kw) -> FastAPI:
    """App dengan settings DETERMINISTIK (tanpa .env) + override dependency."""
    settings = test_settings(**kw)
    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings
    return app


# v1.0 S4: default = semua scope (tes gate scope memakai scopes eksplisit).
FULL_SCOPES = ("chat", "jobs", "threads", "approve", "documents")


def bearer(workspace: str = "toko-a", user: str = "budi",
           scopes: tuple[str, ...] = FULL_SCOPES,
           settings: Settings | None = None,
           clearance: str = "public") -> dict[str, str]:
    s = settings or test_settings()
    token, _ = create_access_token(s, workspace=workspace, user=user,
                                   scopes=scopes, clearance=clearance)
    return {"Authorization": f"Bearer {token}"}


def auth_headers(workspace: str = "toko-a", user: str = "budi",
                 request_id: str | None = None,
                 clearance: str = "public") -> dict[str, str]:
    headers = bearer(workspace=workspace, user=user, clearance=clearance)
    if request_id:
        headers["X-Request-ID"] = request_id
    return headers
