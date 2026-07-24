import { useEffect, useState } from "react";
import { Sheet } from "./Sheet";
import { TypeTag } from "./primitives";
import { conflictsByTicker } from "../data/mock";
import { fetchEvidence } from "../data/api";
import type { Evidence } from "../types";

/** Evidence sheet: fixed per-item structure; conflicts and staleness shown, never hidden. */
export function EvidenceSheet({ ticker, onClose }: { ticker: string | null; onClose: () => void }) {
  const [items, setItems] = useState<Evidence[]>([]);
  useEffect(() => {
    let live = true;
    if (!ticker) { setItems([]); return; }
    fetchEvidence(ticker).then((d) => { if (live) setItems(d); });
    return () => { live = false; };
  }, [ticker]);
  const conflicts = ticker ? conflictsByTicker[ticker] ?? [] : [];
  return (
    <Sheet open={!!ticker} title={ticker ? `${ticker} evidence` : ""} onClose={onClose}>
      <div className="pb-4 space-y-3">
        {conflicts.map((c) => (
          <div
            key={c.thesisClaimId}
            className="rounded-card p-3 text-[13px]"
            style={{ background: "var(--reeval-bg)", color: "var(--reeval)" }}
          >
            <div className="font-semibold">⚠ Evidence conflict detected</div>
            <div className="tnum mt-0.5">
              {c.positive} source{c.positive !== 1 ? "s" : ""} positive · {c.negative} negative
            </div>
            {c.unresolvedReason && <div className="mt-1 opacity-90">{c.unresolvedReason}</div>}
          </div>
        ))}
        {items.map((e) => (
          <EvidenceCard key={e.id} e={e} />
        ))}
      </div>
    </Sheet>
  );
}

function EvidenceCard({ e }: { e: Evidence }) {
  const stale = e.freshness === "stale" || e.freshness === "unavailable";
  return (
    <div className="rounded-card bg-surface-2 p-4">
      <div className="flex items-start justify-between gap-3">
        <p className="text-[15px] font-medium leading-snug">{e.claim}</p>
        <TypeTag type={e.type} />
      </div>

      <div className="mt-2 text-[12px] text-secondary">
        {e.sourceUrl ? (
          <a
            href={e.sourceUrl}
            target="_blank"
            rel="noreferrer"
            className="text-primary underline underline-offset-2 inline-flex items-center min-h-[44px]"
            style={{ color: "var(--text)" }}
          >
            {e.sourceName} ↗
          </a>
        ) : (
          <span className="text-primary">{e.sourceName}</span>
        )}
        <span className="text-tertiary"> · </span>
        Observed {fmtDate(e.observedAt)}
        <span className="text-tertiary"> · </span>
        Fetched {relAge(e.fetchedAt)}
      </div>

      <p className="mt-2 text-[13px] leading-relaxed">
        <span className="text-tertiary">Agent interpretation: </span>
        {e.interpretation}
      </p>

      <div className="mt-2 flex items-center gap-2 text-[12px]">
        <span className="text-secondary">Confidence: <span className="text-primary font-medium">{e.confidence}</span></span>
        {e.freshness === "conflicting" && (
          <span style={{ color: "var(--reeval)" }}>· conflicting</span>
        )}
        {stale && <span style={{ color: "var(--watch)" }}>· stale</span>}
      </div>

      {e.qualityNote && (
        <div
          className="mt-2 rounded-lg px-2.5 py-1.5 text-[12px]"
          style={{ background: "var(--watch-bg)", color: "var(--watch)" }}
        >
          {e.qualityNote}
        </div>
      )}
    </div>
  );
}

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}
function relAge(iso: string): string {
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (mins < 60) return `${Math.max(1, mins)} min ago`;
  const h = Math.round(mins / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.round(h / 24)}d ago`;
}
