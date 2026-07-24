"""Generate market storylines from real news."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from news_pipeline.storage import init_news_schema

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是给散户写"3 分钟看懂今日市场"的金融编辑。
任务是从一批真实新闻里提炼出当天的"3 大市场主线"。

什么是"市场叙事主线"？
好的主线："AI 数据中心电力链强势延续"——具体、有逻辑、跨多只股票。
好的主线："半导体财报季验证 AI 需求未见顶"——事件/趋势驱动多只股票同向。
好的主线："美联储讲话压制成长股估值"——宏观信号 → 板块反应。

风格参考：Finimize / Robinhood Snacks。
- 像跟朋友聊天，不要术语堆叠。
- 先说事实，再说原因，再说对用户的钱有什么影响。
- 必须给具体行动建议，常见答案可以是"不操作"。

禁止输出：
- "软件/平台"、"科技股"、"AI 相关" 这种 sector 分类。
- "AVGO 中性 / META 中性 / MSFT 中性" 这种重复堆叠。
- 新闻不够时硬凑 3 条。
- "投资者信心"、"信心增强"、"乐观情绪"、"对未来发展乐观"、"可能影响"、"持续关注"、"整体趋势" 这种 ChatGPT 式废话。
- "您的整体持仓将上涨"、"可能出现波动" 这种没有操作价值的话。

输出严格 JSON 数组。每条主线必须包含：
title, narrative, representative_tickers, tickers_with_changes,
related_news_ids, impact_on_user_holdings, early_movers, intensity。

其中 narrative 必须包含两段，保留段落标题：
📍 发生了什么：1-2 句具体事实，只能来自输入新闻。
📍 为什么重要：1-2 句解释交易逻辑。

impact_on_user_holdings 必须包含两段，保留段落标题：
📍 对你的钱：针对用户持仓写具体影响。
📍 行动建议：一句具体动作，允许写"不操作"、"只设提醒"、"等回调"。

早期机会 early_movers：
- 必须是真"还没动"的票：同逻辑、涨幅绝对值 < 2%。
- QQQ/SPY/VOO 这类指数不算早期机会。
- 用户已持仓的票不算早期机会。
- 找不到就用 []，不要硬凑。
"""

FEW_SHOT_EXAMPLE = """示例输出：
[
  {
    "title": "AI 数据中心电力链强势延续",
    "narrative": "📍 发生了什么：VST/CEG 拿到数据中心电力相关订单，OKLO 也有政策支持。\n📍 为什么重要：AI 训练吃电这条链没降速，资金开始从芯片扩散到电力和基础设施。",
    "representative_tickers": ["VST", "CEG", "OKLO", "TLN"],
    "tickers_with_changes": [{"ticker": "VST", "change_pct": 3.0}],
    "related_news_ids": ["news_1", "news_2"],
    "impact_on_user_holdings": "📍 对你的钱：若你持有相关标的（如示例 XYZ）直接受益于数据中心电力主线；NVDA/AVGO 间接受益于算力链景气。\n📍 行动建议：不追高。若已涨多，优先设止盈/回撤提醒。",
    "early_movers": [{"ticker": "NRG", "change_pct": 0.5, "logic": "同板块电力但还没启动"}],
    "intensity": 9
  }
]"""

STORYLINE_SCHEMA = """
CREATE TABLE IF NOT EXISTS storylines (
    id TEXT PRIMARY KEY,
    date TEXT NOT NULL,
    rank INTEGER NOT NULL,
    title TEXT NOT NULL,
    narrative TEXT,
    representative_tickers TEXT,
    tickers_with_changes TEXT,
    related_news_ids TEXT,
    impact_on_user_holdings TEXT,
    early_movers TEXT,
    intensity INTEGER,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_storyline_date ON storylines (date DESC, rank ASC);
"""


def _coerce_intensity(value: object) -> int:
    if value is None:
        return 5
    if isinstance(value, (int, float)):
        return max(1, min(10, int(value)))
    raw = str(value).strip().lower()
    zh_map = {"高": 8, "较高": 7, "中高": 7, "中": 5, "中等": 5, "低": 3, "较低": 2}
    if raw in zh_map:
        return zh_map[raw]
    en_map = {"high": 8, "medium": 5, "mid": 5, "low": 3}
    if raw in en_map:
        return en_map[raw]
    try:
        return max(1, min(10, int(float(raw))))
    except (TypeError, ValueError):
        log.warning("storyline intensity not numeric: %r; using 5", value)
        return 5


def init_storyline_schema(db_path: str | Path) -> None:
    init_news_schema(db_path)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(STORYLINE_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def _build_user_prompt(news_items: list[dict], user_holdings: list[str]) -> str:
    holdings = ", ".join(user_holdings)
    lines = []
    for item in news_items:
        ticker = f"[{item.get('primary_ticker')}]" if item.get("primary_ticker") else "[宏观]"
        t = str(item.get("published_at") or "")[:16].replace("T", " ")
        one_line = str(item.get("one_line_zh") or "").strip()
        summary = str(item.get("summary") or "").replace("\n", " ")[:220]
        extra = f" | 人话:{one_line}" if one_line and one_line != str(item.get("title") or "") else ""
        if summary:
            extra += f" | 摘要:{summary}"
        lines.append(f"- id={item['id']} {t} {ticker} {item['title']}{extra}")
    return f"""用户持仓：{holdings}

过去 24 小时新闻（按重要性已排序，top {len(news_items)} 条）：
{chr(10).join(lines)}

请提炼今日 3 大主线（新闻不足可输出 1-2 条），输出严格 JSON 数组。
impact_on_user_holdings 必须针对上述持仓写具体影响和行动建议，绝不重复堆叠或写"中性"模板。"""


def generate_storylines(
    news_items: list[dict],
    user_holdings: list[str],
    api_key: str | None = None,
) -> list[dict] | None:
    api_key = api_key or os.environ.get("GEMINI_API_KEY")
    if len(news_items) < 5:
        log.warning("only %s news items, too few for storyline", len(news_items))
        return None
    parsed: object | None = None
    if api_key:
        try:
            import google.generativeai as genai

            genai.configure(api_key=api_key)
            model = genai.GenerativeModel(
                model_name=os.environ.get("GEMINI_STORYLINE_MODEL", "gemini-2.0-flash"),
                system_instruction=f"{SYSTEM_PROMPT}\n\n{FEW_SHOT_EXAMPLE}",
                generation_config={
                    "response_mime_type": "application/json",
                    "temperature": 0.3,
                    "max_output_tokens": 4096,
                },
            )
            timeout = float(os.environ.get("GEMINI_TIMEOUT_SECONDS", "30"))
            response = model.generate_content(
                _build_user_prompt(news_items, user_holdings),
                request_options={"timeout": timeout},
            )
            text = str(response.text or "").strip()
            if text.startswith("```"):
                text = text.split("```", 2)[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()
            parsed = json.loads(text)
        except Exception as exc:
            if "429" in str(exc) or "quota" in str(exc).lower():
                log.warning("Gemini storyline quota exhausted; using Groq fallback.")
            else:
                log.exception("Gemini storyline generation failed: %s", exc)
    if parsed is None and os.environ.get("GROQ_API_KEY"):
        try:
            from groq import Groq

            client = Groq(api_key=os.environ["GROQ_API_KEY"])
            resp = client.chat.completions.create(
                model=os.environ.get("GROQ_STORYLINE_MODEL", "llama-3.3-70b-versatile"),
                messages=[
                    {
                        "role": "system",
                        "content": f"{SYSTEM_PROMPT}\n\n{FEW_SHOT_EXAMPLE}\n输出 JSON 对象，格式为 {{\"items\":[...]}}。",
                    },
                    {"role": "user", "content": _build_user_prompt(news_items, user_holdings)},
                ],
                temperature=0.3,
                max_tokens=4096,
                response_format={"type": "json_object"},
            )
            parsed = json.loads(str(resp.choices[0].message.content or "{}"))
        except Exception as exc:
            log.exception("Groq storyline generation failed: %s", exc)
    if parsed is None:
        return None
    if isinstance(parsed, dict):
        for key in ("items", "storylines", "results"):
            if isinstance(parsed.get(key), list):
                parsed = parsed[key]
                break
    if not isinstance(parsed, list):
        return None
    valid: list[dict] = []
    held = {s.upper() for s in user_holdings}
    bad_early = {"SPY", "QQQ", "VOO", "VTI", "IVV", "QQQM"}
    bad_phrases = ("投资者信心", "信心增强", "乐观情绪", "未来发展前景", "可能会", "持续关注市场")
    for item in parsed:
        if not isinstance(item, dict) or not item.get("title") or not item.get("narrative"):
            continue
        blob = f"{item.get('title') or ''} {item.get('narrative') or ''} {item.get('impact_on_user_holdings') or ''}"
        if any(p in blob for p in bad_phrases):
            log.warning("drop generic storyline: %s", item.get("title"))
            continue
        impact = str(item.get("impact_on_user_holdings") or "")
        if impact.count("中性") >= 3:
            item["impact_on_user_holdings"] = "AI 输出出现模板化中性堆叠，已屏蔽；请重新生成主线。"
        early = []
        for mover in item.get("early_movers") or []:
            if not isinstance(mover, dict):
                continue
            ticker = str(mover.get("ticker") or "").upper().strip()
            try:
                chg = abs(float(mover.get("change_pct") or 0.0))
            except (TypeError, ValueError):
                chg = 999.0
            if not ticker or ticker in held or ticker in bad_early or chg >= 2.0:
                continue
            mover["ticker"] = ticker
            early.append(mover)
        item["early_movers"] = early
        valid.append(item)
    return valid[:3]


def get_top_news_for_storyline(db_path: str | Path, hours: int = 24, limit: int = 30) -> list[dict]:
    init_storyline_schema(db_path)
    cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT id, title, summary, primary_ticker, affected_tickers,
                      published_at, source,
                      COALESCE(severity, 'pending') AS severity,
                      COALESCE(category, 'pending') AS category,
                      COALESCE(one_line_zh, title) AS one_line_zh
               FROM news
               WHERE published_at >= ?
               ORDER BY
                 CASE severity
                   WHEN 'urgent' THEN 1
                   WHEN 'important' THEN 2
                   WHEN 'major' THEN 2
                   WHEN 'attention' THEN 3
                   WHEN NULL THEN 4
                   ELSE 4
                 END,
                 published_at DESC
               LIMIT ?""",
            (cutoff, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def save_storylines(db_path: str | Path, storylines: list[dict]) -> None:
    init_storyline_schema(db_path)
    today = datetime.now().date().isoformat()
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("DELETE FROM storylines WHERE date = ?", (today,))
        for i, item in enumerate(storylines, 1):
            conn.execute(
                """INSERT INTO storylines (
                      id, date, rank, title, narrative, representative_tickers,
                      tickers_with_changes, related_news_ids, impact_on_user_holdings,
                      early_movers, intensity
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    f"{today}-{i}",
                    today,
                    i,
                    str(item.get("title") or ""),
                    str(item.get("narrative") or ""),
                    json.dumps(item.get("representative_tickers") or []),
                    json.dumps(item.get("tickers_with_changes") or []),
                    json.dumps(item.get("related_news_ids") or []),
                    str(item.get("impact_on_user_holdings") or ""),
                    json.dumps(item.get("early_movers") or []),
                    _coerce_intensity(item.get("intensity")),
                ),
            )
        conn.commit()
    finally:
        conn.close()


def get_today_storylines(db_path: str | Path) -> list[dict]:
    init_storyline_schema(db_path)
    today = datetime.now().date().isoformat()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM storylines WHERE date = ? ORDER BY rank ASC",
            (today,),
        ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            for field in ("representative_tickers", "tickers_with_changes", "related_news_ids", "early_movers"):
                try:
                    item[field] = json.loads(item.get(field) or "[]")
                except Exception:
                    item[field] = []
            out.append(item)
        return out
    finally:
        conn.close()


def run_storyline_pipeline(db_path: str | Path, user_holdings: list[str]) -> dict:
    news_items = get_top_news_for_storyline(db_path)
    if len(news_items) < 5:
        return {"status": "failed", "count": 0, "reason": f"only {len(news_items)} news items, too few"}
    storylines = generate_storylines(news_items, user_holdings)
    if storylines is None:
        return {"status": "failed", "count": 0, "reason": "LLM call failed"}
    save_storylines(db_path, storylines)
    return {"status": "ok", "count": len(storylines)}


if __name__ == "__main__":
    import sys

    from data_layer.universe import load_watchlist_tickers
    from db.client import default_db_path

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    db = Path(sys.argv[1]) if len(sys.argv) > 1 else default_db_path()
    result = run_storyline_pipeline(db, load_watchlist_tickers())
    print(json.dumps(result, indent=2, ensure_ascii=False))
    for storyline in get_today_storylines(db):
        print(f"#{storyline['rank']} {storyline['title']} (intensity {storyline['intensity']})")
