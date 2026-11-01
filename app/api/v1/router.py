"""Aggregate router v1: tambah route baru = tambah 1 baris di sini."""

from fastapi import APIRouter

from app.api.v1.routes import auth, chat, health, jobs, resume, threads, ws

router = APIRouter()
router.include_router(health.router)
router.include_router(auth.router)
router.include_router(chat.router)
router.include_router(ws.router)
router.include_router(jobs.router)
router.include_router(resume.router)
router.include_router(threads.router)
