from __future__ import annotations

import logging
import os
import json
from typing import Any
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)

_PRIORITY_SAFE = frozenset({"min", "low", "default", "high", "max"})


def _ascii_compact(s: str, *, maxlen: int, fallback: str) -> str:
    """ntfy prefers ASCII titles/tags even with JSON payloads; avoids edge-case proxy issues."""
    raw = "".join(ch for ch in (s or "") if ord(ch) < 128)
    raw = raw.strip()
    if not raw:
        return fallback[:maxlen]
    return raw[:maxlen]


def _ascii_tag_list(tags: list[str]) -> list[str]:
    """Drop non-ASCII tag tokens."""
    out: list[str] = []
    for t in tags:
        t2 = "".join(ch for ch in (t or "") if ord(ch) < 128).strip()
        if t2:
            out.append(t2)
    return out


def _safe_topic(topic: str) -> str:
    """Restrict topic to ASCII-ish token to avoid transport edge-cases."""
    cleaned = "".join(ch if ord(ch) < 128 else "-" for ch in (topic or ""))
    cleaned = cleaned.strip().replace(" ", "-")
    cleaned = "".join(ch for ch in cleaned if ch.isalnum() or ch in "-_.")
    cleaned = cleaned.strip("-_.")
    return cleaned[:120] or "alphawatch"


def _topic_url(server: str, topic: str) -> str:
    base = server.rstrip("/")
    safe_topic = quote(topic, safe="")
    return f"{base}/{safe_topic}"


def send_ntfy(
    title: str,
    message: str,
    *,
    topic: str | None = None,
    priority: str = "default",
    tags: str | None = None,
    click: str | None = None,
    timeout: float = 20.0,
) -> bool:
    """
    Publish to ntfy (https://ntfy.sh or self-hosted).

    使用 JSON 载荷发布，正文可含中文等 UTF-8 字符。
    `title` 与标签会先裁成 **纯 ASCII**（非 ASCII 会被去掉；空则回退为 AlphaWatch），
    避免部分环境仍按 latin-1 处理元数据。

    JSON 参见: https://docs.ntfy.sh/publish/#publish-as-json
    """
    topic = (topic or os.environ.get("NTFY_TOPIC") or "").strip()
    if not topic:
        logger.warning("ntfy skipped: NTFY_TOPIC is empty")
        return False

    server = os.environ.get("NTFY_SERVER_URL", "https://ntfy.sh").strip()
    token_raw = os.environ.get("NTFY_ACCESS_TOKEN", "").strip()
    token = token_raw if token_raw.isascii() else ""
    if token_raw and not token:
        logger.warning("ntfy token has non-ASCII chars; skipping Authorization header")

    prio = priority if priority in _PRIORITY_SAFE else "default"
    topic_safe = _safe_topic(topic)
    if topic_safe != topic:
        logger.warning("ntfy topic normalized to ASCII-safe token: %s", topic_safe)
    url = _topic_url(server, topic_safe)
    headers: dict[str, str] = {
        "Content-Type": "application/json; charset=utf-8",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    safe_title = _ascii_compact(title or "", maxlen=200, fallback="AlphaWatch")

    payload: dict[str, Any] = {
        "title": safe_title,
        "message": message[:16000],
        "priority": prio,
    }

    if tags:
        raw_tags = [t.strip() for t in tags.replace("，", ",").split(",") if t.strip()]
        tag_list = _ascii_tag_list(raw_tags)
        if tag_list:
            payload["tags"] = tag_list
    if click:
        payload["click"] = click[:500]

    try:
        # Avoid inheriting proxy/auth/env headers that may contain non-latin-1 chars.
        with requests.Session() as sess:
            sess.trust_env = False
            resp = sess.post(
                url,
                headers=headers,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                timeout=timeout,
            )
        if not resp.ok:
            logger.warning("ntfy HTTP %s: %s", resp.status_code, resp.text[:300])
            return False
        return True
    except (requests.RequestException, UnicodeEncodeError):
        logger.exception("ntfy request failed")
        return False
    except Exception:
        logger.exception("ntfy request failed")
        return False
