from __future__ import annotations

import argparse
import importlib
import os
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
except Exception:
    pass

from db.client import default_db_path


def _connect(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _one(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> sqlite3.Row | None:
    try:
        return conn.execute(sql, params).fetchone()
    except sqlite3.Error as exc:
        print(f"SQL ERROR: {exc}\n  {sql.strip()}")
        return None


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    try:
        return list(conn.execute(sql, params).fetchall())
    except sqlite3.Error as exc:
        print(f"SQL ERROR: {exc}\n  {sql.strip()}")
        return []


def _ok(flag: bool) -> str:
    return "OK" if flag else "FAIL"


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


def _has_import(name: str) -> tuple[bool, str]:
    try:
        importlib.import_module(name)
        return True, ""
    except Exception as exc:
        return False, str(exc)


def _print_news_counts(conn: sqlite3.Connection) -> None:
    _section("DB news status")
    row = _one(
        conn,
        """
        SELECT
            COUNT(*) AS total,
            COUNT(category) AS tagged,
            COUNT(one_line_zh) AS has_zh,
            SUM(CASE WHEN category IS NULL THEN 1 ELSE 0 END) AS pending
        FROM news
        """,
    )
    if not row:
        return
    total = int(row["total"] or 0)
    tagged = int(row["tagged"] or 0)
    has_zh = int(row["has_zh"] or 0)
    pending = int(row["pending"] or 0)
    print(f"total={total} tagged={tagged} has_zh={has_zh} pending={pending}")
    if total == 0:
        print("FAIL: news 表是空的，先跑抓取。")
    elif has_zh == 0:
        print("FAIL: 有原始新闻，但没有 one_line_zh，打标签链路没跑通。")
    elif has_zh < min(100, total):
        print("WARN: 已有中文摘要，但覆盖率偏低；页面还会大量显示待处理。")
    else:
        print("OK: 中文摘要数量达到基本可用水平。")

    sev = _rows(
        conn,
        """
        SELECT COALESCE(severity,'pending') AS severity, COUNT(*) AS n
        FROM news
        GROUP BY COALESCE(severity,'pending')
        ORDER BY n DESC
        """,
    )
    if sev:
        print("severity distribution:")
        for r in sev:
            print(f"  {r['severity']}: {r['n']}")

    src = _rows(
        conn,
        """
        SELECT source, COUNT(*) AS n
        FROM news
        GROUP BY source
        ORDER BY n DESC
        LIMIT 10
        """,
    )
    if src:
        print("top sources:")
        for r in src:
            print(f"  {r['source']}: {r['n']}")


def _print_samples(conn: sqlite3.Connection) -> None:
    _section("Tagged samples")
    rows = _rows(
        conn,
        """
        SELECT title, one_line_zh, severity, category
        FROM news
        WHERE one_line_zh IS NOT NULL
        ORDER BY published_at DESC
        LIMIT 8
        """,
    )
    if not rows:
        print("FAIL: 没有可展示的 tagged samples。")
        return
    for r in rows:
        print(f"- title: {r['title']}")
        print(f"  zh:    {r['one_line_zh']}")
        print(f"  meta:  {r['severity']} / {r['category']}")


def _print_storylines(conn: sqlite3.Connection) -> None:
    _section("Storylines")
    rows = _rows(
        conn,
        """
        SELECT title, narrative, impact_on_user_holdings
        FROM storylines
        WHERE date = date('now','localtime')
        ORDER BY rank ASC
        """,
    )
    print(f"today_storylines={len(rows)}")
    if not rows:
        print("WARN: 今天没有主线。若你希望主线每天自动生成，需要开启/运行 LLM storyline。")
        return
    for r in rows:
        print(f"- {r['title']}")
        print(f"  narrative: {r['narrative']}")
        print(f"  impact:    {r['impact_on_user_holdings']}")


def _print_env_and_imports() -> None:
    _section("Env and imports")
    gemini = bool(os.environ.get("GEMINI_API_KEY"))
    groq = bool(os.environ.get("GROQ_API_KEY"))
    auto_llm = os.environ.get("ALPHA_NEWS_AUTO_LLM", "1")
    print(f"GEMINI_API_KEY: {_ok(gemini)}")
    print(f"GROQ_API_KEY:   {_ok(groq)}")
    print(f"ALPHA_NEWS_AUTO_LLM={auto_llm}")
    if auto_llm != "1":
        print("NOTE: daemon 当前不会自动跑 LLM 打标签/主线；要自动跑，设置 ALPHA_NEWS_AUTO_LLM=1。")

    for mod in (
        "news_pipeline.orchestrator",
        "news_pipeline.news_tagger",
        "news_pipeline.storyline_generator",
        "jobs.news_cycle",
    ):
        ok, err = _has_import(mod)
        print(f"import {mod}: {_ok(ok)}" + (f" ({err})" if err else ""))


def _print_daemon_wiring() -> None:
    _section("Daemon wiring")
    daemon = ROOT / "jobs" / "daemon.py"
    news_cycle = ROOT / "jobs" / "news_cycle.py"
    daemon_text = daemon.read_text(encoding="utf-8") if daemon.exists() else ""
    cycle_text = news_cycle.read_text(encoding="utf-8") if news_cycle.exists() else ""
    print(f"daemon imports run_real_news_cycle: {_ok('run_real_news_cycle' in daemon_text)}")
    print(f"daemon calls run_real_news_cycle:   {_ok('run_real_news_cycle()' in daemon_text or 'run_real_news_cycle(' in daemon_text)}")
    print(f"news_cycle calls run_news_pipeline: {_ok('run_news_pipeline' in cycle_text)}")
    print(f"news_cycle calls run_news_tagging:  {_ok('run_news_tagging' in cycle_text)}")
    print(f"news_cycle calls storyline:         {_ok('run_storyline_pipeline' in cycle_text)}")


def _smoke_llm() -> None:
    _section("LLM smoke test")
    if os.environ.get("GROQ_API_KEY"):
        try:
            from groq import Groq

            client = Groq(api_key=os.environ["GROQ_API_KEY"])
            resp = client.chat.completions.create(
                model=os.environ.get("GROQ_TAGGER_MODEL", "llama-3.3-70b-versatile"),
                messages=[
                    {"role": "system", "content": "Return JSON only."},
                    {"role": "user", "content": '{"ok":true}'},
                ],
                temperature=0,
                max_tokens=20,
                response_format={"type": "json_object"},
            )
            print(f"Groq smoke: OK ({resp.choices[0].message.content})")
        except Exception as exc:
            print(f"Groq smoke: FAIL ({exc})")
    else:
        print("Groq smoke: SKIP (GROQ_API_KEY missing)")

    if os.environ.get("GEMINI_API_KEY"):
        try:
            import google.generativeai as genai

            genai.configure(api_key=os.environ["GEMINI_API_KEY"])
            model = genai.GenerativeModel(os.environ.get("GEMINI_TAGGER_MODEL", "gemini-2.0-flash-lite"))
            resp = model.generate_content("say hi", request_options={"timeout": 8})
            print(f"Gemini smoke: OK ({str(resp.text).strip()[:80]})")
        except Exception as exc:
            print(f"Gemini smoke: FAIL ({exc})")
    else:
        print("Gemini smoke: SKIP (GEMINI_API_KEY missing)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose AlphaWatch real-news pipeline.")
    parser.add_argument("--db", type=Path, default=default_db_path(), help="SQLite DB path.")
    parser.add_argument("--smoke-llm", action="store_true", help="Call configured LLM providers once.")
    args = parser.parse_args()

    print(f"repo={ROOT}")
    print(f"db={args.db}")
    if not args.db.exists():
        print("FAIL: DB file does not exist.")
        return 2

    _print_env_and_imports()
    _print_daemon_wiring()
    conn = _connect(args.db)
    try:
        _print_news_counts(conn)
        _print_samples(conn)
        _print_storylines(conn)
    finally:
        conn.close()
    if args.smoke_llm:
        _smoke_llm()
    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
