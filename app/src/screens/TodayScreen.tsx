import { useCallback, useEffect, useState } from "react";
import { fetchToday } from "../data/api";
import { getMode, setMode, type UiMode } from "../data/mode";
import { FocusCard } from "../components/FocusCard";
import type { ApiEnvelope, DailyBrief, Holding } from "../types";

const OVERALL_LINE: Record<string, string> = {
  no_action: "No action needed today",
  watch: "One holding worth watching",
  re_evaluate: "One holding needs re-evaluation",
};

type Load = { status: "loading" } | { status: "done"; env: ApiEnvelope<DailyBrief> };

export function TodayScreen({
  onOpenThesis,
  onWhyChanged,
  onEvidence,
}: {
  onOpenThesis: (t: string) => void;
  onWhyChanged: (t: string) => void;
  onEvidence: (t: string) => void;
}) {
  const [load, setLoad] = useState<Load>({ status: "loading" });
  const [mode, setModeState] = useState<UiMode>(getMode());

  const run = useCallback(() => {
    setLoad({ status: "loading" });
    let alive = true;
    fetchToday().then((env) => {
      if (alive) setLoad({ status: "done", env });
    });
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => run(), [run, mode]);

  const switchMode = (m: UiMode) => {
    setMode(m);
    setModeState(m);
  };

  return (
    <div className="px-5 lg:px-8 pt-6 pb-12">
      <ModeBar mode={mode} onSwitch={switchMode} />
      {load.status === "loading" ? (
        <p className="text-secondary text-[15px] mt-8">Loading {mode === "demo" ? "demo" : "live"} data…</p>
      ) : (
        <Body
          env={load.env}
          onRetry={run}
          onSwitchDemo={() => switchMode("demo")}
          cardProps={{ onOpenThesis, onWhyChanged, onEvidence }}
        />
      )}
    </div>
  );
}

function Body({
  env,
  onRetry,
  onSwitchDemo,
  cardProps,
}: {
  env: ApiEnvelope<DailyBrief>;
  onRetry: () => void;
  onSwitchDemo: () => void;
  cardProps: {
    onOpenThesis: (t: string) => void;
    onWhyChanged: (t: string) => void;
    onEvidence: (t: string) => void;
  };
}) {
  // Honest failure: never fake a brief. Show unavailable + retry (+ opt-in demo).
  if (env.state === "unavailable" || !env.data) {
    return (
      <div className="mt-10 text-center">
        <h1 className="text-[22px] font-bold">Live data unavailable</h1>
        <p className="text-secondary text-[14px] mt-2 leading-relaxed">
          {env.warnings[0] ?? "The engine could not be reached."} No sample data is shown in Live mode.
        </p>
        <div className="flex gap-2 justify-center mt-5">
          <button onClick={onRetry} className="rounded-chip px-4 py-2 text-[14px] font-semibold min-h-[44px]" style={{ background: "var(--text)", color: "var(--surface)" }}>
            Retry
          </button>
          {env.mode !== "demo" && (
            <button onClick={onSwitchDemo} className="rounded-chip px-4 py-2 text-[14px] font-medium min-h-[44px]" style={{ background: "var(--surface-2)" }}>
              Try demo data
            </button>
          )}
        </div>
      </div>
    );
  }

  const b = env.data;
  const byDelta = (a: Holding, c: Holding) =>
    Math.abs(c.conviction - c.prevConviction) - Math.abs(a.conviction - a.prevConviction);
  const needsAttention = [...b.needsAttention].sort(byDelta);
  const worthWatching = [...b.worthWatching].sort(byDelta);
  const attentionCount = b.needsAttention.length + b.worthWatching.length;
  const overallLine =
    b.overallStatus === "no_action"
      ? "No action needed today"
      : attentionCount === 1
        ? OVERALL_LINE[b.overallStatus]
        : `${attentionCount} holdings need a look`;

  return (
    <>
      <p className="text-secondary text-[15px]">Good morning</p>
      <h1 className="text-[26px] lg:text-[32px] font-bold leading-tight mt-1">{overallLine}</h1>
      <p className="text-secondary text-[14px] mt-2 leading-relaxed">
        {b.overallStatus === "no_action"
          ? "Only price movement today — your core theses are intact."
          : "Some holdings changed at the thesis level. Tap a card to see why."}
      </p>
      <FreshnessLine env={env} />

      {(env.state === "partial" || env.state === "stale") && env.warnings.length > 0 && (
        <div className="mt-3 rounded-card px-3 py-2 text-[13px]" style={{ background: "var(--watch-bg)", color: "var(--watch)" }}>
          {env.warnings[0]}
        </div>
      )}

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

      <CalmDisclosure holdings={b.noMaterialChange} onOpenThesis={cardProps.onOpenThesis} />
    </>
  );
}

function ModeBar({ mode, onSwitch }: { mode: UiMode; onSwitch: (m: UiMode) => void }) {
  const isDemo = mode === "demo";
  return (
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
        onClick={() => onSwitch(isDemo ? "live" : "demo")}
        className="text-[12px] text-secondary underline underline-offset-2 min-h-[44px]"
      >
        {isDemo ? "Switch to Live" : "Switch to Demo"}
      </button>
    </div>
  );
}

function FreshnessLine({ env }: { env: ApiEnvelope<DailyBrief> }) {
  const label =
    env.mode === "demo"
      ? "Sample data (demo mode)"
      : env.fetched_at
        ? `Computed ${relAge(env.fetched_at)}`
        : "Compute time unavailable";
  return <p className="text-tertiary text-[12px] mt-2">{label}</p>;
}

function CalmDisclosure({ holdings, onOpenThesis }: { holdings: Holding[]; onOpenThesis: (t: string) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="mt-6">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1.5 text-[13px] font-semibold text-secondary uppercase tracking-wide min-h-[44px]"
      >
        No material change ({holdings.length})
        <span className="text-tertiary">{open ? "▲" : "▼"}</span>
      </button>
      {open && (
        <div className="space-y-2 animate-fade-in">
          {holdings.map((h) => (
            <CalmRow key={h.ticker} h={h} onOpenThesis={onOpenThesis} />
          ))}
        </div>
      )}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mt-6">
      <h2 className="text-[13px] font-semibold text-secondary uppercase tracking-wide mb-2.5">{title}</h2>
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">{children}</div>
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
        <div className="text-[12px]" style={{ color: h.dayChangePct >= 0 ? "var(--pos)" : "var(--neg)" }}>
          {h.dayChangePct >= 0 ? "+" : ""}
          {h.dayChangePct}%
        </div>
      </div>
    </button>
  );
}

function relAge(iso: string): string {
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (!isFinite(mins)) return "at an unknown time";
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const h = Math.round(mins / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.round(h / 24)}d ago`;
}
