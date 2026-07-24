from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def generate_groq(prompt: str, *, model: str | None = None) -> tuple[str, str, tuple[int, int] | None]:
    key = os.environ.get("GROQ_API_KEY", "").strip()
    m = (
        model or os.environ.get("GROQ_MODEL") or "llama-3.3-70b-versatile"
    ).strip()

    if not key:
        return "", m, None

    try:
        from groq import Groq  # noqa: PLC0415

        client = Groq(api_key=key)
        completion = client.chat.completions.create(
            model=m,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=int(os.environ.get("GROQ_MAX_TOKENS", "2048")),
            temperature=0.35,
        )
        choice = completion.choices[0]
        txt = getattr(choice.message, "content", None) or ""
        usage_t: tuple[int, int] | None = None
        u = getattr(completion, "usage", None)
        if u is not None:
            if isinstance(u, dict):
                pi = u.get("prompt_tokens") or u.get("input_tokens")
                ci = u.get("completion_tokens") or u.get("output_tokens")
            else:
                pi = getattr(u, "prompt_tokens", None) or getattr(u, "input_tokens", None)
                ci = getattr(u, "completion_tokens", None) or getattr(u, "output_tokens", None)
            if pi is not None and ci is not None:
                try:
                    usage_t = (int(pi), int(ci))
                except (TypeError, ValueError):
                    usage_t = None
        return txt.strip(), m, usage_t
    except Exception as e:
        logger.warning("Groq completion failed", exc_info=True)
        return f"[groq error: {e}]", m, None
