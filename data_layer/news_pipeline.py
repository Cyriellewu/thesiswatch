from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import feedparser
import requests

from data_layer.finnhub_client import fetch_company_news, is_configured as finnhub_ok
from data_layer.universe import load_watchlist_tickers
from llm.router import TokenGuard

logger = logging.getLogger(__name__)

_REUTERS_BUSINESS_RSS = "https://feeds.reuters.com/reuters/businessNews"
_SEC_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
_SEC_TICKER_INDEX = "https://www.sec.gov/files/company_tickers.json"
_SEC_UA = "AlphaWatch/1.0 (contact: alphawatch-local-user@example.com)"
_SEC_CIK_BY_TICKER = {
    "AVGO": "1730168",
    "GOOGL": "1652044",
    "IREN": "1843586",
    "META": "1326801",
    "MSFT": "0789019",
    "NVDA": "1045810",
    "PANW": "1327567",
    "TSLA": "1318605",
}
_SEC_CACHE_FILE = Path(__file__).resolve().parents[1] / "data" / "cache" / "sec_company_tickers.json"

try:
    from rapidfuzz import fuzz

    def _title_ratio(a: str, b: str) -> float:
        return float(fuzz.token_sort_ratio(a, b))

except Exception:

    def _title_ratio(a: str, b: str) -> float:
        aa = set((a or "").lower().split())
        bb = set((b or "").lower().split())
        if not aa or not bb:
            return 0.0
        return 100.0 * len(aa & bb) / max(len(aa), len(bb))


def _iso_from_epoch(ts: Any) -> str:
    try:
        return datetime.fromtimestamp(int(float(ts)), tz=timezone.utc).isoformat()
    except Exception:
        return datetime.now(timezone.utc).isoformat()


def _as_utc_dt(value: datetime) -> datetime:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _norm_iso(ts: str | None) -> str:
    raw = (ts or "").strip()
    if not raw:
        return datetime.now(timezone.utc).isoformat()
    try:
        return _as_utc_dt(datetime.fromisoformat(raw.replace("Z", "+00:00"))).isoformat()
    except Exception:
        return datetime.now(timezone.utc).isoformat()


def _news_id(url: str, title: str, source: str) -> str:
    base = f"{url}|{title.strip().lower()}|{source}"
    return hashlib.sha1(base.encode("utf-8")).hexdigest()


def _extract_tickers(text: str, watchlist: set[str]) -> list[str]:
    up = (text or "").upper()
    return sorted([s for s in watchlist if s in up])[:8]


def _source_rank(source: str) -> int:
    key = (source or "").lower()
    if key == "reuters":
        return 4
    if key == "sec_edgar":
        return 3
    if key == "finnhub":
        return 2
    if key == "yfinance":
        return 1
    return 0


def _load_sec_ticker_map(session: requests.Session) -> dict[str, str]:
    # Cache SEC ticker index to avoid frequent requests and rate-limit issues.
    try:
        if _SEC_CACHE_FILE.exists():
            age = datetime.now(timezone.utc) - datetime.fromtimestamp(_SEC_CACHE_FILE.stat().st_mtime, tz=timezone.utc)
            if age < timedelta(hours=24):
                payload = json.loads(_SEC_CACHE_FILE.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    out: dict[str, str] = {}
                    for v in payload.values():
                        if not isinstance(v, dict):
                            continue
                        t = str(v.get("ticker") or "").upper().strip()
                        c = str(v.get("cik_str") or "").strip()
                        if t and c.isdigit():
                            out[t] = c
                    if out:
                        return out
    except Exception:
        logger.debug("sec ticker cache read failed", exc_info=True)

    try:
        r = session.get(_SEC_TICKER_INDEX, timeout=20)
        if not r.ok:
            return {}
        payload = r.json() or {}
        _SEC_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _SEC_CACHE_FILE.write_text(json.dumps(payload), encoding="utf-8")
        out: dict[str, str] = {}
        if isinstance(payload, dict):
            for v in payload.values():
                if not isinstance(v, dict):
                    continue
                t = str(v.get("ticker") or "").upper().strip()
                c = str(v.get("cik_str") or "").strip()
                if t and c.isdigit():
                    out[t] = c
        return out
    except Exception:
        logger.debug("sec ticker index fetch failed", exc_info=True)
        return {}


def _dedupe_and_cluster(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda x: x.get("published_at", ""), reverse=True):
        title = str(row.get("title") or "").strip()
        if not title:
            continue
        title_l = title.lower()
        pub = _norm_iso(str(row.get("published_at") or ""))
        t = _as_utc_dt(datetime.fromisoformat(pub.replace("Z", "+00:00")))
        row["published_at"] = pub
        row["cluster_id"] = row.get("cluster_id") or row["id"]

        duplicate_idx = -1
        for i, exist in enumerate(out):
            et = _as_utc_dt(datetime.fromisoformat(str(exist["published_at"]).replace("Z", "+00:00")))
            close_in_time = abs((t - et).total_seconds()) <= 7200
            same_ticker = bool(set(row.get("affected_tickers") or []) & set(exist.get("affected_tickers") or []))
            similar = _title_ratio(title_l, str(exist.get("title") or "").lower()) >= 70
            if close_in_time and same_ticker and similar:
                duplicate_idx = i
                break
            if _title_ratio(title_l, str(exist.get("title") or "").lower()) >= 85:
                duplicate_idx = i
                break

        if duplicate_idx < 0:
            out.append(row)
            continue

        old = out[duplicate_idx]
        row["cluster_id"] = old.get("cluster_id") or old["id"]
        keep_new = _source_rank(str(row.get("source"))) > _source_rank(str(old.get("source")))
        if keep_new:
            out[duplicate_idx] = row
        else:
            out[duplicate_idx]["cluster_id"] = row["cluster_id"]
    return out


def _insert_news(conn, rows: list[dict[str, Any]]) -> int:
    n = 0
    for r in rows:
        tc = conn.total_changes
        conn.execute(
            """
            INSERT INTO news
            (id, url, source, title, summary, published_at, primary_ticker, affected_tickers, severity, cluster_id)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
              summary=excluded.summary,
              published_at=excluded.published_at,
              affected_tickers=excluded.affected_tickers,
              cluster_id=COALESCE(news.cluster_id, excluded.cluster_id)
            """,
            (
                r["id"],
                r.get("url"),
                r["source"],
                r["title"],
                r.get("summary"),
                r["published_at"],
                r.get("primary_ticker"),
                json.dumps(r.get("affected_tickers") or [], ensure_ascii=False),
                r.get("severity") or "routine",
                r.get("cluster_id"),
            ),
        )
        if conn.total_changes > tc:
            n += 1
    return n


def _set_meta(conn, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO meta_kv (k, v, updated_at) VALUES (?,?,datetime('now'))
        ON CONFLICT(k) DO UPDATE SET v=excluded.v, updated_at=datetime('now')
        """,
        (key, value),
    )


def fetch_finnhub_news_rows(symbols: list[str]) -> list[dict[str, Any]]:
    if not finnhub_ok():
        return []
    out: list[dict[str, Any]] = []
    for s in symbols:
        try:
            for row in fetch_company_news(s, days_back=7)[:40]:
                title = str(row.get("headline") or "").strip()
                url = str(row.get("url") or "").strip()
                if not title:
                    continue
                out.append(
                    {
                        "id": _news_id(url or title, title, "finnhub"),
                        "url": url or None,
                        "source": "finnhub",
                        "title": title,
                        "summary": str(row.get("summary") or "").strip() or None,
                        "published_at": _iso_from_epoch(row.get("datetime")),
                        "primary_ticker": s,
                        "affected_tickers": [s],
                    }
                )
        except Exception:
            logger.warning("finnhub news failed for %s", s, exc_info=True)
    return out


def fetch_reuters_rss_rows(watchlist: set[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        feed = feedparser.parse(_REUTERS_BUSINESS_RSS)
    except Exception:
        logger.warning("reuters rss parse failed", exc_info=True)
        return out
    for e in getattr(feed, "entries", [])[:120]:
        title = str(getattr(e, "title", "")).strip()
        link = str(getattr(e, "link", "")).strip()
        summary = str(getattr(e, "summary", "")).strip() or None
        if not title:
            continue
        pub = None
        if getattr(e, "published", None):
            try:
                pub = parsedate_to_datetime(str(e.published)).astimezone(timezone.utc).isoformat()
            except Exception:
                pub = None
        text = f"{title} {summary or ''}"
        affected = _extract_tickers(text, watchlist)
        out.append(
            {
                "id": _news_id(link or title, title, "reuters"),
                "url": link or None,
                "source": "reuters",
                "title": title,
                "summary": summary,
                "published_at": _norm_iso(pub),
                "primary_ticker": affected[0] if affected else None,
                "affected_tickers": affected,
            }
        )
    return out


def fetch_sec_8k_rows(symbols: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    sess = requests.Session()
    sess.headers.update({"User-Agent": _SEC_UA, "Accept-Encoding": "gzip, deflate"})
    sec_map = _load_sec_ticker_map(sess)
    cutoff = datetime.now(timezone.utc) - timedelta(days=10)
    for s in symbols:
        su = s.upper()
        cik = sec_map.get(su) or _SEC_CIK_BY_TICKER.get(su)
        if not cik:
            continue
        try:
            r = sess.get(_SEC_SUBMISSIONS.format(cik=str(cik).zfill(10)), timeout=20)
            if not r.ok:
                continue
            payload = r.json() or {}
            recent = ((payload.get("filings") or {}).get("recent") or {})
            forms = list(recent.get("form") or [])
            accession = list(recent.get("accessionNumber") or [])
            filed = list(recent.get("filingDate") or [])
            primary_doc = list(recent.get("primaryDocument") or [])
            for i, form in enumerate(forms):
                if str(form).upper() != "8-K":
                    continue
                fdate = str(filed[i] or "")
                if not fdate:
                    continue
                dt = _as_utc_dt(datetime.fromisoformat(f"{fdate}T00:00:00+00:00"))
                if dt < cutoff:
                    continue
                acc = str(accession[i] or "")
                pdoc = str(primary_doc[i] or "")
                acc_plain = acc.replace("-", "")
                url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_plain}/{pdoc}" if pdoc else None
                title = f"{s} files 8-K ({fdate})"
                out.append(
                    {
                        "id": _news_id(url or title, title, "sec_edgar"),
                        "url": url,
                        "source": "sec_edgar",
                        "title": title,
                        "summary": f"SEC 8-K filing for {s} on {fdate}.",
                        "published_at": dt.isoformat(),
                        "primary_ticker": s,
                        "affected_tickers": [s],
                        "category": "legal",
                        "severity": "important",
                    }
                )
        except Exception:
            logger.debug("sec edgar fetch failed for %s", s, exc_info=True)
    return out


def fetch_yfinance_news_rows(symbols: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        import yfinance as yf  # noqa: PLC0415
    except Exception:
        return out
    for s in symbols:
        try:
            items = getattr(yf.Ticker(s), "news", None) or []
            for row in items[:25]:
                title = str(row.get("title") or "").strip()
                url = str(row.get("link") or "").strip()
                if not title:
                    continue
                out.append(
                    {
                        "id": _news_id(url or title, title, "yfinance"),
                        "url": url or None,
                        "source": "yfinance",
                        "title": title,
                        "summary": str(row.get("summary") or "").strip() or None,
                        "published_at": _iso_from_epoch(row.get("providerPublishTime")),
                        "primary_ticker": s,
                        "affected_tickers": [s],
                    }
                )
        except Exception:
            logger.debug("yfinance news failed for %s", s, exc_info=True)
    return out


def _extract_json_array(text: str) -> list[dict[str, Any]]:
    raw = (text or "").strip()
    if not raw:
        return []
    raw = raw.replace("```json", "").replace("```", "").strip()
    try:
        arr = json.loads(raw)
        return arr if isinstance(arr, list) else []
    except Exception:
        start = raw.find("[")
        end = raw.rfind("]")
        if start >= 0 and end > start:
            try:
                arr = json.loads(raw[start : end + 1])
                return arr if isinstance(arr, list) else []
            except Exception:
                return []
        return []


def classify_recent_news(conn, *, batch_size: int = 8) -> int:
    rows = conn.execute(
        """
        SELECT id, title, COALESCE(summary,'') AS summary
        FROM news
        WHERE category IS NULL OR severity IS NULL OR one_line_zh IS NULL
        ORDER BY published_at DESC
        LIMIT 120
        """
    ).fetchall()
    if not rows:
        return 0
    holdings = load_watchlist_tickers()
    tg = TokenGuard()
    updated = 0
    for i in range(0, len(rows), batch_size):
        chunk = rows[i : i + batch_size]
        payload = [{"id": r["id"], "title": r["title"], "summary": r["summary"]} for r in chunk]
        prompt = (
            "你是新闻分析师。对以下新闻输出 JSON 数组，每项必须包含字段："
            "id,category,severity,one_line_zh,affected_tickers,impact_on_holdings。\n"
            "category 可选 earnings|rating|macro|policy|M&A|product|legal|management|other；"
            "severity 可选 urgent|important|attention|routine。"
            f"\n持仓：{', '.join(holdings)}\n新闻：{json.dumps(payload, ensure_ascii=False)}"
        )
        out = tg.call("news_classify_batch", prompt)
        arr = _extract_json_array(str(out.get("text") or ""))
        for obj in arr:
            nid = str(obj.get("id") or "")
            if not nid:
                continue
            conn.execute(
                """
                UPDATE news
                SET category = ?,
                    severity = ?,
                    one_line_zh = ?,
                    affected_tickers = COALESCE(?, affected_tickers),
                    impact_on_holdings = ?
                WHERE id = ?
                """,
                (
                    str(obj.get("category") or "other")[:30],
                    str(obj.get("severity") or "attention")[:20],
                    str(obj.get("one_line_zh") or "")[:120] or None,
                    json.dumps(obj.get("affected_tickers") or [], ensure_ascii=False),
                    (str(obj.get("impact_on_holdings") or "").strip() or None),
                    nid,
                ),
            )
            updated += 1
    return updated


def generate_storylines(conn) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    rows = conn.execute(
        """
        SELECT id, title, COALESCE(one_line_zh,'') AS one_line_zh, COALESCE(category,'other') AS category,
               COALESCE(severity,'attention') AS severity, COALESCE(affected_tickers,'[]') AS affected_tickers
        FROM news
        WHERE published_at >= ?
        ORDER BY CASE severity WHEN 'urgent' THEN 4 WHEN 'important' THEN 3 WHEN 'attention' THEN 2 ELSE 1 END DESC,
                 published_at DESC
        LIMIT 30
        """,
        (cutoff,),
    ).fetchall()
    if len(rows) < 5:
        return 0
    holdings = load_watchlist_tickers()
    payload = [dict(r) for r in rows]
    tg = TokenGuard()
    prompt = (
        "根据过去24小时美股新闻，提炼 3 条市场叙事主线，输出 JSON 数组。"
        "每项必须包含：title,narrative,representative_tickers,tickers_with_changes,"
        "related_news_ids,impact_on_user_holdings,early_movers,intensity。"
        f"我的持仓：{', '.join(holdings)}。新闻：{json.dumps(payload, ensure_ascii=False)}"
    )
    out = tg.call("storyline_generation", prompt)
    arr = _extract_json_array(str(out.get("text") or ""))
    log_dir = Path(__file__).resolve().parents[1] / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"storyline_generation_{datetime.now(timezone.utc).strftime('%Y%m%d')}.log"
    log_file.write_text(
        "\n".join(
            [
                f"ts={datetime.now(timezone.utc).isoformat()}",
                f"routed={out.get('routed')}",
                f"model={out.get('model')}",
                f"rows_in={len(rows)}",
                f"json_ok={1 if arr else 0}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    if not arr:
        logger.error("storyline generation failed: empty/invalid json")
        return 0

    def _intensity(value: object) -> int:
        if value is None:
            return 5
        if isinstance(value, (int, float)):
            return max(1, min(10, int(value)))
        raw = str(value).strip().lower()
        zh_map = {
            "高": 8,
            "较高": 7,
            "中高": 7,
            "中": 5,
            "中等": 5,
            "低": 3,
            "较低": 2,
        }
        if raw in zh_map:
            return zh_map[raw]
        en_map = {"high": 8, "medium": 5, "mid": 5, "low": 3}
        if raw in en_map:
            return en_map[raw]
        try:
            return max(1, min(10, int(float(raw))))
        except (TypeError, ValueError):
            logger.warning("storyline intensity not numeric: %r; using 5", value)
            return 5

    day = datetime.now(timezone.utc).date().isoformat()
    conn.execute("DELETE FROM storylines WHERE date = ?", (day,))
    n = 0
    for i, obj in enumerate(arr[:3], start=1):
        sid = hashlib.sha1(f"{day}|{obj.get('title','')}|{i}".encode("utf-8")).hexdigest()
        conn.execute(
            """
            INSERT INTO storylines
            (id, date, rank, title, narrative, representative_tickers, tickers_with_changes,
             related_news_ids, impact_on_user_holdings, early_movers, intensity)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                sid,
                day,
                i,
                str(obj.get("title") or f"主线{i}")[:80],
                str(obj.get("narrative") or "")[:240],
                json.dumps(obj.get("representative_tickers") or [], ensure_ascii=False),
                json.dumps(obj.get("tickers_with_changes") or [], ensure_ascii=False),
                json.dumps(obj.get("related_news_ids") or [], ensure_ascii=False),
                str(obj.get("impact_on_user_holdings") or "无直接影响")[:200],
                json.dumps(obj.get("early_movers") or [], ensure_ascii=False),
                _intensity(obj.get("intensity")),
            ),
        )
        for nid in obj.get("related_news_ids") or []:
            conn.execute("UPDATE news SET storyline_id = ? WHERE id = ?", (sid, str(nid)))
        n += 1
    return n


def run_news_ingest(conn) -> dict[str, Any]:
    symbols = [s.upper() for s in load_watchlist_tickers()]
    watch = set(symbols)
    now_iso = datetime.now(timezone.utc).isoformat()
    raw: list[dict[str, Any]] = []
    source_stats: dict[str, dict[str, Any]] = {}

    pulls = [
        ("finnhub", lambda: fetch_finnhub_news_rows(symbols)),
        ("reuters", lambda: fetch_reuters_rss_rows(watch)),
        ("sec_edgar", lambda: fetch_sec_8k_rows(symbols)),
        ("yfinance", lambda: fetch_yfinance_news_rows(symbols)),
    ]
    for source, fn in pulls:
        try:
            rows = fn()
            raw.extend(rows)
            source_stats[source] = {"ok": True, "count": len(rows), "error": None}
            _set_meta(conn, f"news_source:{source}:last_ok", now_iso)
            _set_meta(conn, f"news_source:{source}:last_count", str(len(rows)))
            _set_meta(conn, f"news_source:{source}:last_error", "")
        except Exception as e:
            msg = str(e)[:300]
            source_stats[source] = {"ok": False, "count": 0, "error": msg}
            _set_meta(conn, f"news_source:{source}:last_error", msg)

    merged = _dedupe_and_cluster(raw)
    new_rows = _insert_news(conn, merged)
    tagged = classify_recent_news(conn)
    storylines = generate_storylines(conn)
    return {
        "fetched": len(raw),
        "after_dedupe": len(merged),
        "inserted": new_rows,
        "classified": tagged,
        "storylines": storylines,
        "sources": source_stats,
    }
