import { useEffect, useState } from "react";
import { fetchDailyBrief } from "../data/api";
import { dailyBrief as mockDailyBrief } from "../data/mock";
import { FocusCard } from "../components/FocusCard";
import type { DailyBrief, Holding } from "../types";

const OVERALL_LINE: Record<string, string> = {
  no_action: "No action needed today",
  watch: "One holding worth watching",
  re_evaluate: "One holding needs re-evaluation",
};

export function TodayScreen({
  onOpenThesis,
  onWhyChanged,
  onEvidence,
}: {
  onOpenThesis: (t: string) => void;
  onWhyChanged: (t: string) => void;
  onEvidence: (t: string) => void;
}) {
  const [b, setB] = useState<DailyBrief>(mockDailyBrief);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let alive = true;
    fetchDailyBrief().then((d) => {
      if (alive) {
        setB(d);
        setLoading(false);
      }
    });
    return () => {
      alive = false;
    };
  }, []);
  const [showCalm, setShowCalm] = useState(false);
  const cardProps = { onOpenThesis, onWhyChanged, onEvidence };
  const byDelta = (a: Holding, c: Holding) =>
    Math.abs(c.conviction - c.prevConviction) - Math.abs(a.conviction - a.prevConviction);
  const needsAttention = [...b.needsAttention].sort(byDelta);
  const worthWatching = [...b.worthWatching].sort(byDelta);
  const overallLine =
    b.overallStatus === "no_action"
      ? "No action needed today"
      : b.needsAttention.length + b.worthWatching.length === 1
        ? OVERALL_LINE[b.overallStatus]
        : `${b.needsAttention.length + b.worthWatching.length} holdings need a look`;

  return (
    <div className="px-5 pt-3 pb-6">
      <p className="text-secondary text-[15px]">Good morning</p>
      <h1 className="text-[26px] font-bold leading-tight mt-1">{overallLine}</h1>
      <p className="text-secondary text-[14px] mt-2 leading-relaxed">
        {b.overallStatus === "no_action"
          ? "Only price movement today — your core theses are intact."
          : "Some holdings changed at the thesis level. Tap a card to see why."}
      </p>
      <p className="text-tertiary text-[12px] mt-2">
        {loading ? "Loading live data…" : `Updated ${b.updatedAgoMinutes} min ago`}
      </p>

      <Section title="Needs attention">
        {needsAttention.map((h) => (
          <FocusCard key={h.ticker} h={h} {...cardProps} />
        ))}
      </Section>

      {worthWatching.length > 0 && (
        <Section title="Worth watching">
          {worthWatching.map((h) => (
            <FocusCard key={h.ticker} h={h} {...cardProps} />
          ))}
        </Section>
      )}

      <div className="mt-6">
        <button
          onClick={() => setShowCalm((v) => !v)}
          className="flex items-center gap-1.5 text-[13px] font-semibold text-secondary uppercase tracking-wide min-h-[44px]"
        >
          No material change ({b.noMaterialChange.length})
          <span className="text-tertiary">{showCalm ? "▲" : "▼"}</span>
        </button>
        {showCalm && (
          <div className="space-y-2 animate-fade-in">
            {b.noMaterialChange.map((h) => (
              <CalmRow key={h.ticker} h={h} onOpenThesis={onOpenThesis} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mt-6">
      <h2 className="text-[13px] font-semibold text-secondary uppercase tracking-wide mb-2.5">{title}</h2>
      <div className="space-y-3">{children}</div>
    </div>
  );
}

function CalmRow({ h, onOpenThesis }: { h: Holding; onOpenThesis: (t: string) => void }) {
  return (
    <button
      onClick={() => onOpenThesis(h.ticker)}
      className="w-full flex items-center justify-between bg-surface rounded-card px-4 py-3 text-left min-h-[44px]"
    >
      <div>
        <div className="text-[15px] font-semibold">{h.ticker}</div>
        <div className="text-[12px] text-secondary">{h.name}</div>
      </div>
      <div className="tnum text-right">
        <div className="text-[15px] font-semibold">{h.conviction}</div>
        <div
          className="text-[12px]"
          style={{ color: h.dayChangePct >= 0 ? "var(--pos)" : "var(--neg)" }}
        >
          {h.dayChangePct >= 0 ? "+" : ""}
          {h.dayChangePct}%
        </div>
      </div>
    </button>
  );
}
