"""Aggregate router v1: tambah route baru = tambah 1 baris di sini."""

from fastapi import APIRouter

from app.api.v1.routes import chat, health, jobs, resume

router = APIRouter()
router.include_router(health.router)
router.include_router(chat.router)
router.include_router(jobs.router)
router.include_router(resume.router)
