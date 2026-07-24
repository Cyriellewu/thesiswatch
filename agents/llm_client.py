"""Structured LLM client for agent decisions."""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Type

from pydantic import BaseModel, ValidationError

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
except Exception:
    pass


@dataclass
class LLMCallResult:
    success: bool
    data: BaseModel | None = None
    raw_text: str = ""
    error: str = ""
    provider: str = ""
    model: str = ""
    attempts: int = 0


def _strip_markdown_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        i = t.find("\n")
        if i > 0:
            t = t[i + 1 :]
        if t.endswith("```"):
            t = t[:-3]
    return t.strip()


def _try_repair_json(text: str) -> str:
    t = _strip_markdown_fence(text)
    t = re.sub(r",\s*}", "}", t)
    t = re.sub(r",\s*]", "]", t)
    first = min((t.find("{") if "{" in t else 10**9), (t.find("[") if "[" in t else 10**9))
    if first != 10**9:
        t = t[first:]
    last = max(t.rfind("}"), t.rfind("]"))
    if last > 0:
        t = t[: last + 1]
    return t


def _coerce_agent_round_payload(parsed: object) -> object:
    """Accept a few common LLM shapes and convert to AgentRoundDecision JSON.

    Groq models sometimes obey the trading intent but return a legacy shape like
    {"action": "wait", "reason": "..."} or {"actions": [...]}. The strict
    Pydantic schema is still the final gate; this just reshapes obvious cases.
    """

    if not isinstance(parsed, dict):
        return parsed
    if "summary" in parsed and "decisions" in parsed:
        return parsed

    raw_decisions = parsed.get("decisions")
    if raw_decisions is None:
        raw_decisions = parsed.get("actions")
    if raw_decisions is None and parsed.get("action"):
        raw_decisions = [parsed]
    if raw_decisions is None:
        raw_decisions = []
    if isinstance(raw_decisions, dict):
        raw_decisions = [raw_decisions]
    if not isinstance(raw_decisions, list):
        raw_decisions = []

    decisions: list[dict] = []
    reason_bits: list[str] = []
    for item in raw_decisions:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action") or item.get("side") or "hold").strip().lower()
        if action not in {"buy", "add", "hold", "trim", "sell", "wait"}:
            action = "hold"
        ticker = str(
            item.get("ticker")
            or item.get("symbol")
            or item.get("asset")
            or ("CASH" if action == "wait" else "")
        ).strip().upper()
        if not ticker:
            ticker = "CASH"
            action = "wait"
        reasoning = str(
            item.get("reasoning")
            or item.get("reason")
            or item.get("plain_reason")
            or parsed.get("reasoning")
            or parsed.get("reason")
            or "当前信息不足，先保持仓位并等待下一轮新闻和价格确认。"
        ).strip()
        if len(reasoning) < 10:
            reasoning = f"{reasoning}，等待下一轮新闻和价格确认。"
        reason_bits.append(reasoning)
        size = item.get("size_pct", item.get("target_pct", item.get("allocation_pct", 0.0)))
        try:
            size_f = float(size or 0.0)
        except (TypeError, ValueError):
            size_f = 0.0
        if size_f > 1.0:
            size_f = size_f / 100.0
        conf = item.get("confidence", parsed.get("confidence", 0.5))
        try:
            conf_f = float(conf or 0.5)
        except (TypeError, ValueError):
            conf_f = 0.5
        decisions.append(
            {
                "ticker": ticker,
                "action": action,
                "size_pct": max(0.0, min(1.0, size_f)),
                "confidence": max(0.0, min(1.0, conf_f)),
                "entry_price_max": item.get("entry_price_max") or item.get("limit_price"),
                "stop_loss_price": item.get("stop_loss_price"),
                "take_profit_price": item.get("take_profit_price"),
                "expected_holding_days": int(item.get("expected_holding_days") or parsed.get("expected_holding_days") or 14),
                "reasoning": reasoning[:300],
                "signals_used": item.get("signals_used") if isinstance(item.get("signals_used"), list) else [],
            }
        )

    summary = str(parsed.get("summary") or parsed.get("plain_reason") or parsed.get("reason") or "").strip()
    if len(summary) < 10:
        summary = (reason_bits[0] if reason_bits else "当前信息不足，先保持仓位并等待下一轮确认。")[:200]
    macro = str(parsed.get("macro_view") or "neutral").strip()
    if macro not in {"risk_on", "neutral", "risk_off"}:
        macro = "neutral"
    try:
        check = int(parsed.get("next_check_in_hours") or 24)
    except (TypeError, ValueError):
        check = 24
    return {
        "summary": summary[:200],
        "macro_view": macro,
        "decisions": decisions,
        "next_check_in_hours": max(1, min(168, check)),
    }


def _model_validate_payload(schema: Type[BaseModel], parsed: object) -> BaseModel:
    if getattr(schema, "__name__", "") == "AgentRoundDecision":
        parsed = _coerce_agent_round_payload(parsed)
    return schema.model_validate(parsed)


def _call_gemini(system_prompt: str, user_prompt: str, schema: Type[BaseModel], model_name: str) -> LLMCallResult:
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        return LLMCallResult(False, error="GEMINI_API_KEY not set", provider="gemini", model=model_name)
    try:
        import google.generativeai as genai  # noqa: PLC0415

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(
            model_name=model_name,
            system_instruction=system_prompt,
            generation_config={
                "response_mime_type": "application/json",
                "temperature": 0.3,
                "max_output_tokens": 4096,
            },
        )
        resp = model.generate_content(user_prompt)
        raw = str(getattr(resp, "text", "") or "")
    except Exception as e:
        return LLMCallResult(False, error=f"Gemini call failed: {e}", provider="gemini", model=model_name)
    try:
        parsed = json.loads(_try_repair_json(raw))
        valid = _model_validate_payload(schema, parsed)
    except (json.JSONDecodeError, ValidationError) as e:
        return LLMCallResult(False, raw_text=raw, error=f"Gemini parse/validate failed: {e}", provider="gemini", model=model_name)
    return LLMCallResult(True, data=valid, raw_text=raw, provider="gemini", model=model_name)


def _call_groq(system_prompt: str, user_prompt: str, schema: Type[BaseModel], model_name: str) -> LLMCallResult:
    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key:
        return LLMCallResult(False, error="GROQ_API_KEY not set", provider="groq", model=model_name)
    try:
        from groq import Groq  # noqa: PLC0415

        client = Groq(api_key=api_key)
        resp = client.chat.completions.create(
            model=model_name,
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            temperature=0.3,
            max_tokens=4096,
            response_format={"type": "json_object"},
        )
        raw = str(resp.choices[0].message.content or "")
    except Exception as e:
        return LLMCallResult(False, error=f"Groq call failed: {e}", provider="groq", model=model_name)
    try:
        parsed = json.loads(_try_repair_json(raw))
        valid = _model_validate_payload(schema, parsed)
    except (json.JSONDecodeError, ValidationError) as e:
        return LLMCallResult(False, raw_text=raw, error=f"Groq parse/validate failed: {e}", provider="groq", model=model_name)
    return LLMCallResult(True, data=valid, raw_text=raw, provider="groq", model=model_name)


def call_with_fallback(
    system_prompt: str,
    user_prompt: str,
    schema: Type[BaseModel],
    primary: tuple[str, str] = ("gemini", "gemini-2.5-flash"),
    fallback: tuple[str, str] = ("groq", "llama-3.3-70b-versatile"),
    max_retries_per_provider: int = 2,
) -> LLMCallResult:
    gem_ok = bool(os.environ.get("GEMINI_API_KEY", "").strip())
    groq_ok = bool(os.environ.get("GROQ_API_KEY", "").strip())
    # Fast-fail on missing credentials to avoid slow useless retries.
    if not gem_ok and not groq_ok:
        return LLMCallResult(
            False,
            error="No LLM credentials in current process (GEMINI_API_KEY/GROQ_API_KEY).",
            provider="none",
            model="none",
            attempts=0,
        )

    last: LLMCallResult | None = None
    for provider, model in (primary, fallback):
        if provider == "gemini" and not gem_ok:
            last = LLMCallResult(False, error="GEMINI_API_KEY not set", provider=provider, model=model, attempts=0)
            continue
        if provider == "groq" and not groq_ok:
            last = LLMCallResult(False, error="GROQ_API_KEY not set", provider=provider, model=model, attempts=0)
            continue
        for i in range(1, max_retries_per_provider + 1):
            log.info("LLM call: provider=%s model=%s attempt=%s", provider, model, i)
            if provider == "gemini":
                out = _call_gemini(system_prompt, user_prompt, schema, model)
            elif provider == "groq":
                out = _call_groq(system_prompt, user_prompt, schema, model)
            else:
                out = LLMCallResult(False, error=f"Unknown provider: {provider}", provider=provider, model=model)
            out.attempts = i
            last = out
            if out.success:
                log.info("LLM call success: %s/%s attempt=%s", provider, model, i)
                return out
            log.warning("LLM call failed: %s/%s attempt=%s error=%s", provider, model, i, out.error)
            time.sleep(1.0)
    return last or LLMCallResult(False, error="No provider available")
