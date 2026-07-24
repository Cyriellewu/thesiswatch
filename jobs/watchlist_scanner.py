from __future__ import annotations

"""CLI entrypoint for starred watchlist sentinels."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db.client import bootstrap_database, get_conn
from monitors.watchlist_sentinels import scan_watchlist_sentinels


def run_watchlist_scanner_once() -> dict:
    bootstrap_database()
    conn = get_conn()
    try:
        inserted = scan_watchlist_sentinels(conn)
        conn.commit()
        return {"status": "ok", "alerts_inserted": inserted}
    finally:
        conn.close()


if __name__ == "__main__":
    print(json.dumps(run_watchlist_scanner_once(), ensure_ascii=False, indent=2))

