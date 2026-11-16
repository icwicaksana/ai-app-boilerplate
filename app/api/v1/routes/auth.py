"""Route auth (v0.4): login, refresh, logout, me.

Rate-limit login dipasang di Fase 3 (per IP+username, anti credential stuffing).
"""

from fastapi import APIRouter, Depends, Request

from app.controllers.AuthController import AuthController
from app.core.config import Settings, get_settings
from app.core.dependencies import (
    get_cache,
    get_current_claims,
    get_pool,
    get_request_id,
)
from app.core.security import TokenClaims
from app.core.ratelimit import check_login_limit
from app.infra.cache import CacheClient
from app.schemas.auth import (
    LoginRequest,
    LogoutRequest,
    MeResponse,
    RefreshRequest,
    TokenPair,
)

router = APIRouter(tags=["auth"])


def get_auth_controller(
    pool=Depends(get_pool),
    settings: Settings = Depends(get_settings),
    cache: CacheClient | None = Depends(get_cache),
) -> AuthController:
    return AuthController(settings, pool, cache=cache)


@router.post("/auth/login", response_model=TokenPair)
async def login(
    req: LoginRequest,
    request: Request,
    ctrl: AuthController = Depends(get_auth_controller),
    settings: Settings = Depends(get_settings),
    cache: CacheClient | None = Depends(get_cache),
) -> TokenPair:
    # Anti credential stuffing: batasi per (IP + username) SEBELUM verifikasi.
    await check_login_limit(request, workspace=req.workspace, username=req.username,
                            settings=settings, cache=cache)
    return await ctrl.login(workspace=req.workspace, username=req.username,
                            password=req.password,
                            request_id=get_request_id(request))


@router.post("/auth/refresh", response_model=TokenPair)
async def refresh(
    req: RefreshRequest,
    request: Request,
    ctrl: AuthController = Depends(get_auth_controller),
) -> TokenPair:
    return await ctrl.refresh(req.refresh_token, request_id=get_request_id(request))


@router.post("/auth/logout", status_code=204)
async def logout(
    request: Request,
    req: LogoutRequest | None = None,
    claims: TokenClaims = Depends(get_current_claims),
    ctrl: AuthController = Depends(get_auth_controller),
) -> None:
    await ctrl.logout(claims,
                      refresh_token=(req.refresh_token if req else None),
                      request_id=get_request_id(request))


@router.get("/auth/me", response_model=MeResponse)
async def me(
    claims: TokenClaims = Depends(get_current_claims),
    ctrl: AuthController = Depends(get_auth_controller),
) -> MeResponse:
    return ctrl.me(claims)
