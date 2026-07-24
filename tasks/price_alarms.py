"""价格提醒（price alarms）：本地 SQLite 存储 + 触发后走现有 ntfy 推送。

- 每条提醒：ticker + direction（'above' 突破上方 / 'below' 跌破下方）+ target_price。
- 触发一次后自动置为 inactive（避免反复轰炸），可在面板重新启用。
- 依赖已有的 data_layer.market_data.fetch_quotes 与 push.notify.send_alert。
"""

from __future__ import annotations

from typing import Any

from db.client import get_conn


def add_alarm(ticker: str, direction: str, target_price: float, note: str = "") -> int:
    tk = str(ticker or "").upper().strip()
    d = str(direction or "").lower().strip()
    if d not in {"above", "below"}:
        raise ValueError("direction must be 'above' or 'below'")
    if not tk:
        raise ValueError("ticker required")
    price = float(target_price)
    conn = get_conn(read_only=False)
    try:
        cur = conn.execute(
            "INSERT INTO price_alarms (ticker, direction, target_price, note) VALUES (?,?,?,?)",
            (tk, d, price, str(note or "")),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def list_alarms(active_only: bool = False) -> list[dict[str, Any]]:
    conn = get_conn(read_only=True)
    try:
        sql = "SELECT * FROM price_alarms"
        if active_only:
            sql += " WHERE active = 1"
        sql += " ORDER BY active DESC, created_at DESC"
        return [dict(r) for r in conn.execute(sql).fetchall()]
    finally:
        conn.close()


def delete_alarm(alarm_id: int) -> None:
    conn = get_conn(read_only=False)
    try:
        conn.execute("DELETE FROM price_alarms WHERE id = ?", (int(alarm_id),))
        conn.commit()
    finally:
        conn.close()


def set_active(alarm_id: int, active: bool) -> None:
    conn = get_conn(read_only=False)
    try:
        conn.execute(
            "UPDATE price_alarms SET active = ?, triggered_at = NULL WHERE id = ?",
            (1 if active else 0, int(alarm_id)),
        )
        conn.commit()
    finally:
        conn.close()


def _crossed(direction: str, px: float, target: float) -> bool:
    if direction == "above":
        return px >= target
    return px <= target


def check_alarms(px_map: dict[str, float] | None = None, *, send: bool = True) -> list[dict[str, Any]]:
    """检查所有 active 提醒；命中则（可选）推 ntfy 并置为 inactive。返回命中列表。"""

    active = list_alarms(active_only=True)
    if not active:
        return []

    prices: dict[str, float] = {str(k).upper(): float(v) for k, v in (px_map or {}).items()}
    missing = sorted({a["ticker"] for a in active} - set(prices))
    if missing:
        try:
            from data_layer.market_data import fetch_quotes  # noqa: PLC0415

            for q in fetch_quotes(missing) or []:
                prices[str(q.symbol).upper()] = float(q.px)
        except Exception:
            pass

    triggered: list[dict[str, Any]] = []
    conn = get_conn(read_only=False)
    try:
        for a in active:
            tk = str(a["ticker"]).upper()
            px = prices.get(tk)
            if px is None or px <= 0:
                continue
            if not _crossed(str(a["direction"]), px, float(a["target_price"])):
                continue
            triggered.append({**a, "last_price": px})
            conn.execute(
                "UPDATE price_alarms SET active = 0, triggered_at = datetime('now'), last_price = ? WHERE id = ?",
                (px, int(a["id"])),
            )
        conn.commit()
    finally:
        conn.close()

    if send:
        for t in triggered:
            _push(t)
    return triggered


def _push(alarm: dict[str, Any]) -> bool:
    try:
        from push.notify import send_alert  # noqa: PLC0415
    except Exception:
        return False
    tk = str(alarm["ticker"]).upper()
    arrow = "突破上方" if alarm["direction"] == "above" else "跌破下方"
    target = float(alarm["target_price"])
    px = float(alarm.get("last_price") or 0.0)
    emoji = "📈" if alarm["direction"] == "above" else "📉"
    title = f"{emoji} 价格提醒 · {tk}"
    body_lines = [
        f"{tk} 现价 ${px:,.2f}，已{arrow}你设定的 ${target:,.2f}",
    ]
    if str(alarm.get("note") or "").strip():
        body_lines.append(f"备注：{alarm['note']}")
    body_lines.append("请到券商 App 自行判断，本提醒不代下单。")
    return send_alert("attention", title, "\n".join(body_lines), tags="bell")
