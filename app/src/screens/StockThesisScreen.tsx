import { useEffect, useState } from "react";
import { Coverage, DriverChip, StatusPill, TypeTag } from "../components/primitives";
import { fetchThesis } from "../data/api";
import type { Evidence, StockThesis } from "../types";

export function StockThesisScreen({
  ticker,
  onBack,
  onEvidence,
}: {
  ticker: string;
  onBack: () => void;
  onEvidence: (t: string) => void;
}) {
  const [t, setT] = useState<StockThesis | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let live = true;
    setLoading(true);
    fetchThesis(ticker).then((d) => { if (live) { setT(d); setLoading(false); } });
    return () => { live = false; };
  }, [ticker]);
  if (!t) {
    return (
      <div className="px-5 pt-4">
        <button onClick={onBack} className="text-secondary text-[15px] min-h-[44px]">‹ Today</button>
        <p className="text-secondary mt-8">
          {loading ? `Loading ${ticker}…` : `No detailed thesis for ${ticker} in this prototype.`}
        </p>
      </div>
    );
  }
  const risks: Evidence[] = (t as any).risks ?? [];
  const supporting: Evidence[] = (t as any).supporting ?? [];

  return (
    <div className="pb-8">
      {/* sticky header */}
      <div className="sticky top-0 z-10 bg-surface/95 backdrop-blur px-5 pt-3 pb-3 border-b border-hairline">
        <button onClick={onBack} className="text-secondary text-[15px] min-h-[44px] flex items-center">‹ Today</button>
        <div className="flex items-end justify-between mt-1">
          <div>
            <div className="text-[22px] font-bold">{t.ticker}</div>
            <div className="mt-1"><StatusPill status={t.status} size="sm" /></div>
            <div className="text-[13px] text-secondary mt-1">{t.oneLiner}</div>
          </div>
          <div className="text-right">
            <div className="tnum text-[34px] font-bold leading-none">{t.conviction}</div>
            <div className="mt-1"><Coverage pct={t.coveragePct} confidence={t.confidence} /></div>
          </div>
        </div>
      </div>

      <div className="px-5">
        <Block title="What changed">
          <TimeRow label="Latest" drivers={t.whatChanged} />
        </Block>

        <Block title="Current thesis">
          <ul className="space-y-2">
            {t.currentThesis.map((c) => (
              <li key={c.id} className="text-[15px] leading-relaxed flex gap-2">
                <span className="text-tertiary">•</span>
                {c.text}
              </li>
            ))}
          </ul>
        </Block>

        <Block title="Supporting evidence" action={() => onEvidence(ticker)} actionLabel="All sources">
          {supporting.map((e) => <EvidenceMini key={e.id} e={e} />)}
        </Block>

        {/* Risks are NEVER buried */}
        <Block title="Risks & counterarguments">
          {risks.length === 0 && <p className="text-secondary text-[14px]">None flagged.</p>}
          {risks.map((e) => <EvidenceMini key={e.id} e={e} />)}
        </Block>

        <Block title="What would change my mind">
          <ul className="space-y-2">
            {t.whatWouldChangeMyMind.map((w) => (
              <li key={w} className="text-[14px] leading-relaxed flex gap-2" style={{ color: "var(--reeval)" }}>
                <span>✕</span>
                <span className="text-primary">{w}</span>
              </li>
            ))}
          </ul>
        </Block>

        <Block title="Catalysts">
          {t.catalysts.map((c) => (
            <div key={c.label} className="flex justify-between py-1.5 text-[14px]">
              <span>{c.label}</span>
              <span className="tnum text-secondary">{fmtDate(c.date)}</span>
            </div>
          ))}
        </Block>

        <Block title="Decision history">
          {t.decisionHistory.map((d) => (
            <div key={d.date} className="flex items-center justify-between py-1.5 text-[14px]">
              <span className="tnum text-secondary">{fmtDate(d.date)}</span>
              <span className="tnum font-medium" style={{ color: d.to >= d.from ? "var(--pos)" : "var(--neg)" }}>
                {d.from} → {d.to}
              </span>
              <span className="text-secondary flex-1 text-right ml-3">{d.note}</span>
            </div>
          ))}
        </Block>
      </div>
    </div>
  );
}

function Block({
  title, children, action, actionLabel,
}: { title: string; children: React.ReactNode; action?: () => void; actionLabel?: string }) {
  return (
    <section className="mt-6">
      <div className="flex items-center justify-between mb-2.5">
        <h2 className="text-[13px] font-semibold text-secondary uppercase tracking-wide">{title}</h2>
        {action && (
          <button onClick={action} className="text-[13px] text-secondary min-h-[44px] flex items-center">{actionLabel} ›</button>
        )}
      </div>
      <div className="bg-surface rounded-card p-4">{children}</div>
    </section>
  );
}

function TimeRow({ label, drivers }: { label: string; drivers: { label: string; points: number; sign: any }[] }) {
  return (
    <div className="py-1.5">
      <div className="text-[12px] text-tertiary mb-1.5">{label}</div>
      <div className="flex flex-wrap gap-1.5">
        {drivers.map((d) => <DriverChip key={d.label} label={d.label} points={d.points} sign={d.sign} />)}
      </div>
    </div>
  );
}

function EvidenceMini({ e }: { e: Evidence }) {
  return (
    <div className="py-2 border-b last:border-0" style={{ borderColor: "var(--hairline)" }}>
      <div className="flex items-start justify-between gap-2">
        <p className="text-[14px] leading-snug">{e.claim}</p>
        <TypeTag type={e.type} />
      </div>
      <div className="text-[12px] text-tertiary mt-1">
        {e.sourceName}
        {(e.freshness === "stale" || e.freshness === "conflicting") && (
          <span style={{ color: "var(--watch)" }}> · {e.freshness}</span>
        )}
      </div>
    </div>
  );
}

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}
