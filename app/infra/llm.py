"""Factory LLM. Provider dipilih via MODEL_PROVIDER, dibungkus agar agent tidak pernah
instansiasi chat model langsung. Ganti model/provider = ubah .env + file ini.

Provider:
- openai: ChatOpenAI ke api.openai.com (MODEL_NAME, OPENAI_API_KEY).
- opencode_go: ChatOpenAI ke endpoint OpenAI-compatible OpenCode Go
  (OPENCODE_BASE_URL, mis. https://opencode.ai/zen/go/v1 — path
  /chat/completions ditambahkan otomatis oleh client).
  Hanya model di endpoint chat/completions yang didukung, mis:
  kimi-k2.7-code, kimi-k2.6, glm-5.3-flash, deepseek-v4-flash,
  deepseek-v4.1-flash, qwen3.8-flash (cek /v1/models untuk daftar terbaru).
  Model di endpoint /responses atau /messages (grok, gpt-luna, muse-spark,
  minimax, qwen-max) TIDAK kompatibel dengan ChatOpenAI.
"""

from langchain_openai import ChatOpenAI

from app.core.config import Settings
from app.core.errors import UpstreamAIError


def get_llm(settings: Settings, session_id: str | None = None):
    """Kembalikan chat model LangChain sesuai config.

    session_id: diisi thread_id oleh controller saat provider butuh session
    per-percakapan (OpenCode Go mewajibkan header x-opencode-session).
    Model tetap murah dibentuk; graph yang mahal di-compile sekali per agent.

    Raise UpstreamAIError (502 RFC7807) agar konsisten dengan error AI lain —
    bukan ValueError mentah yang jadi 500 default.
    """
    provider = (settings.MODEL_PROVIDER or "").strip().lower()
    if provider == "openai":
        if not settings.OPENAI_API_KEY:
            raise UpstreamAIError("OPENAI_API_KEY kosong. Isi .env dulu (lihat .env.example).")
        return ChatOpenAI(
            model=settings.MODEL_NAME,
            api_key=settings.OPENAI_API_KEY,
            temperature=settings.LLM_TEMPERATURE,
            timeout=settings.LLM_TIMEOUT_SECONDS,
        )
    if provider in ("opencode_go", "opencode"):
        if not settings.OPENCODE_API_KEY:
            raise UpstreamAIError("OPENCODE_API_KEY kosong. Isi .env dulu (lihat .env.example).")
        headers = {"user-agent": f"{settings.PROJECT_NAME}/v0"}
        if session_id:
            # Wajib oleh OpenCode Go: 1 sesi stabil per percakapan (docs: go/#where-can-i-use-it).
            headers["x-opencode-session"] = session_id
        # Catatan: ChatOpenAI otomatis menambah /chat/completions di belakang
        # base_url, jadi JANGAN sertakan path itu di .env (cukup .../zen/go/v1).
        return ChatOpenAI(
            model=settings.OPENCODE_MODEL,
            api_key=settings.OPENCODE_API_KEY,
            base_url=settings.OPENCODE_BASE_URL.rstrip("/"),
            temperature=settings.LLM_TEMPERATURE,
            timeout=settings.LLM_TIMEOUT_SECONDS,
            default_headers=headers,
        )
    raise UpstreamAIError(
        f"MODEL_PROVIDER tidak dikenal: {settings.MODEL_PROVIDER}. "
        "Pilih 'openai' atau 'opencode_go'."
    )
