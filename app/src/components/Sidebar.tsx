import type { Tab } from "./BottomNav";

const NAV: { id: Tab; label: string; icon: string; hint: string }[] = [
  { id: "today", label: "Today", icon: "◎", hint: "今天要不要管" },
  { id: "portfolio", label: "Portfolio", icon: "◧", hint: "隐藏风险敞口" },
  { id: "watchlist", label: "Watchlist", icon: "☆", hint: "全部持仓" },
];

/** Desktop left sidebar. Replaces the bottom tab bar on wide screens. */
export function Sidebar({
  tab,
  onChange,
  onAsk,
}: {
  tab: Tab;
  onChange: (t: Tab) => void;
  onAsk: () => void;
}) {
  return (
    <aside className="hidden lg:flex flex-col w-[248px] shrink-0 h-screen sticky top-0 border-r border-hairline bg-surface/60 px-4 py-6">
      <div className="px-2">
        <div className="text-[19px] font-bold tracking-tight">ThesisWatch</div>
        <div className="text-[12px] text-secondary mt-0.5">论点变化监测 · 不是买卖评分</div>
      </div>

      <nav className="mt-8 space-y-1">
        {NAV.map((n) => {
          const active = n.id === tab;
          return (
            <button
              key={n.id}
              onClick={() => onChange(n.id)}
              aria-current={active ? "page" : undefined}
              className="w-full flex items-center gap-3 rounded-card px-3 py-2.5 text-left transition-colors"
              style={{
                background: active ? "var(--surface-2)" : "transparent",
                color: active ? "var(--text)" : "var(--text-2)",
              }}
            >
              <span className="text-[18px] leading-none w-5 text-center">{n.icon}</span>
              <span className="flex-1">
                <span className="text-[15px] font-semibold block leading-tight">{n.label}</span>
                <span className="text-[11px] text-tertiary">{n.hint}</span>
              </span>
            </button>
          );
        })}
      </nav>

      <div className="mt-auto">
        <button
          onClick={onAsk}
          className="w-full rounded-card py-3 text-[15px] font-semibold"
          style={{ background: "var(--text)", color: "var(--surface)" }}
        >
          Ask Alpha
        </button>
        <p className="text-[11px] text-tertiary mt-3 px-1 leading-relaxed">
          Not financial advice. Local-first · $0/month.
        </p>
      </div>
    </aside>
  );
}
