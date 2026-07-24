"""Willow agent 记忆层(阶段 5)——本地 SQLite,不需要任何 API key。

能力:
- 记录每次简报快照(willow_agent_runs + willow_stock_calls)
- 算出「较上次的变化」(哪只票的结论从 A 变成 B)
- 用户规则(单票上限%/科技上限%/冷却小时/定投额),带默认值、可改
- 推送冷却:内容没变且在冷却期内则跳过,避免重复轰炸
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from db.client import get_conn

DEFAULT_RULES: dict[str, Any] = {
    "max_single_name_pct": 25.0,   # 单票占比超过则提醒
    "max_tech_pct": 55.0,          # 科技/AI 敞口超过则提醒
    "cooldown_hours": 12.0,        # 推送冷却(内容没变则跳过)
    "dca_qqq_usd": 100.0,
    "dca_voo_usd": 50.0,
    "beginner_mode": 1,
}


def get_rules() -> dict[str, Any]:
    rules = dict(DEFAULT_RULES)
    try:
        conn = get_conn(read_only=True)
        try:
            for r in conn.execute("SELECT key, value FROM willow_user_rules").fetchall():
                k = str(r["key"])
                raw = r["value"]
                if k in DEFAULT_RULES and isinstance(DEFAULT_RULES[k], float):
                    try:
                        rules[k] = float(raw)
                    except (TypeError, ValueError):
                        pass
                elif k in DEFAULT_RULES and isinstance(DEFAULT_RULES[k], int):
                    try:
                        rules[k] = int(float(raw))
                    except (TypeError, ValueError):
                        pass
                else:
                    rules[k] = raw
        finally:
            conn.close()
    except Exception:
        pass
    return rules


def set_rule(key: str, value: Any) -> None:
    conn = get_conn(read_only=False)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO willow_user_rules (key, value, updated_at) VALUES (?,?,datetime('now'))",
            (str(key), str(value)),
        )
        conn.commit()
    finally:
        conn.close()


def last_call_map() -> dict[str, str]:
    """最近一次快照里每只票的动作 {ticker: action}。"""
    conn = get_conn(read_only=True)
    try:
        row = conn.execute("SELECT id FROM willow_agent_runs ORDER BY run_at DESC LIMIT 1").fetchone()
        if not row:
            return {}
        rid = row["id"]
        calls = conn.execute(
            "SELECT ticker, action FROM willow_stock_calls WHERE run_id = ?", (rid,)
        ).fetchall()
        return {str(c["ticker"]): str(c["action"]) for c in calls}
    except Exception:
        return {}
    finally:
        conn.close()


def diff_calls(stocks: list[dict[str, Any]]) -> list[dict[str, str]]:
    """当前每只票动作 vs 最近快照,返回有变化的 [{ticker, from, to}]。"""
    prev = last_call_map()
    if not prev:
        return []
    changes: list[dict[str, str]] = []
    for s in stocks:
        tk = s.get("ticker")
        now = s.get("action_zh") or s.get("action")
        old = prev.get(tk)
        if old and now and old != now:
            changes.append({"ticker": tk, "from": old, "to": now})
    return changes


def save_snapshot(advice: dict[str, Any], *, trigger: str = "manual") -> int:
    """把本次简报存为一次快照。返回 run_id。"""
    conn = get_conn(read_only=False)
    try:
        p = advice.get("portfolio", {})
        cur = conn.execute(
            """INSERT INTO willow_agent_runs (headline, portfolio_action, risk_level, trigger, advice_json)
               VALUES (?,?,?,?,?)""",
            (advice.get("headline_zh", ""), p.get("action", ""), p.get("risk_level", ""),
             trigger, json.dumps(advice, ensure_ascii=False, default=str)),
        )
        rid = int(cur.lastrowid)
        for s in advice.get("stocks", []):
            conn.execute(
                "INSERT INTO willow_stock_calls (run_id, ticker, action, chg_pct) VALUES (?,?,?,?)",
                (rid, s.get("ticker"), s.get("action_zh") or s.get("action"), s.get("chg_pct")),
            )
        conn.commit()
        return rid
    finally:
        conn.close()


def recent_runs(limit: int = 10) -> list[dict[str, Any]]:
    conn = get_conn(read_only=True)
    try:
        rows = conn.execute(
            "SELECT run_at, headline, portfolio_action, risk_level, trigger FROM willow_agent_runs ORDER BY run_at DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def _last_digest_signature() -> tuple[str, str] | None:
    """返回最近一次 'digest' 触发快照的 (signature, run_at)。"""
    conn = get_conn(read_only=True)
    try:
        row = conn.execute(
            "SELECT headline, portfolio_action, run_at FROM willow_agent_runs WHERE trigger='digest' ORDER BY run_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        sig = f"{row['headline']}|{row['portfolio_action']}"
        return sig, str(row["run_at"])
    except Exception:
        return None
    finally:
        conn.close()


def should_push_digest(advice: dict[str, Any], *, cooldown_hours: float | None = None) -> bool:
    """内容(headline+组合动作)较上次推送没变、且在冷却期内 → 不推。"""
    if cooldown_hours is None:
        cooldown_hours = float(get_rules().get("cooldown_hours", 12.0))
    last = _last_digest_signature()
    if not last:
        return True
    sig, run_at = last
    cur_sig = f"{advice.get('headline_zh','')}|{advice.get('portfolio',{}).get('action','')}"
    if cur_sig != sig:
        return True
    try:
        last_dt = datetime.fromisoformat(run_at.replace("Z", ""))
        return datetime.utcnow() - last_dt >= timedelta(hours=cooldown_hours)
    except Exception:
        return True
