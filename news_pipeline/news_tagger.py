"""News classification and plain-language labels."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:  # noqa: SIM105
    from dotenv import load_dotenv  # noqa: E402

    load_dotenv(ROOT / ".env", override=False)
except Exception:
    pass

log = logging.getLogger(__name__)
_LAST_ERROR = ""
_GEMINI_DISABLED_FOR_PROCESS = False

SYSTEM_PROMPT = """你是新闻分类助手。任务：给一批美股相关新闻打标签 + 写大白话总结。

输入是 N 条新闻（每条带 id、标题、摘要、提到的股票）和用户持仓。
输出是 N 个 JSON 对象组成的数组（顺序对应输入）。

每条必须输出：
{
  "id": "原 id",
  "category": "earnings|rating|macro|policy|merger|product|legal|management|industry|other",
  "severity": "urgent|important|attention|routine",
  "one_line_zh": "大白话一句话，≤30字",
  "impact_on_holdings": "对持仓具体影响，≤50字。无影响则填 null",
  "market_stance": "bullish|bearish|noise|neutral",
  "what_it_means_zh": "金融直觉一句话，≤45字，像专家跟朋友解释「这意味着涨还是跌」"
}

market_stance 标准（金融直觉）：
- bullish: 真趋势、真利好，可能推动股价或估值上修（如 Meta 进军云计算对标 AWS）
- bearish: 真利空，可能伤及盈利核心或系统性风险
- noise: 散户容易恐慌的「假利空」或重复旧闻（如巨头常规诉讼、营销稿、无新信息的评级微调）
- neutral: 有影响但方向不明，或对用户持仓无实质定价影响

what_it_means_zh 要求：
- 必须给出方向判断，不要模棱两可
- 像推送通知：「Meta 进军云计算对标 AWS，估值模型重构，利好。」
- 若是 noise：说明为何不用慌，如「儿童隐私诉讼是巨头常客，不伤盈利核心，可忽略」
- 禁止「需观察」「影响有限」「中性中性」等空话

severity 标准：
- urgent: 持仓股 ±5% 异动 / 财报炸雷 / 系统性风险 / 紧急政策
- important: 持仓股评级大调 / 持仓股财报 / 行业重大事件 / 美联储讲话或数据
- attention: 持仓相关行业新闻 / 同板块异动 / 一般评级变动 / 关注列表股票动态
- routine: 一般性新闻、不影响投资决策的内容、营销稿

one_line_zh 要像跟朋友聊天，不要术语，必须包含发生了什么和为什么重要，保留关键数字。
风格示例：
✅ "IREN 抱上 NVDA，大型数据中心合作到手"
✅ "Vistra 财报又赢了，电力需求继续被 AI 撑着"
✅ "微软和亚马逊财报后被拿来对比，市场更看云业务"
❌ "某公司发布最新业务动态"
❌ "投资者关注该公司表现"
one_line_zh 只能压缩原始标题/摘要里的事实，不能新增标题/摘要没有说的股票、公司或结论。
impact_on_holdings 只说影响，不给买卖建议。只有标题/摘要明确提到用户持仓 ticker 或公司名，才可写直接持仓影响；否则不要硬扯单股。
对 macro/policy 新闻，impact_on_holdings 不能填 null，也不能写"无直接影响"；必须解释组合层面影响，例如：
- 美联储/利率偏鹰 → "你科技仓较重，短期估值承压；不等于基本面变坏。"
- 油价上涨 → "能源仓位很低，可能错过能源弹性；高油价也会压成长股估值。"
- 美元走强 → "大科技海外收入换汇承压，影响偏估值层面。"
输入里的 "抓取关联" 只是弱元数据，可能来自按 ticker 抓取的来源，不等于新闻真的提到该股票。不要因为抓取关联里有 NVDA/MSFT/GOOGL 就硬写这些持仓受益。
绝对禁止 "AVGO 中性 / AVGO 中性 / AVGO 中性" 这种重复堆叠。
绝对禁止 "无直接持仓影响" / "影响有限" 这种没信息量的句子。
输出严格 JSON 数组，不要 markdown。"""

VALID_SEVERITIES = {"urgent", "important", "attention", "routine"}
VALID_CATEGORIES = {
    "earnings",
    "rating",
    "macro",
    "policy",
    "merger",
    "product",
    "legal",
    "management",
    "industry",
    "other",
}
VALID_STANCES = {"bullish", "bearish", "noise", "neutral"}


def _build_user_prompt(news_batch: list[dict], holdings: list[str]) -> str:
    lines = [f"用户持仓:{', '.join(holdings)}", "", "新闻列表:"]
    for item in news_batch:
        affected = item.get("affected_tickers")
        if isinstance(affected, str):
            try:
                affected = json.loads(affected)
            except Exception:
                affected = []
        affected_str = ",".join(map(str, affected)) if affected else "无"
        title = str(item.get("title") or "").replace("\n", " ")[:200]
        summary = str(item.get("summary") or "").replace("\n", " ")[:300]
        line = f"id={item['id']} | 抓取关联(弱证据):{affected_str} | 标题:{title}"
        if summary:
            line += f" | 摘要:{summary}"
        lines.append(line)
    lines.append("")
    lines.append(f"请按顺序输出 {len(news_batch)} 个 JSON 对象组成的数组。")
    return "\n".join(lines)


def _call_gemini(prompt_user: str, api_key: str) -> str | None:
    global _GEMINI_DISABLED_FOR_PROCESS, _LAST_ERROR
    if _GEMINI_DISABLED_FOR_PROCESS:
        return None
    try:
        import google.generativeai as genai

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(
            model_name=os.environ.get("GEMINI_TAGGER_MODEL", "gemini-2.0-flash-lite"),
            system_instruction=SYSTEM_PROMPT,
            generation_config={
                "response_mime_type": "application/json",
                "temperature": 0.2,
                "max_output_tokens": 4096,
            },
        )
        timeout = float(os.environ.get("GEMINI_TIMEOUT_SECONDS", "30"))
        resp = model.generate_content(prompt_user, request_options={"timeout": timeout})
        return str(resp.text or "")
    except ImportError:
        _LAST_ERROR = "google-generativeai not installed"
        log.error(_LAST_ERROR)
        return None
    except Exception as exc:
        _LAST_ERROR = f"Gemini call failed: {exc}"
        if "429" in str(exc) or "quota" in str(exc).lower():
            _GEMINI_DISABLED_FOR_PROCESS = True
            log.warning("Gemini quota exhausted; using fallback for the rest of this process.")
        else:
            log.exception("Gemini call failed: %s", exc)
        return None


def _call_groq(prompt_user: str, api_key: str) -> str | None:
    global _LAST_ERROR
    try:
        from groq import Groq

        client = Groq(api_key=api_key)
        resp = client.chat.completions.create(
            model=os.environ.get("GROQ_TAGGER_MODEL", "llama-3.3-70b-versatile"),
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT + "\n输出 JSON 对象，格式为 {\"items\":[...]}。"},
                {"role": "user", "content": prompt_user},
            ],
            temperature=0.2,
            max_tokens=4096,
            response_format={"type": "json_object"},
        )
        return str(resp.choices[0].message.content or "")
    except ImportError:
        _LAST_ERROR = "groq package not installed"
        log.error(_LAST_ERROR)
        return None
    except Exception as exc:
        _LAST_ERROR = f"Groq call failed: {exc}"
        log.exception("Groq call failed: %s", exc)
        return None


def _call_llm(prompt_user: str, api_key: str | None = None) -> str | None:
    """Gemini first, Groq fallback."""

    gemini_key = api_key or os.environ.get("GEMINI_API_KEY")
    if gemini_key:
        raw = _call_gemini(prompt_user, gemini_key)
        if raw:
            return raw
    groq_key = os.environ.get("GROQ_API_KEY")
    if groq_key:
        return _call_groq(prompt_user, groq_key)
    global _LAST_ERROR
    if not _LAST_ERROR:
        _LAST_ERROR = "No LLM credentials available for news tagging"
    return None


def _parse_response(raw_text: str) -> list[dict] | None:
    global _LAST_ERROR
    text = str(raw_text or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
        if text.endswith("```"):
            text = text[:-3].strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        _LAST_ERROR = f"JSON parse failed: {exc}"
        log.error("JSON parse failed: %s, raw: %s", exc, text[:300])
        return None
    if isinstance(data, dict):
        for key in ("items", "news", "tags", "results"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
    if not isinstance(data, list):
        _LAST_ERROR = f"response not list: {type(data).__name__}"
        return None
    return data


def _validate_tag(tag: dict) -> bool:
    if not isinstance(tag, dict) or not tag.get("id") or not tag.get("one_line_zh"):
        return False
    if tag.get("severity") not in VALID_SEVERITIES:
        return False
    if tag.get("category") not in VALID_CATEGORIES:
        tag["category"] = "other"
    stance = str(tag.get("market_stance") or "neutral").strip().lower()
    if stance not in VALID_STANCES:
        stance = "neutral"
    tag["market_stance"] = stance
    witm = str(tag.get("what_it_means_zh") or tag.get("one_line_zh") or "").strip()
    tag["what_it_means_zh"] = witm[:80] if witm else tag.get("one_line_zh", "")
    impact = tag.get("impact_on_holdings") or ""
    if isinstance(impact, str) and impact.count("中性") >= 3:
        log.warning("tag %s has 中性 stacking, nulling impact", tag.get("id"))
        tag["impact_on_holdings"] = None
    return True


def tag_news_batch(
    news_batch: list[dict],
    holdings: list[str],
    api_key: str | None = None,
) -> dict[str, dict] | None:
    api_key = api_key or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        global _LAST_ERROR
        _LAST_ERROR = "GEMINI_API_KEY not set"
        log.error(_LAST_ERROR)
        return None
    if not news_batch:
        return {}
    raw = _call_llm(_build_user_prompt(news_batch, holdings), api_key)
    if not raw:
        return None
    parsed = _parse_response(raw)
    if parsed is None:
        return None
    expected_ids = {str(item["id"]) for item in news_batch}
    out: dict[str, dict] = {}
    for tag in parsed:
        if not _validate_tag(tag):
            continue
        nid = str(tag["id"])
        if nid in expected_ids:
            out[nid] = tag
    log.info("tagged %s/%s news", len(out), len(news_batch))
    return out


def get_pending_news(db_path: str | Path, limit: int = 50) -> list[dict]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT id, title, summary, primary_ticker, affected_tickers, source, published_at
               FROM news
               WHERE category IS NULL
               ORDER BY published_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def save_tags(db_path: str | Path, tags: dict[str, dict]) -> int:
    if not tags:
        return 0
    conn = sqlite3.connect(str(db_path))
    updated = 0
    try:
        for news_id, tag in tags.items():
            conn.execute(
                """UPDATE news
                   SET category = ?, severity = ?, one_line_zh = ?, impact_on_holdings = ?,
                       market_stance = ?, what_it_means_zh = ?
                   WHERE id = ?""",
                (
                    tag.get("category"),
                    tag.get("severity"),
                    tag.get("one_line_zh"),
                    tag.get("impact_on_holdings"),
                    tag.get("market_stance"),
                    tag.get("what_it_means_zh"),
                    news_id,
                ),
            )
            updated += 1
        conn.commit()
    finally:
        conn.close()
    log.info("saved tags for %s news", updated)
    return updated


def run_news_tagging(
    db_path: str | Path,
    holdings: list[str],
    batch_size: int = 8,
    max_batches: int = 10,
) -> dict:
    pending = get_pending_news(db_path, limit=batch_size * max_batches)
    if not pending:
        return {"pending": 0, "tagged": 0, "batches": 0, "errors": 0}
    total_tagged = 0
    errors = 0
    batches_run = 0
    last_error = ""
    for i in range(0, len(pending), batch_size):
        batch = pending[i : i + batch_size]
        batches_run += 1
        tags = tag_news_batch(batch, holdings)
        if tags is None:
            errors += 1
            last_error = _LAST_ERROR or "unknown"
            log.warning("batch %s failed, leaving rows pending", batches_run)
            time.sleep(2)
            continue
        total_tagged += save_tags(db_path, tags)
        time.sleep(float(os.environ.get("GEMINI_TAGGER_SLEEP_SECONDS", "4.5")))
    return {
        "pending": len(pending),
        "tagged": total_tagged,
        "batches": batches_run,
        "errors": errors,
        "last_error": last_error,
    }


if __name__ == "__main__":
    import sys

    from data_layer.universe import load_watchlist_tickers
    from db.client import default_db_path

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
    db = Path(sys.argv[1]) if len(sys.argv) > 1 else default_db_path()
    result = run_news_tagging(db, load_watchlist_tickers(), batch_size=8, max_batches=5)
    print(json.dumps(result, indent=2, ensure_ascii=False))
