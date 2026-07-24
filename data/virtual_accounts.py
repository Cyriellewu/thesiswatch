from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class Trade:
    ts: str
    symbol: str
    side: str
    shares: float
    price: float
    reason: str


@dataclass
class VirtualAccount:
    name: str
    initial_cash: float
    cash: float
    positions: dict[str, dict[str, float]] = field(default_factory=dict)
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[dict[str, float | str]] = field(default_factory=list)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _store_dir() -> Path:
    p = _repo_root() / "data" / "virtual_accounts"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _path(name: str) -> Path:
    return _store_dir() / f"{name}.json"


def load_virtual_account(name: str) -> VirtualAccount:
    p = _path(name)
    if not p.exists():
        return VirtualAccount(name=name, initial_cash=0.0, cash=0.0)
    raw = json.loads(p.read_text(encoding="utf-8"))
    trades = [Trade(**t) for t in raw.get("trades") or []]
    return VirtualAccount(
        name=str(raw.get("name") or name),
        initial_cash=float(raw.get("initial_cash") or 0.0),
        cash=float(raw.get("cash") or 0.0),
        positions=dict(raw.get("positions") or {}),
        trades=trades,
        equity_curve=list(raw.get("equity_curve") or []),
    )


def save_virtual_account(account: VirtualAccount) -> None:
    payload = asdict(account)
    _path(account.name).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

