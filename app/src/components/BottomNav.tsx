export type Tab = "today" | "portfolio" | "watchlist";

const TABS: { id: Tab; label: string; icon: string }[] = [
  { id: "today", label: "Today", icon: "◎" },
  { id: "portfolio", label: "Portfolio", icon: "◧" },
  { id: "watchlist", label: "Watchlist", icon: "☆" },
];

export function BottomNav({ tab, onChange }: { tab: Tab; onChange: (t: Tab) => void }) {
  return (
    <nav
      className="sticky bottom-0 bg-surface/95 backdrop-blur border-t border-hairline pb-safe"
      style={{ WebkitBackdropFilter: "blur(12px)" }}
    >
      <div className="flex items-stretch justify-around px-2 pt-1.5">
        {TABS.map((t) => {
          const active = t.id === tab;
          return (
            <button
              key={t.id}
              onClick={() => onChange(t.id)}
              className="flex-1 flex flex-col items-center gap-0.5 py-1.5 min-h-[44px]"
              aria-current={active ? "page" : undefined}
            >
              <span
                className="text-[19px] leading-none"
                style={{ color: active ? "var(--text)" : "var(--text-3)" }}
              >
                {t.icon}
              </span>
              <span
                className="text-[10px] font-medium"
                style={{ color: active ? "var(--text)" : "var(--text-3)" }}
              >
                {t.label}
              </span>
            </button>
          );
        })}
      </div>
    </nav>
  );
}
