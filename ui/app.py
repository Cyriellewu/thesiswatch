from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Prefer project-local .env values for reproducible local runs.
load_dotenv(ROOT / ".env", override=True)

from db.client import bootstrap_database  # noqa: E402
from ui import tab_agent_funds  # noqa: E402
from ui import tab_agent_home  # noqa: E402
from ui import tab_agents  # noqa: E402
from ui import tab_auto_watch  # noqa: E402
from ui import tab_committee  # noqa: E402
from ui import tab_dashboard  # noqa: E402
from ui import tab_holdings  # noqa: E402
from ui import tab_news  # noqa: E402
from ui import tab_portfolio_editor  # noqa: E402
from ui import tab_portfolio_risk  # noqa: E402
from ui import tab_stock_picker  # noqa: E402
from ui import tab_thesis  # noqa: E402
from ui import tab_tokens  # noqa: E402


def main() -> None:
    st.set_page_config(page_title="Willow's Stock Agent", layout="wide")
    bootstrap_database()

    # 首页 = Willow's Stock Agent(单页命令中心)
    tab_agent_home.render()

    st.divider()
    # 旧的 6 个标签页默认不加载(它们会联网抓很多数据、拖慢整页);需要时再打开。
    show_adv = st.toggle(
        "🛠️ 打开高级工具（原始标签页：股票墙 / 持仓 / 要闻 / 选股 / 提醒 / 实验室）",
        value=False, key="show_advanced_tools",
        help="这些是旧页面,联网抓取较多、较慢。平时用上面的 Agent 首页即可。",
    )
    if show_adv:
        thesis, risk, editor, committee, dashboard, holdings, news, picker, auto_watch, agents, funds = st.tabs(
            ["📓 投资论点", "📐 组合风险", "✏️ 编辑持仓", "🏛️ 投资委员会", "📈 股票墙", "📊 我的持仓", "📰 今日要闻", "✨ 选股池", "🔔 自动提醒", "🤖 实验室", "🏟️ Agent 竞技场"]
        )

        with thesis:
            tab_thesis.render()

        with risk:
            tab_portfolio_risk.render()

        with editor:
            tab_portfolio_editor.render()

        with committee:
            tab_committee.render()

        with dashboard:
            tab_dashboard.render()

        with holdings:
            tab_holdings.render()

        with news:
            tab_news.render()

        with picker:
            tab_stock_picker.render()

        with auto_watch:
            tab_auto_watch.render()

        with agents:
            tab_agents.render()
            with st.expander("💰 Token 消耗（保留）", expanded=False):
                tab_tokens.render()

        with funds:
            tab_agent_funds.render()


if __name__ == "__main__":
    main()
