from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)


def generate_ollama(prompt: str, *, model: str | None = None) -> tuple[str, str]:
    base = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    m = (model or os.environ.get("OLLAMA_MODEL") or "qwen2.5:7b").strip()

    if os.environ.get("ALPHAWATCH_OFFLINE") == "1":
        return "", m

    try:
        payload = {"model": m, "prompt": prompt, "stream": False}
        timeout = float(os.environ.get("OLLAMA_TIMEOUT_SECONDS", "120"))
        resp = httpx.post(f"{base}/api/generate", json=payload, timeout=timeout)
        resp.raise_for_status()
        body = resp.json()
        txt = isinstance(body, dict) and body.get("response") or ""
        return (txt.strip() or "[empty Ollama response]", m)
    except Exception as e:
        logger.warning("Ollama request failed", exc_info=True)
        return f"[ollama error: {e}]", m
