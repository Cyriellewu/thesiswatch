import type { ThesisStatus, EvidenceType, DriverSign } from "../types";

export function statusMeta(s: ThesisStatus) {
  switch (s) {
    case "re_evaluate":
      return { label: "Re-evaluate", icon: "⟳", fg: "var(--reeval)", bg: "var(--reeval-bg)" };
    case "watch":
      return { label: "Watch", icon: "◍", fg: "var(--watch)", bg: "var(--watch-bg)" };
    default:
      return { label: "No action", icon: "✓", fg: "var(--neutral)", bg: "var(--calm-bg)" };
  }
}

export function StatusPill({ status, size = "md" }: { status: ThesisStatus; size?: "sm" | "md" }) {
  const m = statusMeta(status);
  const pad = size === "sm" ? "px-2 py-0.5 text-[11px]" : "px-2.5 py-1 text-xs";
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-chip font-semibold ${pad}`}
      style={{ color: m.fg, background: m.bg }}
    >
      <span aria-hidden>{m.icon}</span>
      {m.label}
    </span>
  );
}

export function ConvictionDelta({ from, to }: { from: number; to: number }) {
  const up = to >= from;
  const col = to === from ? "var(--neutral)" : up ? "var(--pos)" : "var(--neg)";
  return (
    <span className="tnum inline-flex items-center gap-1 font-semibold">
      <span className="text-secondary">{from}</span>
      <span className="text-tertiary">→</span>
      <span style={{ color: col }}>{to}</span>
    </span>
  );
}

export function DriverChip({ label, points, sign }: { label: string; points: number; sign: DriverSign }) {
  const col = sign === "positive" ? "var(--pos)" : sign === "negative" ? "var(--neg)" : "var(--neutral)";
  const s = points > 0 ? `+${points}` : `${points}`;
  return (
    <span className="inline-flex items-center gap-1.5 rounded-chip bg-surface-2 px-2.5 py-1 text-[12px]">
      <span className="tnum font-semibold" style={{ color: col }}>{s}</span>
      <span className="text-secondary">{label}</span>
    </span>
  );
}

const TYPE_META: Record<EvidenceType, { label: string; fg: string; bg: string }> = {
  fact: { label: "Fact", fg: "var(--pos)", bg: "var(--calm-bg)" },
  model_interpretation: { label: "Model interpretation", fg: "var(--watch)", bg: "var(--watch-bg)" },
  assumption: { label: "Assumption", fg: "var(--neutral)", bg: "var(--calm-bg)" },
  counterargument: { label: "Counterargument", fg: "var(--neg)", bg: "var(--reeval-bg)" },
};

export function TypeTag({ type }: { type: EvidenceType }) {
  const m = TYPE_META[type];
  return (
    <span
      className="inline-flex items-center rounded-chip px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide"
      style={{ color: m.fg, background: m.bg }}
    >
      {m.label}
    </span>
  );
}

export function Coverage({ pct, confidence }: { pct: number; confidence: string }) {
  return (
    <span className="text-[12px] text-secondary">
      Confidence: <span className="text-primary font-medium">{confidence}</span>
      <span className="text-tertiary"> · </span>
      Evidence coverage: <span className="tnum text-primary font-medium">{pct}%</span>
    </span>
  );
}
