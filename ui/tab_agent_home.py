"""🐕 Willow's Stock Agent — 单页首页(阶段 1)。

一个平静的命令中心:打开就知道「今天要不要动、动哪只、为什么、盯什么价位」。
- 顶部:奶奶版一句话 + 今日简报(做什么/别做/盯什么)+ 组合状态
- 重点:一张大图(TradingView + MACD/RSI)+ 新手信号卡
- 价格提醒(生效中) + 新闻雷达(精选)
- 持仓快照(只显示需注意的)
旧的 6 个标签页由 ui/app.py 收进「高级工具」折叠区。
"""

from __future__ import annotations

import streamlit as st

from tasks import price_alarms as pa
from tasks import willow_agent
from tasks import willow_memory as wm
from data_layer import guru_holdings
from ui import native_chart
from ui.tab_dashboard import _advanced, _ta_gauge

_WATCH_EXTRA = ["SMH", "SOXX", "TSM", "CRWV", "BE", "SMR", "AMZN", "AAPL", "MU", "AMD", "VOO", "SPAXX"]

_RISK_COLOR = {"routine": "🟢", "attention": "🟡", "major": "🟠", "urgent": "🔴"}


@st.cache_data(ttl=300, show_spinner=False)
def _advice_cached() -> dict:
    return willow_agent.build_advice()


@st.cache_data(ttl=12 * 3600, show_spinner=False)
def _gurus_cached(my_tickers: tuple[str, ...]) -> tuple[list, dict, dict]:
    gurus = guru_holdings.load_all_gurus(top_n=10)
    ov = guru_holdings.overlap_with_holdings(gurus, list(my_tickers))
    ins = guru_holdings.build_insights(gurus, list(my_tickers))
    return gurus, ov, ins


def _news_line(item: dict) -> str:
    text = item.get("title") or item.get("text") or item.get("source_title") or ""
    url = item.get("url") or item.get("source_url") or ""
    src = item.get("source") or ""
    label = f"{text}" + (f" ·{src}" if src else "")
    return f"[{label}]({url})" if url else label


def render() -> None:
    top = st.columns([3, 2, 1])
    with top[0]:
        st.markdown("## 🐕 Willow's Stock Agent")
    advice = _advice_cached()
    with top[1]:
        st.caption(f"更新于 {advice.get('as_of_label','')}｜规则化建议,不代下单")
    with top[2]:
        if st.button("🔄 刷新简报", use_container_width=True):
            _advice_cached.clear()
            st.rerun()
        if st.button("📲 推到手机", use_container_width=True, help="通过 ntfy 发送今日简报(需配置 NTFY_TOPIC)"):
            ok = willow_agent.push_daily_digest(advice, force=True)
            st.success("已发送到手机 ✅") if ok else st.warning("发送失败(检查 .env 里的 NTFY_TOPIC)")
        if st.button("📸 记录快照", use_container_width=True, help="存一次快照,之后能对比较上次的变化"):
            wm.save_snapshot(advice, trigger="manual")
            st.success("已记录 ✅")

    # ---------- Today:今天需要做什么(三状态 thesis 引擎) ----------
    try:
        from tasks import thesis_scan  # noqa: PLC0415

        gurus = st.session_state.get("gurus_loaded")
        ps, statuses = thesis_scan.scan_portfolio(advice, gurus=gurus, persist=False)
        st.markdown(f"### {ps.emoji} {ps.headline_zh}")
        if ps.needs_attention:
            st.markdown("**🟠 需要重新评估**")
            for s in ps.needs_attention[:3]:
                with st.container(border=True):
                    st.markdown(f"**{s.ticker}** · {s.status_zh}")
                    st.caption("；".join(s.reasons[:2]))
        if ps.worth_watching:
            st.markdown("**🟡 值得留意**")
            for s in ps.worth_watching[:4]:
                st.caption(f"• **{s.ticker}**：{'；'.join(s.reasons[:1])}")
        if ps.no_change:
            with st.expander(f"🟢 无实质变化（{len(ps.no_change)} 只，仅价格波动）", expanded=False):
                st.caption("、".join(s.ticker for s in ps.no_change))
        st.caption("提示：首次运行会先为持仓建立论点快照，之后每天才能对比“变了什么”。到 📓 投资论点 页补充逻辑/风险/失效条件，判断会更准。")
    except Exception as e:  # never break the home page on a thesis error
        st.caption(f"（Thesis 状态暂不可用：{e}）")

    st.divider()

    # ---------- 奶奶版一句话 ----------
    risk = advice.get("portfolio", {}).get("risk_level", "routine")
    dot = _RISK_COLOR.get(risk, "🟢")
    st.markdown(f"### {dot} 今日一句话")
    st.info(advice.get("headline_zh", ""))
    changes = advice.get("changes") or []
    if changes:
        chg_txt = "、".join(f"**{c['ticker']}** {c['from']}→{c['to']}" for c in changes[:6])
        st.markdown(f"📌 **较上次快照的变化:** {chg_txt}")

    # ---------- Agent 推理轨迹(透明度) ----------
    with st.expander("🧠 Agent 是怎么想出来的（推理轨迹）", expanded=False):
        p = advice.get("portfolio", {})
        temp = advice.get("market_temp", {})
        n_stocks = len(advice.get("stocks", []))
        n_att = len(advice.get("attention") or [])
        focus0 = (advice.get("focus") or [{}])[0]
        steps = [
            f"1️⃣ 读取行情+指标：共 {n_stocks} 只（RSI/MACD/52周位置/今日涨跌）"
            + (f"，其中 {advice['data_degraded']} 只用了兜底价" if advice.get("data_degraded") else ""),
            f"2️⃣ 判市场温度：{temp.get('zh','—')}",
            f"3️⃣ 每股规则裁决→动作标签，并按多因子打信念分（{n_stocks} 只）",
            f"4️⃣ 排序出今日聚焦 top-N"
            + (f"，第一名 {focus0.get('ticker','')}（{focus0.get('conviction',0):.0f} 分）" if focus0 else ""),
            f"5️⃣ 组合体检：科技占比 {p.get('tech_weight_pct',0):.0f}%"
            + ("，触发集中度提醒" if (p.get('concentration_flags')) else "，集中度正常"),
            f"6️⃣ 拉免费新闻做因果解读，挑出 {n_att} 只需要注意的",
            "7️⃣ 汇总成奶奶版一句话 + 今日简报（做什么/别做/盯什么）",
        ]
        for s in steps:
            st.markdown(s)
        st.caption("全程规则化、可复现；LLM 只做可选润色，不编造数字或新闻。")

    # ---------- 今日大事(为什么涨跌)----------
    big_events = advice.get("big_events") or []
    if big_events:
        st.markdown("#### 🌍 今日大事(为什么涨跌)")
        for e in big_events:
            with st.container(border=True):
                title = e.get("headline", "")
                url = e.get("url", "")
                src = e.get("source", "")
                head = f"**[{title}]({url})**" if url else f"**{title}**"
                st.markdown(head + (f"  ·{src}" if src else ""))
                st.markdown(e.get("cause_effect", ""))

    if advice.get("data_degraded"):
        st.caption(f"⚠️ 有 {advice['data_degraded']} 只暂时取不到完整行情,已用兜底价,判断偏保守。")

    # ---------- 🎯 今日聚焦(多因子信念排序) ----------
    focus = advice.get("focus") or []
    # If the user has already loaded guru holdings this session, fold the
    # smart-money factor into the ranking so 大佬持仓 actually moves the score.
    gurus_loaded = st.session_state.get("gurus_loaded")
    if gurus_loaded:
        try:
            from tasks import conviction as _conv  # noqa: PLC0415

            focus = _conv.build_focus(advice.get("stocks", []), gurus=gurus_loaded,
                                      news=advice.get("news"), top_n=5)
        except Exception:
            pass
    if focus:
        st.markdown("#### 🎯 今日聚焦")
        st.caption("agent 融合了技术信号、RSI、52周位置、动量、大佬持仓与新闻,给出今天最值得看的几只(0–100 信念分,越高越值得留意;非买卖指令)。")
        top3 = focus[:3]
        cols = st.columns(len(top3))
        for col, f in zip(cols, top3):
            with col:
                with st.container(border=True):
                    st.markdown(f"**{f['ticker']}** · {f['direction']}")
                    st.progress(min(1.0, f["conviction"] / 100.0), text=f"信念 {f['conviction']:.0f}/100")
                    st.caption(f"今日 {f.get('chg_pct',0):+.1f}%｜{f.get('action_zh','')}")
                    for fac in f.get("factors", [])[:3]:
                        arrow = "▲" if fac["delta"] >= 0 else "▼"
                        st.markdown(f"<small>{arrow} {fac['label']}：{fac['detail']}</small>", unsafe_allow_html=True)
        with st.expander("查看全部聚焦排序 + 一句话论点", expanded=False):
            for f in focus:
                st.markdown(f"- {f['thesis']}")
        # Factor attribution: turn the score into "why" for the #1 focus stock.
        lead = focus[0]
        if lead.get("factors"):
            st.markdown(f"##### 🔬 {lead['ticker']} 信念分拆解（为什么 {lead['conviction']:.0f} 分）")
            st.plotly_chart(native_chart.factor_bars(lead["factors"]),
                            use_container_width=True, config={"displayModeBar": False})

    st.divider()

    # ---------- Ask Agent 问答 ----------
    st.markdown("#### 💬 问问 Agent")
    holdings_now = [s["ticker"] for s in advice.get("stocks", [])]
    ask_opts = holdings_now + [s for s in _WATCH_EXTRA if s not in holdings_now]
    qc = st.columns([1.1, 2.4, 1])
    with qc[0]:
        ask_sym = st.selectbox("股票", ask_opts, key="ask_sym")
    with qc[1]:
        ask_q = st.text_input("想问什么", key="ask_q", placeholder="现在能买吗 / 跌了要加吗 / 帮我设提醒")
    with qc[2]:
        ask_llm = st.checkbox("AI润色", value=False, key="ask_llm", help="需已配置 LLM key")
    chips = st.columns(4)
    quick = None
    if chips[0].button("现在能买吗", use_container_width=True):
        quick = "现在能买吗"
    if chips[1].button("跌了要加吗", use_container_width=True):
        quick = "跌了要加吗"
    if chips[2].button("帮我设提醒", use_container_width=True):
        quick = "帮我设提醒"
    if chips[3].button("💬 问", use_container_width=True):
        quick = ask_q or "现在能买吗"
    if quick:
        ans = willow_agent.answer_question(ask_sym, quick, advice=advice, use_llm=ask_llm)
        st.session_state["ask_answer"] = ans
    ans = st.session_state.get("ask_answer")
    if ans:
        with st.container(border=True):
            st.markdown(f"**{ans['ticker']} · 奶奶版:** {ans['granny']}")
            st.markdown(f"**动作:** `{ans['action_zh']}`")
            if ans.get("reasons"):
                st.markdown("**原因:**")
                for r in ans["reasons"]:
                    if r:
                        st.markdown(f"- {r}")
            st.markdown(f"**如果真想买:** {ans['if_buy']}")
            if ans.get("watch_next"):
                st.caption(f"盯什么:{ans['watch_next']}")
            if ans.get("note"):
                st.warning(ans["note"])
            if ans.get("llm_text"):
                st.markdown("---")
                st.markdown(ans["llm_text"])

    st.divider()

    # ---------- 今日简报 + 组合状态 ----------
    left, right = st.columns([3, 2])
    with left:
        st.markdown("#### 📋 今日简报")
        ta = advice.get("today_actions", {})
        can = ta.get("可以做") or []
        dont = ta.get("先别做") or []
        watch = ta.get("盯着看") or []
        st.markdown("**✅ 今天可以做**")
        st.markdown("\n".join(f"- {x}" for x in can) if can else "- (今天没有明显的加仓机会)")
        st.markdown("**⛔ 今天先别做**")
        st.markdown("\n".join(f"- {x}" for x in dont) if dont else "- (无)")
        st.markdown("**👀 盯着看**")
        st.markdown("\n".join(f"- {x}" for x in watch) if watch else "- (无特别价位)")

    with right:
        st.markdown("#### 💰 组合状态")
        p = advice.get("portfolio", {})
        temp = advice.get("market_temp", {})
        c1, c2 = st.columns(2)
        c1.metric("总资产", f"${p.get('total_asset',0):,.0f}")
        c2.metric("现金", f"${p.get('cash',0):,.0f}")
        st.write(f"**市场温度:** {temp.get('zh','')}")
        if temp.get("note"):
            st.caption(temp["note"])
        flags = p.get("concentration_flags") or []
        if flags:
            st.warning("集中度提醒:" + "、".join(flags))
        else:
            st.caption("集中度正常。")

    st.divider()

    # ---------- 重点:大图 + 新手信号卡 ----------
    st.markdown("#### 📈 看图")
    holdings = [s["ticker"] for s in advice.get("stocks", [])]
    opts = holdings + [s for s in _WATCH_EXTRA if s not in holdings]
    if "home_chart" not in st.session_state:
        # 默认选最需要注意的那只
        att = advice.get("attention") or []
        st.session_state["home_chart"] = att[0]["ticker"] if att else (holdings[0] if holdings else "QQQ")
    sel = st.selectbox("选择股票", opts,
                       index=opts.index(st.session_state["home_chart"]) if st.session_state["home_chart"] in opts else 0)
    st.session_state["home_chart"] = sel

    big, card = st.columns([3, 1])
    with big:
        ok = native_chart.render(sel)
        if not ok:
            st.info("暂时取不到该股票的行情数据(可能离线或被限流),稍后再试。")
        with st.expander("🌐 交互式大图(TradingView,需联网)", expanded=False):
            _advanced(sel)
    with card:
        stock = next((s for s in advice.get("stocks", []) if s["ticker"] == sel), None)
        if stock:
            st.markdown(f"##### {sel} · 新手信号卡")
            st.markdown(f"**动作:** `{stock['action_zh']}`")
            st.caption(stock.get("reason_zh", ""))
            m1, m2 = st.columns(2)
            m1.metric("今日", f"{stock.get('chg_pct',0):+.1f}%")
            if stock.get("pnl_pct") is not None:
                m2.metric("我的盈亏", f"{stock['pnl_pct']:+.1f}%")
            rsi_v = stock.get("rsi")
            pos52 = stock.get("pos_52")
            st.write(f"RSI:{rsi_v:.0f}" if rsi_v is not None else "RSI: —")
            if pos52 is not None:
                st.write(f"52周位置:{pos52:.0f}%")
            st.markdown(f"**盯什么:** {stock.get('watch_next_zh','')}")
        else:
            st.markdown(f"##### {sel} · 买卖强弱表")
            _ta_gauge(sel)

    st.divider()

    # ---------- 提醒 + 新闻 ----------
    a_col, n_col = st.columns(2)
    with a_col:
        st.markdown("#### 🔔 价格提醒")
        active = pa.list_alarms(active_only=True)
        if active:
            for a in active:
                arrow = "≥" if a["direction"] == "above" else "≤"
                note = f" · {a['note']}" if str(a.get("note") or "").strip() else ""
                st.write(f"🔔 **{a['ticker']}** {arrow} ${float(a['target_price']):,.2f}{note}")
        else:
            st.caption("暂无生效中的提醒。")
        with st.expander("➕ 新建 / 检查提醒", expanded=False):
            with st.form("home_add_alarm", clear_on_submit=True):
                cc = st.columns([1.1, 1.1, 1])
                a_sym = cc[0].selectbox("股票", opts, key="home_alarm_sym")
                a_dir = cc[1].radio("方向", ["突破上方", "跌破下方"], key="home_alarm_dir")
                a_px = cc[2].number_input("目标价$", min_value=0.0, step=1.0, format="%.2f", key="home_alarm_px")
                a_note = st.text_input("备注(可选)", key="home_alarm_note")
                if st.form_submit_button("添加", use_container_width=True):
                    if a_px and a_px > 0:
                        pa.add_alarm(a_sym, "above" if a_dir == "突破上方" else "below", float(a_px), a_note)
                        st.success(f"已添加 {a_sym} {a_dir} ${a_px:,.2f}")
                        st.rerun()
                    else:
                        st.warning("请填写大于 0 的目标价。")
            if st.button("🔎 立即检查一次(命中即推送)"):
                hit = pa.check_alarms(send=True)
                st.success("已触发:" + "，".join(h["ticker"] for h in hit)) if hit else st.info("暂无触发。")

    with n_col:
        st.markdown("#### 📰 新闻雷达")
        news = advice.get("news", {})
        geo = news.get("geopolitics") or []
        macro = news.get("macro") or []
        chips = news.get("chips") or []
        market = news.get("market") or []
        hold_n = news.get("holdings") or []
        any_news = False
        if market:
            st.markdown("**📈 大盘**")
            for it in market[:2]:
                st.markdown("- " + _news_line(it))
            any_news = True
        if geo:
            st.markdown("**🛢️ 地缘 / 战争 / 油**")
            for it in geo[:2]:
                st.markdown("- " + _news_line(it))
            any_news = True
        if chips:
            st.markdown("**🔧 半导体 / AI**")
            for it in chips[:2]:
                st.markdown("- " + _news_line(it))
            any_news = True
        if macro:
            st.markdown("**🏦 宏观 / 美联储**")
            for it in macro[:2]:
                st.markdown("- " + _news_line(it))
            any_news = True
        if hold_n:
            with st.expander("📊 持仓相关新闻", expanded=False):
                for it in hold_n[:8]:
                    st.markdown(f"- **{it.get('ticker','')}** " + _news_line(it))
            any_news = True
        if not any_news:
            st.caption("暂时没抓到新闻(可能网络问题,稍后刷新)。")

    st.divider()

    # ---------- 持仓快照(只显需注意) ----------
    st.markdown("#### 🎯 需要注意的持仓")
    attention = advice.get("attention") or []
    if attention:
        import pandas as pd

        rows = [{
            "股票": s["ticker"],
            "动作": s["action_zh"],
            "今日": f"{s.get('chg_pct',0):+.1f}%",
            "我的盈亏": (f"{s['pnl_pct']:+.1f}%" if s.get("pnl_pct") is not None else "—"),
            "仓位": f"{s.get('weight_pct',0):.0f}%",
            "为什么": s.get("reason_zh", ""),
        } for s in attention]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.caption("今天没有需要特别处理的持仓,按计划即可。")

    with st.expander("展开全部持仓", expanded=False):
        import pandas as pd

        allrows = [{
            "股票": s["ticker"],
            "动作": s["action_zh"],
            "今日": f"{s.get('chg_pct',0):+.1f}%",
            "盈亏": (f"{s['pnl_pct']:+.1f}%" if s.get("pnl_pct") is not None else "—"),
            "仓位": f"{s.get('weight_pct',0):.0f}%",
            "RSI": (f"{s['rsi']:.0f}" if s.get("rsi") is not None else "—"),
        } for s in advice.get("stocks", [])]
        st.dataframe(pd.DataFrame(allrows), use_container_width=True, hide_index=True)

    st.divider()

    # ---------- 大佬持仓参考 ----------
    st.markdown("#### 👥 大佬持仓参考")
    st.caption(
        "看看知名投资人也持有你的哪些票(带占其组合 %)。仅供参考、不是买入信号;"
        "13F 为季度披露、约 45 天延迟,仅美股多头持仓。数据来源 Dataroma。"
    )
    if st.button("📥 加载 / 刷新大佬持仓(联网抓取,首次约 10 秒)"):
        st.session_state["home_load_gurus"] = True
        _gurus_cached.clear()
    if st.session_state.get("home_load_gurus"):
        with st.spinner("抓取 Dataroma 大佬持仓…"):
            gurus, overlap, insights = _gurus_cached(tuple(holdings))
        st.session_state["gurus_loaded"] = gurus  # feed the 🎯 今日聚焦 smart-money factor

        # --- 洞察:和我的持仓有什么关系 ---
        st.markdown("##### 🧠 对你的持仓有什么启发")
        lines = guru_holdings.insight_lines(insights)
        if lines:
            for ln in lines:
                st.markdown("- " + ln)
        else:
            st.caption("暂无足够重叠可分析。")

        # --- 环状图:选一位大佬看持仓环 + 你自己的持仓环 ---
        st.markdown("##### 🍩 持仓环状图")
        mine = {s["ticker"] for s in advice.get("stocks", [])}
        g_labels = [g["label"] for g in gurus if g.get("holdings")]
        gcol, mcol = st.columns(2)
        with gcol:
            pick = st.selectbox("看哪位大佬", g_labels, key="guru_pick")
            g = next((x for x in gurus if x["label"] == pick), None)
            if g and g.get("holdings"):
                items = [(h["symbol"], float(h["pct"])) for h in g["holdings"]]
                fig = native_chart.donut(items, title=f"{pick.split(' (')[0]} · {g.get('period','')}",
                                         highlight=mine)
                st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
                st.caption(f"组合规模 {g.get('portfolio_value','')}｜⭐=你也持有")
        with mcol:
            my_items = [(s["ticker"], float(s.get("weight_pct") or 0.0))
                        for s in advice.get("stocks", []) if (s.get("weight_pct") or 0) > 0]
            if my_items:
                gset = {str(h.get("symbol", "")).upper() for h in (g.get("holdings") if g else [])}
                figm = native_chart.donut(my_items, title="你的持仓", highlight=gset)
                st.plotly_chart(figm, use_container_width=True, config={"displayModeBar": False})
                st.caption("⭐=这位大佬也持有的票")

        # --- 重叠明细(可展开) ---
        with st.expander("📋 重叠明细 + 各位大佬前 10 大持仓", expanded=False):
            import pandas as pd

            if overlap:
                ov_rows = []
                for tk in sorted(overlap.keys()):
                    who = "、".join(f"{h['label'].split(' (')[0]} {h['pct']:.1f}%" for h in overlap[tk][:4])
                    ov_rows.append({"你的票": tk, "持有的大佬(占其组合%)": who})
                st.markdown("**和你重叠的持仓:**")
                st.dataframe(pd.DataFrame(ov_rows), use_container_width=True, hide_index=True)
            for gg in gurus:
                hs = gg.get("holdings") or []
                if not hs:
                    continue
                st.markdown(f"**{gg['label']} · {gg.get('period','')} · {gg.get('portfolio_value','')}**")
                rows = [{"股票": h["symbol"] + ("  ⭐" if h["symbol"] in mine else ""),
                         "占比": f"{h['pct']:.1f}%", "名称": h.get("name", "")} for h in hs]
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.caption("点上面的按钮加载(结果会缓存 12 小时,不会反复抓取)。")

    st.caption("以上为规则化提示,不构成投资建议;请在券商 App 自行判断下单。")

    # ---------- 我的规则与记忆 ----------
    with st.expander("⚙️ 我的规则与记忆(阶段5)", expanded=False):
        rules = wm.get_rules()
        st.markdown("**我的风险规则**(agent 会按这些来提醒集中度、控制推送频率)")
        rc = st.columns(3)
        v_single = rc[0].number_input("单票占比上限 %", min_value=5.0, max_value=60.0,
                                      value=float(rules["max_single_name_pct"]), step=1.0)
        v_tech = rc[1].number_input("科技敞口上限 %", min_value=20.0, max_value=100.0,
                                    value=float(rules["max_tech_pct"]), step=1.0)
        v_cd = rc[2].number_input("推送冷却(小时)", min_value=0.0, max_value=48.0,
                                  value=float(rules["cooldown_hours"]), step=1.0)
        dc = st.columns(2)
        v_qqq = dc[0].number_input("QQQ 每周定投 $", min_value=0.0, value=float(rules["dca_qqq_usd"]), step=10.0)
        v_voo = dc[1].number_input("VOO 每周定投 $", min_value=0.0, value=float(rules["dca_voo_usd"]), step=10.0)
        if st.button("💾 保存规则"):
            wm.set_rule("max_single_name_pct", v_single)
            wm.set_rule("max_tech_pct", v_tech)
            wm.set_rule("cooldown_hours", v_cd)
            wm.set_rule("dca_qqq_usd", v_qqq)
            wm.set_rule("dca_voo_usd", v_voo)
            _advice_cached.clear()
            st.success("已保存,下次简报按新规则来。")
            st.rerun()

        st.markdown("**最近的简报快照**")
        runs = wm.recent_runs(8)
        if runs:
            import pandas as pd

            rows = [{
                "时间": str(r.get("run_at", ""))[:16],
                "触发": r.get("trigger", ""),
                "一句话": (r.get("headline", "") or "")[:40],
            } for r in runs]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.caption("还没有快照。点右上角「📸 记录快照」存第一条,之后就能看到较上次的变化。")
