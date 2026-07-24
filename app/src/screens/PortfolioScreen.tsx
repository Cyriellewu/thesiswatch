import { useEffect, useState } from "react";
import { exposures as mockExposures } from "../data/mock";
import { fetchExposures } from "../data/api";
import type { ExposureLevel, PortfolioExposure } from "../types";

const LEVEL_META: Record<ExposureLevel, { label: string; fg: string; bg: string; w: string }> = {
  high: { label: "High", fg: "var(--reeval)", bg: "var(--reeval-bg)", w: "92%" },
  medium: { label: "Medium", fg: "var(--watch)", bg: "var(--watch-bg)", w: "58%" },
  low: { label: "Low", fg: "var(--neutral)", bg: "var(--calm-bg)", w: "28%" },
};

const WHATIF = [
  { id: "wi-1", label: "Add $500 QQQ", mega: [47, 50], semi: [29, 27], cash: [8, 6] },
  { id: "wi-2", label: "Reduce NVDA 10%", mega: [47, 46], semi: [29, 26], cash: [8, 10] },
  { id: "wi-3", label: "Keep as-is", mega: [47, 47], semi: [29, 29], cash: [8, 8] },
];

export function PortfolioScreen() {
  const [exposures, setExposures] = useState<PortfolioExposure[]>(mockExposures);
  const [open, setOpen] = useState<string | null>("AI infrastructure");
  const [sim, setSim] = useState(WHATIF[0]);

  useEffect(() => {
    let live = true;
    fetchExposures().then((d) => {
      if (!live || !d.length) return;
      setExposures(d);
      setOpen(d[0]?.factor ?? null);
    });
    return () => { live = false; };
  }, []);

  return (
    <div className="px-5 lg:px-8 pt-6 pb-12">
      <h1 className="text-[26px] lg:text-[32px] font-bold">Portfolio</h1>
      <p className="text-secondary text-[14px] mt-1 leading-relaxed">
        Looks diversified — but what shared risks are you actually betting on?
      </p>

      <div className="lg:grid lg:grid-cols-2 lg:gap-8 lg:items-start">
      <section>
      <h2 className="text-[13px] font-semibold text-secondary uppercase tracking-wide mt-6 mb-2.5">
        Hidden exposures
      </h2>
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
      </section>

      <section>
      <h2 className="text-[13px] font-semibold text-secondary uppercase tracking-wide mt-7 lg:mt-6 mb-2.5">
        What-if simulator
      </h2>
      <div className="flex gap-2 flex-wrap">
        {WHATIF.map((w) => (
          <button
            key={w.id}
            onClick={() => setSim(w)}
            className="rounded-chip px-3 py-2 text-[13px] font-medium min-h-[44px]"
            style={{
              background: sim.id === w.id ? "var(--text)" : "var(--surface)",
              color: sim.id === w.id ? "var(--surface)" : "var(--text)",
            }}
          >
            {w.label}
          </button>
        ))}
      </div>

      <div className="bg-surface rounded-card p-4 mt-3 space-y-1">
        <SimRow label="Mega-cap concentration" a={sim.mega[0]} b={sim.mega[1]} />
        <SimRow label="Semiconductor exposure" a={sim.semi[0]} b={sim.semi[1]} />
        <SimRow label="Cash allocation" a={sim.cash[0]} b={sim.cash[1]} />
      </div>
      <p className="text-tertiary text-[12px] mt-3">Decision simulation only — no orders are placed.</p>
      </section>
      </div>
    </div>
  );
}

function SimRow({ label, a, b }: { label: string; a: number; b: number }) {
  const col = b === a ? "var(--neutral)" : b > a ? "var(--neg)" : "var(--pos)";
  return (
    <div className="flex items-center justify-between py-1.5">
      <span className="text-[14px]">{label}</span>
      <span className="tnum text-[14px] font-medium">
        <span className="text-secondary">{a}%</span>
        <span className="text-tertiary"> → </span>
        <span style={{ color: col }}>{b}%</span>
      </span>
    </div>
  );
}
