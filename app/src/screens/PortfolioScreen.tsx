import { useEffect, useState } from "react";
import { fetchExposures } from "../data/api";
import { getMode, setMode, type UiMode } from "../data/mode";
import type { ExposureLevel, PortfolioExposure } from "../types";

const LEVEL_META: Record<ExposureLevel, { label: string; fg: string; bg: string; w: string }> = {
  high: { label: "High", fg: "var(--reeval)", bg: "var(--reeval-bg)", w: "92%" },
  medium: { label: "Medium", fg: "var(--watch)", bg: "var(--watch-bg)", w: "58%" },
  low: { label: "Low", fg: "var(--neutral)", bg: "var(--calm-bg)", w: "28%" },
};

export function PortfolioScreen() {
  const [exposures, setExposures] = useState<PortfolioExposure[] | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const mode = getMode();
  const isDemo = mode === "demo";

  useEffect(() => {
    let live = true;
    fetchExposures().then((d) => {
      if (!live) return;
      setExposures(d);
      setOpen(d.length > 0 ? d[0].factor : null);
    });
    return () => { live = false; };
  }, []);

  return (
    <div className="px-5 lg:px-8 pt-6 pb-12">
      <div className="flex items-center gap-2 mb-3">
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
          onClick={() => {
            const next: UiMode = isDemo ? "live" : "demo";
            setMode(next);
            window.location.reload();
          }}
          className="text-[12px] text-secondary underline underline-offset-2 min-h-[44px]"
        >
          {isDemo ? "Switch to Live" : "Switch to Demo"}
        </button>
      </div>
      <h1 className="text-[26px] lg:text-[32px] font-bold">Portfolio</h1>
      <p className="text-secondary text-[14px] mt-1 leading-relaxed">
        Looks diversified — but what shared risks are you actually betting on?
      </p>

      <div className="lg:grid lg:grid-cols-2 lg:gap-8 lg:items-start">
        <section>
          <h2 className="text-[13px] font-semibold text-secondary uppercase tracking-wide mt-6 mb-2.5">
            Hidden exposures
          </h2>
          {exposures === null ? (
            <p className="text-secondary text-[15px]">Loading exposure data…</p>
          ) : exposures.length === 0 ? (
            <p className="text-secondary text-[15px]">Exposure data unavailable</p>
          ) : (
            <div className="space-y-2">
              {exposures.map((ex) => {
                const m = LEVEL_META[ex.level];
                const isOpen = open === ex.factor;
                return (
                  <div key={ex.factor} className="bg-surface rounded-card overflow-hidden">
                    <button
                      onClick={() => setOpen(isOpen ? null : ex.factor)}
                      className="w-full flex items-center justify-between px-4 py-3 min-h-[44px]"
                    >
                      <span className="text-[15px] font-medium text-left">{ex.factor}</span>
                      <span className="flex items-center gap-2">
                        <span
                          className="rounded-chip px-2 py-0.5 text-[11px] font-semibold"
                          style={{ color: m.fg, background: m.bg }}
                        >
                          {m.label}
                        </span>
                        <span className="text-tertiary text-[12px]">{isOpen ? "▲" : "▼"}</span>
                      </span>
                    </button>
                    {isOpen && (
                      <div className="px-4 pb-3 animate-fade-in">
                        {ex.holdings.map((h) => (
                          <div key={h.ticker} className="flex items-center gap-3 py-1">
                            <span className="text-[13px] font-medium w-14">{h.ticker}</span>
                            <div className="flex-1 h-2 rounded-chip" style={{ background: "var(--surface-2)" }}>
                              <div
                                className="h-2 rounded-chip"
                                style={{ width: `${h.weightPct * 3}%`, background: m.fg, opacity: 0.85 }}
                              />
                            </div>
                            <span className="tnum text-[13px] text-secondary w-10 text-right">{h.weightPct}%</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
