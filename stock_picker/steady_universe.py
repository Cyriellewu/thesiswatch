from __future__ import annotations

"""A simple steady-stock library for the "star it and we'll watch it" flow."""

import os
from dataclasses import dataclass
from typing import Iterable

from data_layer.market_data import fetch_quotes
from stock_picker.practical_zones import PracticalZones, calc_practical_zones


STEADY_UNIVERSE_CRITERIA = {
    "min_market_cap": 50_000_000_000,
    "min_5y_return": 0.30,
    "max_5y_drawdown": -0.35,
    "max_annual_volatility": 0.30,
    "min_revenue_growth_consistency": 4,
}


STEADY_LIBRARY: dict[str, dict] = {
    "JNJ": {"name": "Johnson & Johnson", "category": "防御之王", "reason": "医疗防御，波动小，适合补非科技仓。", "r5": 25, "dd": -13, "vol": 12},
    "KO": {"name": "Coca-Cola", "category": "防御之王", "reason": "消费防御现金流稳定，不靠短期题材。", "r5": 30, "dd": -14, "vol": 13},
    "PG": {"name": "Procter & Gamble", "category": "防御之王", "reason": "日用品龙头，经济周期里更稳。", "r5": 50, "dd": -18, "vol": 14},
    "WMT": {"name": "Walmart", "category": "防御之王", "reason": "零售龙头，长期趋势稳，消费防御属性强。", "r5": 90, "dd": -15, "vol": 16},
    "COST": {"name": "Costco", "category": "必需消费", "reason": "会员制零售强，长期慢牛，适合科技外分散。", "r5": 200, "dd": -19, "vol": 18},
    "MCD": {"name": "McDonald's", "category": "必需消费", "reason": "全球餐饮龙头，现金流和品牌稳定。", "r5": 55, "dd": -20, "vol": 16},
    "PEP": {"name": "PepsiCo", "category": "必需消费", "reason": "饮料零食双线，防御性比科技更强。", "r5": 45, "dd": -17, "vol": 14},
    "GOOGL": {"name": "Alphabet", "category": "稳健成长", "reason": "广告和云业务龙头，长期向上但仍有科技波动。", "r5": 120, "dd": -28, "vol": 25},
    "AAPL": {"name": "Apple", "category": "稳健成长", "reason": "消费科技龙头，生态稳定，适合长期观察。", "r5": 130, "dd": -25, "vol": 24},
    "MSFT": {"name": "Microsoft", "category": "稳健成长", "reason": "云和企业软件龙头，质量高，适合长期配置。", "r5": 140, "dd": -27, "vol": 23},
    "V": {"name": "Visa", "category": "金融 / 支付", "reason": "支付网络龙头，轻资产现金流强。", "r5": 75, "dd": -20, "vol": 18},
    "MA": {"name": "Mastercard", "category": "金融 / 支付", "reason": "支付网络龙头，长期趋势稳。", "r5": 85, "dd": -22, "vol": 18},
    "UNH": {"name": "UnitedHealth", "category": "医疗健康", "reason": "医疗保险龙头，长期需求确定。", "r5": 110, "dd": -28, "vol": 22},
    "HD": {"name": "Home Depot", "category": "稳健成长", "reason": "家装零售龙头，周期性存在但质量较高。", "r5": 70, "dd": -30, "vol": 23},
    "BX": {"name": "Blackstone", "category": "金融 / 支付", "reason": "另类资产龙头，AI 数据中心和私募资产主题有催化。", "r5": 95, "dd": -34, "vol": 29},
    "BLK": {"name": "BlackRock", "category": "金融 / 支付", "reason": "资管龙头，适合金融板块补位观察。", "r5": 70, "dd": -32, "vol": 25},
    "JPM": {"name": "JPMorgan", "category": "金融 / 支付", "reason": "美国银行龙头，质量高但受利率周期影响。", "r5": 85, "dd": -30, "vol": 24},
    "VOO": {"name": "Vanguard S&P 500 ETF", "category": "ETF 推荐", "reason": "大盘底仓，适合定投和降低单股风险。", "r5": 85, "dd": -25, "vol": 18},
    "VTI": {"name": "Vanguard Total Stock Market ETF", "category": "ETF 推荐", "reason": "全美股票底仓，适合长期定投。", "r5": 85, "dd": -25, "vol": 18},
    "SCHD": {"name": "Schwab U.S. Dividend Equity ETF", "category": "ETF 推荐", "reason": "偏高股息和防御，波动相对更小。", "r5": 60, "dd": -20, "vol": 15},
    "QQQ": {"name": "Invesco QQQ ETF", "category": "ETF 推荐", "reason": "科技成长 ETF，长期强但和你现有科技仓重叠高。", "r5": 130, "dd": -33, "vol": 24},
}


AVOID_LIBRARY: dict[str, str] = {
    "TSLA": "波动太大，适合单独观察，不放进稳健票库。",
    "OKLO": "小盘风口股，主题强但过山车。",
    "IREN": "高波动卫星仓，不属于稳健慢牛。",
    "SMCI": "历史财务/波动风险较大。",
    "SOFI": "小盘成长波动大，不是懒人稳健票。",
    "NIO": "长期趋势和基本面不够稳。",
    "AI": "题材波动大，不是稳健票。",
}


@dataclass
class SteadyRow:
    ticker: str
    name: str
    category: str
    reason: str
    price: float
    chg_pct: float
    return_5y_pct: float
    max_drawdown_5y_pct: float
    annual_volatility_pct: float
    zones: PracticalZones


def build_steady_universe(symbols: Iterable[str] | None = None) -> list[SteadyRow]:
    tickers = [s.upper() for s in (symbols or STEADY_LIBRARY.keys()) if s.upper() in STEADY_LIBRARY]
    qmap = {q.symbol.upper(): q for q in fetch_quotes(tickers, ttl_seconds=120)} if tickers else {}
    rows: list[SteadyRow] = []
    for ticker in tickers:
        meta = STEADY_LIBRARY[ticker]
        quote = qmap.get(ticker)
        price = float(quote.px) if quote else 0.0
        zones = calc_practical_zones(ticker, price=price if price > 0 else None)
        rows.append(
            SteadyRow(
                ticker=ticker,
                name=str(meta["name"]),
                category=str(meta["category"]),
                reason=str(meta["reason"]),
                price=zones.current_price,
                chg_pct=float(quote.chg_pct) if quote else 0.0,
                return_5y_pct=float(meta["r5"]),
                max_drawdown_5y_pct=float(meta["dd"]),
                annual_volatility_pct=float(meta["vol"]),
                zones=zones,
            )
        )
    return rows


def screen_steady_universe(all_tickers: Iterable[str]) -> list[SteadyRow]:
    """Best-effort screen; currently returns known steady names from the input.

    The criteria are kept explicit so this can later be upgraded to full
    fundamentals without changing the UI contract.
    """

    return build_steady_universe([t for t in all_tickers if t.upper() in STEADY_LIBRARY])


def offline_safe_steady_universe() -> list[SteadyRow]:
    old = os.environ.get("ALPHAWATCH_OFFLINE")
    os.environ["ALPHAWATCH_OFFLINE"] = "1"
    try:
        return build_steady_universe()
    finally:
        if old is None:
            os.environ.pop("ALPHAWATCH_OFFLINE", None)
        else:
            os.environ["ALPHAWATCH_OFFLINE"] = old

