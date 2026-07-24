from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from llm.gemini_client import generate_gemini
from llm.groq_client import generate_groq
from llm.ollama_client import generate_ollama

logger = logging.getLogger(__name__)


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _persist_llm_call(provider_key: str, model: str, task: str) -> None:
    if os.environ.get("ALPHA_LLML_LOG_DISABLED", "").strip() == "1":
        return
    try:
        from db.client import get_conn  # noqa: PLC0415

        conn = get_conn(read_only=False)
        try:
            conn.execute(
                "INSERT INTO llm_calls (provider, model, task) VALUES (?,?,?)",
                (provider_key, model, task[:500]),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.debug("llm_calls persistence skipped", exc_info=True)


def _reply_bad(reply: str) -> bool:
    r = reply.strip()
    return (not r) or r.startswith("[gemini error:") or r.startswith("[groq error:")


@dataclass
class Limits:
    flash_lite_cap: int = 1000
    flash_cap: int = 250
    groq_cap: int = 14400
    lite_buffer: int = 100
    groq_buffer: int = 1000


class TokenGuard:
    """Prefer Gemini (lite env model) → Groq → Ollama; persists one llm_calls row per invocation."""

    def __init__(self, limits: Limits | None = None) -> None:
        self.limits = limits or Limits()
        self._lite_used_session = 0
        self._groq_used_session = 0

    def lite_used_total(self) -> int:
        return _env_int("ALPHA_LLM_LITE_COUNT", self._lite_used_session)

    def groq_used_total(self) -> int:
        return _env_int("ALPHA_LLM_GROQ_COUNT", self._groq_used_session)

    def lite_remaining(self) -> int:
        return max(0, self.limits.flash_lite_cap - self.lite_used_total())

    def groq_remaining(self) -> int:
        return max(0, self.limits.groq_cap - self.groq_used_total())

    def flash_remaining(self) -> int:
        used = _env_int("ALPHA_LLM_FLASH_COUNT", 0)
        return max(0, self.limits.flash_cap - used)

    def call(self, task: str, prompt: str) -> dict:
        gemini_model = (
            os.environ.get("GEMINI_FLASH_LITE_MODEL")
            or os.environ.get("GEMINI_FLASH_MODEL")
            or "gemini-2.0-flash-lite"
        ).strip()
        groq_model = (os.environ.get("GROQ_MODEL") or "llama-3.3-70b-versatile").strip()
        ollama_model = (os.environ.get("OLLAMA_MODEL") or "qwen2.5:7b").strip()

        gemini_ok = bool(os.environ.get("GEMINI_API_KEY", "").strip())
        groq_ok = bool(os.environ.get("GROQ_API_KEY", "").strip())

        routed = "groq"
        txt, resolved = "", groq_model
        usage_prompt: int | None = None
        usage_completion: int | None = None

        if gemini_ok and self.lite_remaining() > self.limits.lite_buffer:
            routed = "gemini-lite"
            txt, resolved = generate_gemini(prompt, model=gemini_model)
            self._lite_used_session += 1

        if _reply_bad(txt):
            if groq_ok and self.groq_remaining() > self.limits.groq_buffer:
                routed = "groq"
                txt, resolved, groq_usage = generate_groq(prompt, model=groq_model)
                self._groq_used_session += 1
                if groq_usage:
                    usage_prompt, usage_completion = groq_usage

        if _reply_bad(txt) or (not txt.strip()):
            routed = "ollama"
            txt, resolved = generate_ollama(prompt, model=ollama_model)

        _persist_llm_call(routed, resolved, task)
        return {
            "task": task,
            "routed": routed,
            "model": resolved,
            "text": txt,
            "usage_prompt": usage_prompt,
            "usage_completion": usage_completion,
        }
