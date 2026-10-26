"""Factory LLM — single gateway LiteLLM proxy (v0.2, ADR-01/ADR-04).

Semua akses LLM lewat proxy OpenAI-compatible. Agent tidak pernah instansiasi
chat model langsung. Ganti model/provider = ubah via UI proxy atau .env
(LITELLM_MODEL), tanpa ubah kode. Tanpa cabang direct (ADR-03): proxy down =
UpstreamAIError eksplisit, bukan fallback diam-diam.

- Client: ChatLiteLLM — preserves reasoning_content/reasoning_items yang
  dibuang ChatOpenAI (isu langchain-ai#34706/#35059).
- Header per-request: x-opencode-session (wajib upstream OpenCode Go, diisi
  thread_id; upstream lain abaikan). Proxy teruskan karena
  forward_client_headers_to_llm_api: true.
- Atribusi spend: extra_body.metadata.spend_logs_metadata (project + thread).
  (Jalur header x-litellm-spend-logs-metadata terbukti tak mendarat di v1.105.1.)
- Retry/timeout/fallback = milik proxy (num_retries di YAML). Client TIDAK
  retry (max_retries=0) untuk cegah badai multiplikatif.
"""

from langchain_litellm import ChatLiteLLM

from app.core.config import Settings
from app.core.errors import UpstreamAIError


def get_llm(settings: Settings, session_id: str | None = None):
    """Kembalikan chat model LangGraph via gateway.

    session_id: diisi thread_id oleh controller (1 sesi stabil per percakapan).
    Instance LLM murah dibentuk per build; GRAPH dibangun per request (ADR-11)
    — jangan cache graph bila header spend per-thread masih menempel di LLM.

    Raise UpstreamAIError (502 RFC7807) agar konsisten dengan error AI lain.
    """
    if not settings.LITELLM_API_KEY:
        raise UpstreamAIError("LITELLM_API_KEY kosong. Isi .env dulu (lihat .env.example).")
    if not settings.LITELLM_MODEL:
        raise UpstreamAIError("LITELLM_MODEL kosong. Isi .env dulu (lihat .env.example).")
    project = settings.LITELLM_PROJECT_TAG.strip() or settings.PROJECT_NAME
    spend_meta: dict = {"project_id": project}
    headers: dict[str, str] = {}
    if session_id:
        spend_meta["thread_id"] = session_id
        # Wajib OpenCode Go (docs: go/#where-can-i-use-it); upstream lain abaikan.
        headers["x-opencode-session"] = session_id
    # Atribusi spend via BODY (extra_body.metadata) — jalur header
    # x-litellm-spend-logs-metadata terbukti tidak mendarat di proxy v1.105.1.
    # Lihat: docs proxy/cost_tracking (body method).
    # custom_llm_provider="openai": paksa transport OpenAI-compatible ke base_url
    # proxy TANPA inferensi provider (model tanpa prefix seperti "deepseek-v4.1-flash"
    # otherwise ditolak SDK dengan "LLM Provider NOT provided"). Nama model dikirim
    # verbatim agar cocok dengan model_name di proxy (jangan tambah prefix sendiri).
    return ChatLiteLLM(
        model=settings.LITELLM_MODEL.strip(),
        custom_llm_provider="openai",
        api_key=settings.LITELLM_API_KEY,
        base_url=settings.LITELLM_BASE_URL.rstrip("/"),
        temperature=settings.LLM_TEMPERATURE,
        request_timeout=settings.LLM_TIMEOUT_SECONDS,
        max_retries=0,  # retry = milik proxy (num_retries di YAML)
        extra_headers=headers,
        model_kwargs={"extra_body": {"metadata": {"spend_logs_metadata": spend_meta}}},
    )
