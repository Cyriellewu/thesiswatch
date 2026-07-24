"""解析 LLM JSON 并记入 token_usage（粗估 token）。"""

from __future__ import annotations

import json
import re
import sqlite3

from llm.router import TokenGuard


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def extract_json_object(text: str) -> dict | None:
    t = text.strip()
    if not t:
        return None
    # remove markdown fences if present
    t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*```$", "", t)
    # try direct parse first
    try:
        obj = json.loads(t)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    # fallback: pick largest JSON-like object block
    candidates = re.findall(r"\{[\s\S]*\}", t)
    for chunk in sorted(candidates, key=len, reverse=True):
        # tolerate trailing commas
        cleaned = re.sub(r",\s*([}\]])", r"\1", chunk)
        try:
            obj = json.loads(cleaned)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    return None


def _is_decision_payload(obj: dict | None) -> bool:
    if not isinstance(obj, dict):
        return False
    required = {"action", "plain_reason", "plain_risk", "next_watch"}
    return required.issubset(set(obj.keys()))


def call_agent_decision_llm(
    conn: sqlite3.Connection,
    *,
    prompt: str,
    agent_id: str,
    reason_for_call: str,
) -> tuple[dict | None, str, str, str]:
    """
    返回 (parsed_json_or_none, raw_text, routed, model)。
    """

    tg = TokenGuard()
    parse_hint = (
        "\n\nIMPORTANT: Return ONE valid JSON object only. "
        "Do not use markdown fences, comments, or extra prose."
    )
    attempts = [prompt, prompt + parse_hint, prompt + parse_hint + "\nIf unsure, use action=HOLD with complete fields."]
    last_out: dict | None = None
    parsed: dict | None = None
    total_in = 0
    total_out = 0
    for ptxt in attempts:
        out = tg.call(f"agent_decision:{agent_id}", ptxt)
        last_out = out
        raw_try = str(out.get("text") or "")
        up = out.get("usage_prompt")
        uc = out.get("usage_completion")
        total_in += int(up) if up is not None else estimate_tokens(ptxt)
        total_out += int(uc) if uc is not None else estimate_tokens(raw_try)
        parsed = extract_json_object(raw_try)
        if _is_decision_payload(parsed):
            break

    # Second-stage hardening: if still invalid, ask model to repair into strict JSON.
    if not _is_decision_payload(parsed) and last_out is not None:
        raw_prev = str(last_out.get("text") or "")
        if raw_prev.strip():
            repair_prompt = (
                "You are a JSON repair tool. Convert the following model output into ONE valid JSON object.\n"
                "Rules:\n"
                "1) Output JSON only, no markdown.\n"
                "2) Must include keys: action,symbol,symbol_to,dollar_amount,plain_reason,plain_risk,next_watch,confidence.\n"
                "3) action must be one of HOLD|BUY|SELL|TRIM|ROTATE.\n"
                "4) confidence must be a number between 0 and 1.\n"
                "5) If missing values, choose safe defaults (e.g. HOLD, dollar_amount=0).\n\n"
                f"Raw output to repair:\n{raw_prev}"
            )
            out2 = tg.call(f"agent_decision_repair:{agent_id}", repair_prompt)
            raw2 = str(out2.get("text") or "")
            up2 = out2.get("usage_prompt")
            uc2 = out2.get("usage_completion")
            total_in += int(up2) if up2 is not None else estimate_tokens(repair_prompt)
            total_out += int(uc2) if uc2 is not None else estimate_tokens(raw2)
            p2 = extract_json_object(raw2)
            if _is_decision_payload(p2):
                parsed = p2
                last_out = out2

    out = last_out or {}
    raw = str(out.get("text") or "")
    routed = str(out.get("routed") or "")
    model = str(out.get("model") or "")
    ok_parse = _is_decision_payload(parsed)
    fallback_used = 1 if routed and routed != "gemini-lite" else 0
    conn.execute(
        """INSERT INTO token_usage
           (feature, agent_id, model, input_tokens, output_tokens, success, fallback_used, reason_for_call)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            "agent_decision",
            agent_id,
            model,
            total_in,
            total_out,
            1 if ok_parse else 0,
            fallback_used,
            reason_for_call[:900],
        ),
    )
    return parsed, raw, routed, model
