import { useEffect, useState } from "react";
import { Sheet } from "./Sheet";
import { ConvictionDelta, StatusPill } from "./primitives";
import { fetchWhy } from "../data/api";
import type { WhyChanged } from "../types";

/** Why Changed: conclusion → conviction change → interpretation → (sources link). */
export function WhyChangedSheet({
  ticker,
  onClose,
  onOpenEvidence,
}: {
  ticker: string | null;
  onClose: () => void;
  onOpenEvidence: (t: string) => void;
}) {
  const [wc, setWc] = useState<WhyChanged | null>(null);
  useEffect(() => {
    let live = true;
    if (!ticker) { setWc(null); return; }
    fetchWhy(ticker).then((d) => { if (live) setWc(d); });
    return () => { live = false; };
  }, [ticker]);
  return (
    <Sheet open={!!wc} title={wc ? `Why ${wc.ticker} changed` : ""} onClose={onClose}>
      {wc && (
        <div className="pb-4">
          <div className="flex items-center justify-between">
            <StatusPill status={wc.status} />
            <ConvictionDelta from={wc.prevConviction} to={wc.currentConviction} />
          </div>

          {/* conviction breakdown ledger — driver rows open evidence */}
          <div className="mt-4 bg-surface-2 rounded-card p-4">
            <Row label="Previous conviction" value={`${wc.prevConviction}`} muted />
            {wc.drivers.map((d) => (
              <button
                key={d.label}
                onClick={() => onOpenEvidence(wc.ticker)}
                className="w-full flex items-center justify-between py-1.5 min-h-[44px] text-left"
              >
                <span className="text-[14px] flex items-center gap-1.5">
                  {d.label}
                  <span className="text-tertiary text-[11px]">›</span>
                </span>
                <span
                  className="tnum text-[14px] font-semibold"
                  style={{
                    color:
                      d.sign === "positive" ? "var(--pos)" : d.sign === "negative" ? "var(--neg)" : undefined,
                  }}
                >
                  {d.points > 0 ? `+${d.points}` : `${d.points}`}
                </span>
              </button>
            ))}
            <div className="my-2 h-px" style={{ background: "var(--hairline)" }} />
            <Row label="Current conviction" value={`${wc.currentConviction}`} bold />
          </div>

          {/* what this means for you */}
          <h3 className="mt-5 text-[13px] font-semibold text-secondary uppercase tracking-wide">
            What this means for you
          </h3>
          <p className="mt-1.5 text-[15px] leading-relaxed">{wc.meaningForYou}</p>

          <button
            onClick={() => onOpenEvidence(wc.ticker)}
            className="mt-5 w-full rounded-card bg-surface-2 py-3 text-[15px] font-medium min-h-[44px]"
          >
            View evidence →
          </button>
          <p className="mt-3 text-[12px] text-tertiary">
            Updated {relAge(wc.asOf)} · conclusion first, evidence last
          </p>
        </div>
      )}
    </Sheet>
  );
}

function Row({
  label, value, muted, bold, color,
}: { label: string; value: string; muted?: boolean; bold?: boolean; color?: string }) {
  return (
    <div className="flex items-center justify-between py-1.5">
      <span className={`text-[14px] ${muted ? "text-secondary" : ""} ${bold ? "font-semibold" : ""}`}>
        {label}
      </span>
      <span className="tnum text-[14px] font-semibold" style={{ color }}>
        {value}
      </span>
    </div>
  );
}

function relAge(iso: string): string {
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (mins < 60) return `${Math.max(1, mins)} min ago`;
  const h = Math.round(mins / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.round(h / 24)}d ago`;
}
