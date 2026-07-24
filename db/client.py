from __future__ import annotations

import os
import sqlite3
from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_db_path() -> Path:
    env = os.environ.get("ALPHA_DB_PATH")
    if env:
        p = Path(env)
        return p if p.is_absolute() else repo_root() / p
    return repo_root() / "data" / "alphawatch.db"


def get_conn(read_only: bool = False) -> sqlite3.Connection:
    path = default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if read_only:
        uri = f"file:{path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, check_same_thread=False, timeout=30)
        conn.execute("PRAGMA busy_timeout = 30000")
    else:
        conn = sqlite3.connect(path.as_posix(), check_same_thread=False, timeout=30)
        conn.execute("PRAGMA busy_timeout = 30000")
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
        except sqlite3.OperationalError:
            # Some local/sandboxed runs expose the db as read-only briefly.
            # Keep the longer busy timeout rather than crashing during bootstrap.
            pass
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn: sqlite3.Connection | None = None) -> None:
    owns = conn is None
    c = conn or get_conn(read_only=False)
    try:
        schema = (repo_root() / "db" / "schema.sql").read_text(encoding="utf-8")
        c.executescript(schema)
        from db.migrate import apply_migrations

        apply_migrations(c)
        c.commit()
    finally:
        if owns:
            c.close()


def bootstrap_database() -> None:
    """建表 + 本地虚拟账户种子 + 机会池（不写入假推送、不造纸面收益曲线）。"""

    from db.agent_seed import seed_virtual_agents_if_empty
    from db.opportunity_seed import seed_opportunity_universe_if_empty

    conn = get_conn()
    try:
        init_schema(conn)
        seed_virtual_agents_if_empty(conn)
        seed_opportunity_universe_if_empty(conn)
        _seed_agent_funds_if_empty(conn)
        conn.commit()
    finally:
        conn.close()


def _seed_agent_funds_if_empty(conn) -> None:
    """Seed the Agent Arena fund roster on first run (idempotent)."""
    try:
        from agent_funds.arena import prepare_arena

        prepare_arena(conn)
    except Exception:
        pass



def seed_demo_if_empty() -> None:
    """兼容 Streamlit 入口；与 `bootstrap_database` 等价。"""

    bootstrap_database()
