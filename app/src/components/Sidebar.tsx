import { useState } from "react";
import type { Tab } from "./BottomNav";
import { getMode, setMode, type UiMode } from "../data/mode";

const NAV: { id: Tab; label: string; icon: string; hint: string }[] = [
  { id: "today", label: "Today", icon: "◎", hint: "今天要不要管" },
  { id: "portfolio", label: "Portfolio", icon: "◧", hint: "隐藏风险敞口" },
];

/** Desktop left sidebar. Replaces the bottom tab bar on wide screens. */
export function Sidebar({
  tab,
  onChange,
}: {
  tab: Tab;
  onChange: (t: Tab) => void;
}) {
  const [mode] = useState<UiMode>(getMode());
  const isDemo = mode === "demo";

  const toggleMode = () => {
    const next: UiMode = isDemo ? "live" : "demo";
    setMode(next);
    window.location.reload();
  };

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
        <div className="px-1">
          <div className="flex items-center gap-2">
            <span
              className="rounded-chip px-2 py-0.5 text-[11px] font-semibold"
              style={{
                background: isDemo ? "var(--watch-bg)" : "var(--calm-bg)",
                color: isDemo ? "var(--watch)" : "var(--neutral)",
              }}
            >
              {isDemo ? "DEMO DATA" : "LIVE"}
            </span>
            <button
              onClick={toggleMode}
              className="text-[12px] text-secondary underline underline-offset-2 min-h-[44px]"
            >
              {isDemo ? "Switch to Live" : "Switch to Demo"}
            </button>
          </div>
        </div>
        <p className="text-[11px] text-tertiary mt-3 px-1 leading-relaxed">
          Not financial advice. Local-first · $0/month.
        </p>
      </div>
    </aside>
  );
}
