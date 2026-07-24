import { useEffect, useState, type ReactNode } from "react";
import { ConvictionDelta, Coverage, DriverChip, StatusPill, TypeTag } from "../components/primitives";
import { fetchThesisEnvelope, fetchWhyEnvelope } from "../data/api";
import type { ApiEnvelope, Evidence, StockThesis, WhyChanged } from "../types";

type Load =
  | { status: "loading" }
  | { status: "done"; thesisEnv: ApiEnvelope<StockThesis>; whyEnv: ApiEnvelope<WhyChanged> };

type Period = "d1" | "w1" | "m1";

const PERIODS: Array<{ id: Period; label: string }> = [
  { id: "d1", label: "Today" },
  { id: "w1", label: "Week" },
  { id: "m1", label: "Month" },
];

export function ThesisDetail({
  ticker,
  prevConviction,
  onBack,
}: {
  ticker: string;
  prevConviction?: number;
  onBack: () => void;
}) {
  const [period, setPeriod] = useState<Period>("d1");
  const [expandedClaimIds, setExpandedClaimIds] = useState<string[]>([]);
  const [load, setLoad] = useState<Load>({ status: "loading" });
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let alive = true;
    setExpandedClaimIds([]);
    setLoad({ status: "loading" });
    Promise.all([fetchThesisEnvelope(ticker), fetchWhyEnvelope(ticker)]).then(([thesisEnv, whyEnv]) => {
      if (alive) setLoad({ status: "done", thesisEnv, whyEnv });
    });
    return () => {
      alive = false;
    };
  }, [ticker, reloadKey]);

  if (load.status === "loading") {
    return <LoadingState ticker={ticker} onBack={onBack} />;
  }

  const { thesisEnv, whyEnv } = load;
  if (thesisEnv.state === "unavailable" || !thesisEnv.data) {
    return (
      <UnavailableState
        ticker={ticker}
        warning={thesisEnv.warnings[0]}
        onBack={onBack}
        onRetry={() => setReloadKey((v) => v + 1)}
      />
    );
  }

  const thesis = thesisEnv.data;
  const why = whyEnv.data;
  const evidenceItems = [...thesis.supporting, ...thesis.risks];
  const headerPrev = prevConviction ?? why?.prevConviction ?? thesis.decisionHistory[0]?.from ?? thesis.conviction;
  const bannerWarnings: string[] = [];
  if (thesisEnv.state === "stale" || thesisEnv.state === "partial") bannerWarnings.push(...thesisEnv.warnings);
  if (whyEnv.state === "stale" || whyEnv.state === "partial") bannerWarnings.push(...whyEnv.warnings);

  const toggleClaim = (claimId?: string) => {
    if (!claimId) return;
    setExpandedClaimIds((ids) =>
      ids.includes(claimId) ? ids.filter((id) => id !== claimId) : [...ids, claimId],
    );
  };

  return (
    <div className="pb-10">
      <div className="sticky top-0 z-10 bg-surface/95 backdrop-blur px-5 lg:px-8 pt-4 pb-4 border-b border-hairline">
        <button onClick={onBack} className="xl:hidden text-secondary text-[15px] min-h-[44px] flex items-center">
          ‹ Back
        </button>
        <div className="flex items-start justify-between gap-4 mt-1">
          <div>
            <div className="flex items-center gap-2 flex-wrap">
              <div className="text-[24px] font-bold">{thesis.ticker}</div>
              <StatusPill status={thesis.status} size="sm" />
            </div>
            <div className="text-[14px] text-secondary mt-1">{thesis.oneLiner}</div>
            <div className="mt-2 text-[14px]">
              <ConvictionDelta from={headerPrev} to={thesis.conviction} />
            </div>
          </div>
          <div className="text-right">
            <div className="tnum text-[34px] font-bold leading-none">{thesis.conviction}</div>
            <div className="mt-2">
              <Coverage pct={thesis.coveragePct} confidence={thesis.confidence} />
            </div>
          </div>
        </div>
      </div>

      <div className="px-5 lg:px-8 pt-5 space-y-5">
        {bannerWarnings.length > 0 && (
          <WarningBanner title={thesisEnv.state === "stale" ? "Data may be stale" : "Data is partial"}>
            <ul className="space-y-1">
              {bannerWarnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          </WarningBanner>
        )}

        {whyEnv.state === "unavailable" && (
          <div className="rounded-card px-4 py-3 text-[13px]" style={{ background: "var(--surface-2)", color: "var(--text-2)" }}>
            Change explanation unavailable right now. Thesis and evidence below may still load.
          </div>
        )}

        <Section title="WHAT CHANGED">
          <div className="flex gap-2 flex-wrap">
            {PERIODS.map((p) => (
              <button
                key={p.id}
                onClick={() => setPeriod(p.id)}
                className="rounded-chip px-3 py-2 text-[13px] font-medium min-h-[44px]"
                style={{
                  background: period === p.id ? "var(--text)" : "var(--surface-2)",
                  color: period === p.id ? "var(--surface)" : "var(--text)",
                }}
              >
                {p.label}
              </button>
            ))}
          </div>
          <div className="mt-3 flex flex-wrap gap-1.5">
            {thesis.whatChanged[period].length > 0 ? (
              thesis.whatChanged[period].map((driver) => (
                <DriverChip key={`${period}-${driver.label}`} label={driver.label} points={driver.points} sign={driver.sign} />
              ))
            ) : (
              <p className="text-[14px] text-secondary">No material drivers for this period.</p>
            )}
          </div>
        </Section>

        {why?.meaningForYou && (
          <Section title="MEANING FOR YOU">
            <p className="text-[15px] leading-relaxed">{why.meaningForYou}</p>
          </Section>
        )}

        <Section title="DRIVERS">
          {why?.drivers?.length ? (
            <div className="space-y-2">
              {why.drivers.map((driver) => {
                const claimId = driver.thesisClaimId;
                const isExpanded = claimId ? expandedClaimIds.includes(claimId) : false;
                const linkedEvidence = claimId
                  ? evidenceItems.filter((item) => item.thesisClaimId === claimId)
                  : [];
                return (
                  <div key={`${driver.label}-${claimId ?? "none"}`} className="rounded-card bg-surface-2 overflow-hidden">
                    <button
                      data-testid="driver-row"
                      className="driver-row w-full flex items-center justify-between gap-3 px-4 py-3 text-left min-h-[44px]"
                      onClick={() => toggleClaim(claimId)}
                    >
                      <div className="flex items-start gap-3">
                        <span className="text-[14px] text-secondary mt-0.5">{isExpanded ? "▾" : "▸"}</span>
                        <div>
                          <div className="text-[15px] font-medium">{driver.label}</div>
                          <div className="text-[12px] text-secondary mt-1">
                            {driver.points > 0 ? "+" : ""}
                            {driver.points} conviction points
                          </div>
                        </div>
                      </div>
                      <StatusBadge sign={driver.sign} />
                    </button>
                    {isExpanded && (
                      <div className="px-4 pb-4 space-y-3">
                        {linkedEvidence.length > 0 ? (
                          linkedEvidence.map((item) => <EvidenceItem key={item.id} e={item} compact />)
                        ) : (
                          <p className="text-[13px] text-secondary">No linked evidence available for this driver yet.</p>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          ) : (
            <p className="text-[14px] text-secondary">No driver breakdown available.</p>
          )}
        </Section>

        <Section title="EVIDENCE">
          <div className="space-y-3">
            {thesis.conflicts.map((conflict) => (
              <div
                key={conflict.thesisClaimId}
                className="rounded-card px-4 py-3 text-[13px]"
                style={{ background: "var(--reeval-bg)", color: "var(--reeval)" }}
              >
                <div className="font-semibold">⚠ Evidence conflict</div>
                <div className="mt-1">
                  {conflict.positive} positive · {conflict.negative} negative
                </div>
                {conflict.unresolvedReason && <div className="mt-1 opacity-90">{conflict.unresolvedReason}</div>}
              </div>
            ))}

            <EvidenceGroup title="Supporting evidence" items={thesis.supporting} />
            <EvidenceGroup title="Risks & counter evidence" items={thesis.risks} />
          </div>
        </Section>

        <Section title="INVALIDATION">
          <ul className="space-y-2">
            {thesis.whatWouldChangeMyMind.map((item) => (
              <li key={item} className="flex gap-2 text-[14px] leading-relaxed">
                <span style={{ color: "var(--reeval)" }}>✕</span>
                <span>{item}</span>
              </li>
            ))}
          </ul>
        </Section>

        <details className="rounded-card bg-surface p-4">
          <summary className="cursor-pointer text-[13px] font-semibold text-secondary uppercase tracking-wide">
            Context
          </summary>
          <div className="mt-4 grid gap-5">
            <div>
              <h3 className="text-[13px] font-semibold text-secondary uppercase tracking-wide mb-2">Catalysts</h3>
              <div className="space-y-2">
                {thesis.catalysts.map((catalyst) => (
                  <div key={`${catalyst.date}-${catalyst.label}`} className="flex items-center justify-between text-[14px]">
                    <span>{catalyst.label}</span>
                    <span className="tnum text-secondary">{fmtDate(catalyst.date)}</span>
                  </div>
                ))}
              </div>
            </div>
            <div>
              <h3 className="text-[13px] font-semibold text-secondary uppercase tracking-wide mb-2">Decision history</h3>
              <div className="space-y-2">
                {thesis.decisionHistory.map((entry) => (
                  <div key={`${entry.date}-${entry.note}`} className="flex items-center justify-between gap-3 text-[14px]">
                    <span className="tnum text-secondary">{fmtDate(entry.date)}</span>
                    <span className="tnum font-medium">{entry.from} → {entry.to}</span>
                    <span className="text-secondary text-right">{entry.note}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </details>
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section>
      <h2 className="text-[13px] font-semibold text-secondary uppercase tracking-wide mb-2.5">{title}</h2>
      <div className="bg-surface rounded-card p-4">{children}</div>
    </section>
  );
}

function WarningBanner({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="rounded-card px-4 py-3 text-[13px]" style={{ background: "var(--watch-bg)", color: "var(--watch)" }}>
      <div className="font-semibold mb-1">{title}</div>
      <div>{children}</div>
    </div>
  );
}

function EvidenceGroup({ title, items }: { title: string; items: Evidence[] }) {
  return (
    <div>
      <h3 className="text-[13px] font-semibold text-secondary uppercase tracking-wide mb-2">{title}</h3>
      {items.length > 0 ? (
        <div className="space-y-3">
          {items.map((item) => <EvidenceItem key={item.id} e={item} />)}
        </div>
      ) : (
        <div className="rounded-card bg-surface-2 px-4 py-3 text-[14px] text-secondary">No items available.</div>
      )}
    </div>
  );
}

function EvidenceItem({ e, compact = false }: { e: Evidence; compact?: boolean }) {
  return (
    <div className={`rounded-card ${compact ? "bg-surface px-3 py-3" : "bg-surface-2 p-4"}`}>
      <div className="flex items-start justify-between gap-3">
        <p className="text-[14px] font-medium leading-snug">{e.claim}</p>
        <TypeTag type={e.type} />
      </div>
      <div className="mt-2 text-[12px] text-secondary">
        {e.sourceUrl ? (
          <a href={e.sourceUrl} target="_blank" rel="noreferrer" className="underline underline-offset-2">
            {e.sourceName} ↗
          </a>
        ) : (
          <span>{e.sourceName}</span>
        )}
        <span className="text-tertiary"> · </span>
        {fmtDate(e.observedAt)}
      </div>
      <p className="mt-2 text-[13px] leading-relaxed">
        <span className="text-secondary">Interpretation: </span>
        {e.interpretation}
      </p>
      <div className="mt-2 text-[12px] flex flex-wrap gap-x-2 gap-y-1">
        <span className="text-secondary">Confidence: <span className="font-medium text-primary">{e.confidence}</span></span>
        {e.freshness !== "fresh" && (
          <span style={{ color: e.freshness === "conflicting" ? "var(--reeval)" : "var(--watch)" }}>
            {e.freshness}
          </span>
        )}
      </div>
      {e.qualityNote && (
        <div className="mt-2 rounded-lg px-2.5 py-1.5 text-[12px]" style={{ background: "var(--watch-bg)", color: "var(--watch)" }}>
          {e.qualityNote}
        </div>
      )}
    </div>
  );
}

function StatusBadge({ sign }: { sign: WhyChanged["drivers"][number]["sign"] }) {
  const color =
    sign === "positive" ? "var(--pos)" : sign === "negative" ? "var(--neg)" : "var(--neutral)";
  return (
    <span className="text-[12px] font-semibold" style={{ color }}>
      {sign === "positive" ? "Supports" : sign === "negative" ? "Risks" : "Context"}
    </span>
  );
}

function LoadingState({ ticker, onBack }: { ticker: string; onBack: () => void }) {
  return (
    <div className="pb-10">
      <div className="sticky top-0 z-10 bg-surface/95 backdrop-blur px-5 lg:px-8 pt-4 pb-4 border-b border-hairline">
        <button onClick={onBack} className="xl:hidden text-secondary text-[15px] min-h-[44px] flex items-center">
          ‹ Back
        </button>
        <div className="text-[24px] font-bold mt-1">{ticker}</div>
      </div>
      <div className="px-5 lg:px-8 pt-5 space-y-4">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="bg-surface rounded-card p-4 animate-pulse">
            <div className="h-4 w-28 rounded bg-surface-2" />
            <div className="h-4 w-full rounded bg-surface-2 mt-3" />
            <div className="h-4 w-3/4 rounded bg-surface-2 mt-2" />
          </div>
        ))}
      </div>
    </div>
  );
}

function UnavailableState({
  ticker,
  warning,
  onBack,
  onRetry,
}: {
  ticker: string;
  warning?: string;
  onBack: () => void;
  onRetry: () => void;
}) {
  return (
    <div className="px-5 lg:px-8 pt-6">
      <button onClick={onBack} className="text-secondary text-[15px] min-h-[44px] flex items-center">
        ‹ Back
      </button>
      <div className="mt-8 rounded-card bg-surface p-5 text-center">
        <h1 className="text-[22px] font-bold">Thesis unavailable</h1>
        <p className="text-secondary text-[14px] mt-2 leading-relaxed">
          {warning ?? `Could not load the thesis for ${ticker}.`}
        </p>
        <div className="flex gap-2 justify-center mt-5">
          <button
            onClick={onRetry}
            className="rounded-chip px-4 py-2 text-[14px] font-semibold min-h-[44px]"
            style={{ background: "var(--text)", color: "var(--surface)" }}
          >
            Retry
          </button>
          <button
            onClick={onBack}
            className="rounded-chip px-4 py-2 text-[14px] font-medium min-h-[44px]"
            style={{ background: "var(--surface-2)" }}
          >
            Back
          </button>
        </div>
      </div>
    </div>
  );
}

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}
