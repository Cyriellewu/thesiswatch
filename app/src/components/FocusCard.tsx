import type { DriverSign, Holding } from "../types";
import { ConvictionDelta, Coverage, DriverChip, StatusPill } from "./primitives";

type FocusDriver = { label: string; points: number; sign: DriverSign };

export function FocusCard({
  h,
  onOpenThesis,
  selected,
}: {
  h: Holding;
  onOpenThesis: (t: string, prevConviction?: number) => void;
  selected?: boolean;
}) {
  const drivers = (((h as Holding & { drivers?: FocusDriver[] }).drivers) ?? []).slice(0, 3);
  const oneLiner =
    h.status === "re_evaluate" ? "Thesis assumption in question"
    : h.status === "watch" ? "Thesis strengthened"
    : "No material change";

  return (
    <button
      onClick={() => onOpenThesis(h.ticker, h.prevConviction)}
      className={`w-full bg-surface rounded-card shadow-card overflow-hidden text-left p-4 transition-colors ${selected ? "ring-1" : ""}`}
      style={{
        borderColor: selected ? "var(--watch)" : "transparent",
        boxShadow: selected ? "inset 0 0 0 1px var(--watch)" : undefined,
      }}
    >
        <div className="flex items-start justify-between">
          <div>
            <div className="flex items-center gap-2">
              <span className="text-[17px] font-bold">{h.ticker}</span>
              {h.dataState === "stale_data" && (
                <span className="text-[10px]" style={{ color: "var(--watch)" }}>stale</span>
              )}
            </div>
            <div className="text-[13px] text-secondary">{oneLiner}</div>
          </div>
          <div className="text-right">
            <ConvictionDelta from={h.prevConviction} to={h.conviction} />
            <div className="mt-1"><StatusPill status={h.status} size="sm" /></div>
          </div>
        </div>

        {drivers.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-1.5">
            {drivers.map((d) => (
              <DriverChip key={d.label} label={d.label} points={d.points} sign={d.sign} />
            ))}
          </div>
        )}

        <div className="mt-3">
          <Coverage pct={h.coveragePct} confidence={h.confidence} />
        </div>
        <div className="mt-4 pt-3 border-t text-[13px] font-medium" style={{ borderColor: "var(--hairline)", color: "var(--text-2)" }}>
          View thesis ›
        </div>
    </button>
  );
}
