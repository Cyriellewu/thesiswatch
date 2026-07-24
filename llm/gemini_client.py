from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def generate_gemini(prompt: str, *, model: str | None = None) -> tuple[str, str]:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    m = (
        model
        or os.environ.get("GEMINI_FLASH_LITE_MODEL")
        or os.environ.get("GEMINI_FLASH_MODEL")
        or "gemini-2.0-flash"
    ).strip()

    if not key:
        return "", m

    try:
        import google.generativeai as genai  # noqa: PLC0415

        genai.configure(api_key=key)
        client = genai.GenerativeModel(m)
        resp = client.generate_content(prompt)
        text = getattr(resp, "text", None) or ""
        if not text.strip() and resp.candidates:
            parts = getattr(resp.candidates[0].content, "parts", []) or []
            text = "".join(getattr(p, "text", "") for p in parts)
        return (text.strip() or "[empty Gemini response]", m)
    except Exception as e:
        logger.warning("Gemini generation failed", exc_info=True)
        return f"[gemini error: {e}]", m
