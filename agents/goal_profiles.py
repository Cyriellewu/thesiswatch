"""按投资期限（horizon）调整发给 LLM 的 Top 信号排序与短文说明。

不改变风控与执行上限；不把 horizon 写成硬性到期——只影响侧重点。"""

from __future__ import annotations

import json

import yaml

from agents.context import AgentContextBundle

VALID_HORIZONS = frozenset({"2w", "1m", "3m", "6m", "1y"})
VALID_OBJECTIVES = frozenset({"maximize_return", "preserve_then_grow"})
VALID_RISK_MODES = frozenset({"risk_aware", "capital_first"})
DEFAULT_GOAL = {
    "horizon": "2w",
    "objective": "maximize_return",
    "risk_mode": "risk_aware",
}

WEIGHT_PRESETS: dict[str, dict[str, float]] = {
    "2w": {
        "news_catalyst": 0.35,
        "momentum": 0.30,
        "macro_risk": 0.20,
        "fundamental_quality": 0.10,
        "smart_money": 0.05,
        "trend": 0.00,
    },
    "1m": {
        "news_catalyst": 0.27,
        "momentum": 0.23,
        "macro_risk": 0.20,
        "trend": 0.15,
        "fundamental_quality": 0.10,
        "smart_money": 0.05,
    },
    "3m": {
        "trend": 0.23,
        "fundamental_quality": 0.20,
        "news_catalyst": 0.17,
        "macro_risk": 0.18,
        "momentum": 0.12,
        "smart_money": 0.10,
    },
    "6m": {
        "fundamental_quality": 0.35,
        "macro_risk": 0.25,
        "trend": 0.20,
        "smart_money": 0.15,
        "news_catalyst": 0.05,
        "momentum": 0.00,
    },
    "1y": {
        "fundamental_quality": 0.37,
        "macro_risk": 0.24,
        "trend": 0.18,
        "smart_money": 0.16,
        "news_catalyst": 0.05,
        "momentum": 0.00,
    },
}


def effective_signal_weights(goal: dict[str, str]) -> dict[str, float]:
    """Horizon 基础权重 + objective/risk_mode 小幅平移后再归一，用于排序不改变 LLM 条数。"""
    hz = goal.get("horizon") or "2w"
    if hz not in VALID_HORIZONS:
        hz = "2w"
    w = dict(WEIGHT_PRESETS[hz])
    obj = goal.get("objective") or DEFAULT_GOAL["objective"]
    rm = goal.get("risk_mode") or DEFAULT_GOAL["risk_mode"]

    if obj == "preserve_then_grow":
        w["fundamental_quality"] = w.get("fundamental_quality", 0.0) + 0.08
        w["macro_risk"] = w.get("macro_risk", 0.0) + 0.04
        w["momentum"] = max(0.0, w.get("momentum", 0.0) - 0.08)
        w["news_catalyst"] = max(0.0, w.get("news_catalyst", 0.0) - 0.04)

    if rm == "capital_first":
        w["macro_risk"] = w.get("macro_risk", 0.0) + 0.10
        w["fundamental_quality"] = w.get("fundamental_quality", 0.0) + 0.03
        w["momentum"] = max(0.0, w.get("momentum", 0.0) - 0.06)
        w["news_catalyst"] = max(0.0, w.get("news_catalyst", 0.0) - 0.07)

    tot = sum(w.values())
    if tot <= 0:
        return dict(WEIGHT_PRESETS["2w"])
    return {k: float(v) / tot for k, v in w.items()}


def goal_weights_effective_summary(goal: dict[str, str]) -> dict[str, float]:
    return effective_signal_weights(goal)


def horizon_weights_summary(horizon: str) -> dict[str, float]:
    """仅用期限的基础预设（不含 objective/risk 平移）；供调试对比。"""
    return dict(WEIGHT_PRESETS.get(horizon, WEIGHT_PRESETS["2w"]))


DASH_SUMMARY_ZH: dict[str, str] = {
    "2w": "当前期限（约两周）：在既定风控下争取收益；更关注突发催化与盘面动量。",
    "1m": "当前期限（约一个月）：Swing 取向；更重趋势能否多拿几周，少跟一日游噪音。",
    "3m": "当前期限（约三个月）：更重行业与中期趋势的延展，抑制纯情绪短线。",
    "6m": "当前期限（约半年）：更重基本面与估值质量；不追高波动情绪票。",
    "1y": "当前期限（约一年）：偏配置视角；更重护城河、盈利质量与回撤纪律。",
}

OBJECTIVE_LINE_ZH: dict[str, str] = {
    "maximize_return": "收益风格：在现金与仓位上限内积极把握机会。",
    "preserve_then_grow": "收益风格：**先稳后进**，少追的最后一涨。",
}

RISK_LINE_ZH: dict[str, str] = {
    "risk_aware": "风控：**常规觉知**坏消息要减速，但不盲目空仓。",
    "capital_first": "风控：**本金优先**，宏观极差时少走短线反抽。",
}


def parse_agent_goal(raw: str | bytes | dict | None) -> dict[str, str]:
    d = dict(DEFAULT_GOAL)
    if not raw:
        return d
    try:
        if isinstance(raw, dict):
            jo = raw
        else:
            jo = json.loads(str(raw))
        if not isinstance(jo, dict):
            return d
        hz = str(jo.get("horizon") or d["horizon"]).strip().lower()
        if hz not in VALID_HORIZONS:
            hz = d["horizon"]
        d["horizon"] = hz
        obj = str(jo.get("objective") or d["objective"]).strip().lower().replace("-", "_")
        if obj not in VALID_OBJECTIVES:
            obj = d["objective"]
        d["objective"] = obj
        rm = str(jo.get("risk_mode") or d["risk_mode"]).strip().lower().replace("-", "_")
        if rm not in VALID_RISK_MODES:
            rm = d["risk_mode"]
        d["risk_mode"] = rm
        return d
    except Exception:
        return dict(DEFAULT_GOAL)


def dumps_agent_goal(goal: dict[str, str]) -> str:
    hz = goal.get("horizon") or "2w"
    if hz not in VALID_HORIZONS:
        hz = "2w"
    obj = goal.get("objective") or DEFAULT_GOAL["objective"]
    if obj not in VALID_OBJECTIVES:
        obj = DEFAULT_GOAL["objective"]
    rm = goal.get("risk_mode") or DEFAULT_GOAL["risk_mode"]
    if rm not in VALID_RISK_MODES:
        rm = DEFAULT_GOAL["risk_mode"]
    return json.dumps(
        {"horizon": hz, "objective": obj, "risk_mode": rm},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def dashboard_goal_line_zh(goal: dict[str, str]) -> str:
    hz_line = DASH_SUMMARY_ZH.get(goal["horizon"], DASH_SUMMARY_ZH["2w"])
    o = OBJECTIVE_LINE_ZH.get(goal.get("objective") or "", "")
    r = RISK_LINE_ZH.get(goal.get("risk_mode") or "", "")
    return " ".join(x for x in (hz_line, o, r) if x.strip())


def _facet_from_news_text(lower: str) -> dict[str, float]:
    f = {k: 0.0 for k in WEIGHT_PRESETS["2w"]}
    if any(
        x in lower
        for x in (
            "earnings",
            "eps",
            "guidance",
            "revenue",
            "warn",
            "downgrade",
            "upgrade",
            "analyst",
            "sec ",
            "bankrupt",
            "lawsuit",
            "investigation",
            "merger",
            "acqui",
            "fda",
            "clinical",
            "dividend",
            "buyback",
            "proxy",
        )
    ):
        f["news_catalyst"] = max(f["news_catalyst"], 0.92)
        f["fundamental_quality"] = max(f["fundamental_quality"], 0.35)

    if any(x in lower for x in ("%", "spike", "surge", "plunge", "soar", "crash", "squeeze")):
        f["momentum"] = max(f["momentum"], 0.72)
        f["trend"] = max(f["trend"], 0.28)

    if any(x in lower for x in ("treasury", "yield", "inflation", "fed ", "ecb", "vix ", "rates", "powell")):
        f["macro_risk"] = max(f["macro_risk"], 0.82)

    if any(x in lower for x in ("insider", "13f", "activist", "hedge ", "ownership", "stake ", "institution")):
        f["smart_money"] = max(f["smart_money"], 0.65)

    if any(x in lower for x in ("margin", "cash flow", "valuation", "pe ratio")):
        f["fundamental_quality"] = max(f["fundamental_quality"], 0.55)

    if any(x in lower for x in ("weeks ahead", "quarter outlook", "structural")):
        f["trend"] = max(f["trend"], 0.52)

    return f


def _facet_from_signal(signal_type: str, payload_repr: str) -> dict[str, float]:
    f = {k: 0.0 for k in WEIGHT_PRESETS["2w"]}
    low = payload_repr.lower()
    st = (signal_type or "").lower()

    if st == "daily_price_move" or "price" in st:
        f["momentum"] = 0.92
        f["trend"] = 0.35
        fx = _facet_from_news_text(low)
        for k in f:
            f[k] = max(f[k], fx[k])

    elif st == "company_headline" or "news" in st:
        fx = _facet_from_news_text(low)
        for k in f:
            f[k] = max(f[k], fx[k])
        f["news_catalyst"] = max(f["news_catalyst"], 0.78)

    elif st == "macro_strip":
        f["macro_risk"] = 0.95
        f["trend"] = max(f["trend"], 0.42)

    else:
        f["macro_risk"] = max(f["macro_risk"], 0.22)
        f["trend"] = max(f["trend"], 0.25)

    return f


def _alignment_score(weights: dict[str, float], facets: dict[str, float]) -> float:
    return float(sum(weights.get(k, 0.0) * min(1.0, max(0.0, facets[k])) for k in weights))


def _rank_boost(base_score: float, goal: dict[str, str], facets: dict[str, float]) -> float:
    w = effective_signal_weights(goal)
    align = _alignment_score(w, facets)
    return float(base_score) * (1.0 + (align - 0.35) * 0.72)


def pick_top_signals_for_prompt(
    bundle: AgentContextBundle,
    goal: dict[str, str],
    limit: int = 5,
) -> tuple[str, list[int], list[int]]:
    """按 goal（期限+风格）对候选条目重排；仍最多 ``limit`` 条给 LLM，token 不涨。"""
    g = parse_agent_goal(goal)

    lim = max(3, min(int(limit), 8))
    cand: list[tuple[float, str, str, list[int]]] = []

    for a in bundle.alerts[:8]:
        base_sc = {"urgent": 100.0, "major": 70.0, "attention": 40.0}.get(str(a.get("level")), 10.0)
        blob = (
            f"{a.get('title', '') or ''} {a.get('what_happened', '') or ''} "
            f"{a.get('why_matters', '') or ''}".lower()
        )
        facets = _facet_from_news_text(blob)
        sc = _rank_boost(base_sc, g, facets)
        aid = int(a["id"]) if isinstance(a.get("id"), int) else 0
        line = (
            f"提醒[{a.get('level')}] {a.get('symbol') or '-'}:"
            f" {a.get('title', '')[:140]}｜发生了什么:{str(a.get('what_happened', ''))[:140]}"
        )
        cand.append((sc, line, "al", [aid]))

    for s in bundle.signals[:12]:
        sid = int(s["id"]) if isinstance(s.get("id"), int) else 0
        pl = str(s.get("payload"))[:320]
        facets = _facet_from_signal(str(s.get("type") or ""), pl)
        base_sc = float(s.get("score") or 0) + (20 if sid else 0)
        sc = _rank_boost(base_sc, g, facets)
        line = f"信号[{s['type']}] {s['symbol'] or '-'} score={s['score']}::{str(s['payload'])[:240]}"
        cand.append((sc, line, "sig", [sid]))

    cand.sort(key=lambda x: -x[0])

    picks: list[str] = []
    aid_set: list[int] = []
    sid_set: list[int] = []
    for _, line, origin, lids in cand:
        if origin == "al" and lids:
            lid = lids[0]
            if lid and lid not in aid_set:
                aid_set.append(lid)
        elif origin == "sig" and lids:
            lid = lids[0]
            if lid and lid not in sid_set:
                sid_set.append(lid)
        picks.append(line)
        if len(picks) >= lim:
            break

    snippet = "\n".join(f"{i + 1}. {p}" for i, p in enumerate(picks[:lim]))
    return snippet, sid_set[:8], aid_set[:8]


def llm_horizon_instructions(goal: dict[str, str]) -> str:
    gg = parse_agent_goal(goal)
    hz = gg["horizon"]
    obj = gg["objective"]
    rm = gg["risk_mode"]
    hz_en = {"2w": "2 weeks", "1m": "1 month", "3m": "3 months", "6m": "6 months", "1y": "1 year"}.get(hz, "2 weeks")

    if hz in ("6m", "1y"):
        mid = (
            f"Investment horizon ≈ {hz_en}. Aim to grow **over this horizon**, favoring quality, "
            "cash-flow durability, and trend sustainability over one-day hype."
        )
    elif hz in ("1m", "3m"):
        mid = (
            f"Investment horizon ≈ {hz_en}. Aim to grow **over this horizon**, using catalysts/moves only "
            "if they can realistically extend for multiple weeks."
        )
    else:
        mid = (
            f"Investment horizon ≈ {hz_en}. Aim to grow **over this horizon**, weighting fresh catalysts "
            "and actionable momentum more, but still avoid reckless sizing."
        )

    if obj == "preserve_then_grow":
        mid += " Prefer smaller, higher-conviction steps; it's fine to pass marginal trades."

    rm_tail = ""
    if rm == "capital_first":
        rm_tail = (
            " Risk posture: capital preservation first—if macro/stress is ugly, default to HOLD unless the "
            "edge is exceptional; skip low-quality rebound punts."
        )
    else:
        rm_tail = (
            " Risk posture: risk-aware—slow down when headlines/macro deteriorate; obey hard cash/position limits; "
            "this is not gambling."
        )

    return mid + rm_tail + " Stated min-cash / per-name caps / allowed symbols always win over stories."


def horizons_for_selector() -> list[tuple[str, str]]:
    return [
        ("约 2 周（催化+动量）", "2w"),
        ("约 1 个月（Swing）", "1m"),
        ("约 3 个月（趋势/行业）", "3m"),
        ("约 6 个月（质量）", "6m"),
        ("约 1 年（偏配置）", "1y"),
    ]


def objectives_for_selector() -> list[tuple[str, str]]:
    return [
        ("积极收益（仍守硬规则）", "maximize_return"),
        ("先稳后进", "preserve_then_grow"),
    ]


def risk_modes_for_selector() -> list[tuple[str, str]]:
    return [
        ("风险觉知（常规）", "risk_aware"),
        ("本金优先（更保守）", "capital_first"),
    ]


def yaml_goal_defaults() -> dict[str, str]:
    """`config/settings.yaml` → `agent_goal_defaults`；读失败则用 DEFAULT_GOAL。"""
    base = dict(DEFAULT_GOAL)
    try:
        from db.client import repo_root

        txt = (repo_root() / "config" / "settings.yaml").read_text(encoding="utf-8")
        cfg = yaml.safe_load(txt) or {}
        blk = cfg.get("agent_goal_defaults") if isinstance(cfg.get("agent_goal_defaults"), dict) else {}
        merged = {**base, **{k: blk[k] for k in ("horizon", "objective", "risk_mode") if k in blk}}
        return parse_agent_goal(merged)
    except Exception:
        return base
