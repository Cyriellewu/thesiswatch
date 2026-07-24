import type { Holding } from "../types";
import { ConvictionDelta, Coverage, DriverChip, StatusPill } from "./primitives";

/** Today Focus Card. Tap body → Stock Thesis; "Why changed" → sheet; source count → Evidence. */
export function FocusCard({
  h,
  onOpenThesis,
  onWhyChanged,
  onEvidence,
}: {
  h: Holding;
  onOpenThesis: (t: string) => void;
  onWhyChanged: (t: string) => void;
  onEvidence: (t: string) => void;
}) {
  const drivers: { label: string; points: number; sign: any }[] = ((h as any).drivers ?? []).slice(0, 3);
  const oneLiner =
    h.status === "re_evaluate" ? "Thesis assumption in question"
    : h.status === "watch" ? "Thesis strengthened"
    : "No material change";

  return (
    <div className="bg-surface rounded-card shadow-card overflow-hidden">
      <button onClick={() => onOpenThesis(h.ticker)} className="w-full text-left p-4 pb-3">
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
      </button>

      <div className="flex items-stretch border-t" style={{ borderColor: "var(--hairline)" }}>
        <button
          onClick={() => onWhyChanged(h.ticker)}
          className="flex-1 py-2.5 text-[14px] font-medium min-h-[44px]"
          style={{ color: "var(--text)" }}
        >
          Why changed
        </button>
        <div className="w-px" style={{ background: "var(--hairline)" }} />
        <button
          onClick={() => onEvidence(h.ticker)}
          className="flex-1 py-2.5 text-[14px] text-secondary min-h-[44px]"
        >
          Sources
        </button>
      </div>
    </div>
  );
}
