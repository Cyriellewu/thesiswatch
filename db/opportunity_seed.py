"""Seed opportunity_candidates from `config/opportunity_universe.yaml` (no fabricated scores)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import yaml

from db.client import repo_root


def seed_opportunity_universe_if_empty(conn: sqlite3.Connection) -> None:
    if conn.execute("SELECT COUNT(*) AS n FROM opportunity_candidates").fetchone()["n"]:
        return

    path = repo_root() / "config" / "opportunity_universe.yaml"
    if not path.exists():
        return

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rows = raw.get("tickers") or []
    for row in rows:
        if isinstance(row, str):
            sym = str(row).upper().strip()
            theme = ""
        else:
            sym = str((row or {}).get("symbol") or "").upper().strip()
            theme = str((row or {}).get("theme") or "").strip()
        if not sym:
            continue
        blurb = theme or "机会池观测标的（分数由脉冲任务更新）。"
        conn.execute(
            """INSERT INTO opportunity_candidates (ticker, signals_json, score, bucket, blurb)
               VALUES (?,?,?,?,?)""",
            (sym, "{}", 0, "watch", blurb),
        )
