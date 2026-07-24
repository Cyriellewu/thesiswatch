/**
 * Honest data layer (ADR-003/004).
 *
 * - Live mode: fetch the real engine (FastAPI /api/*). On network/HTTP failure we return
 *   an explicit `unavailable` envelope (Today) or null/empty (detail) — we NEVER silently
 *   substitute mock data.
 * - Demo mode: the user has explicitly opted in; we serve bundled sample data, tagged so
 *   it can't be mistaken for real data.
 *
 * `/api/today` already returns the typed ApiEnvelope from the backend. The detail
 * endpoints (why/evidence/thesis/exposures) are not yet enveloped (that is the
 * merge-detail slice); here they simply stop falling back to mock on error.
 */
import type {
  ApiEnvelope,
  DailyBrief,
  Evidence,
  PortfolioExposure,
  StockThesis,
  WhyChanged,
} from "../types";
import {
  dailyBrief as mockDailyBrief,
  evidenceByTicker,
  exposures as mockExposures,
  thesisByTicker,
  whyChangedByTicker,
} from "./mock";
import { getMode } from "./mode";

const API = (import.meta as any).env?.VITE_API ?? "/api";
const ENVELOPE_STATES = ["ok", "stale", "partial", "unavailable"] as const;

function unavailable<T>(reason: string): ApiEnvelope<T> {
  return {
    data: null, state: "unavailable", mode: getMode(),
    observed_at: null, fetched_at: null, sources: [], warnings: [reason],
  };
}

function isEnvelopeState(state: unknown): state is ApiEnvelope<unknown>["state"] {
  return typeof state === "string" && ENVELOPE_STATES.includes(state as (typeof ENVELOPE_STATES)[number]);
}

function normalizeEnvelope<T>(body: ApiEnvelope<T>): ApiEnvelope<T> {
  return {
    ...body,
    mode: body.mode === "demo" ? "demo" : "live",
    warnings: Array.isArray(body.warnings) ? body.warnings : [],
    sources: Array.isArray(body.sources) ? body.sources : [],
  };
}

/** Today: consume the backend's typed envelope; honest `unavailable` on any failure. */
export async function fetchToday(): Promise<ApiEnvelope<DailyBrief>> {
  if (getMode() === "demo") {
    return {
      data: mockDailyBrief, state: "ok", mode: "demo",
      observed_at: null, fetched_at: null, sources: ["bundled-sample"],
      warnings: ["Demo mode: bundled sample data, not live sources."],
    };
  }
  try {
    const r = await fetch(`${API}/today`, { signal: AbortSignal.timeout(20000) });
    if (!r.ok) return unavailable<DailyBrief>(`HTTP ${r.status} from /today`);
    const body = (await r.json()) as ApiEnvelope<DailyBrief>;
    if (!body || !isEnvelopeState(body.state)) {
      return unavailable<DailyBrief>("Malformed envelope from /today");
    }
    return normalizeEnvelope(body);
  } catch (e) {
    return unavailable<DailyBrief>(`Request to /today failed: ${(e as Error).message}`);
  }
}

/** Detail fetch: demo → labeled sample; live → real, and null/empty (never mock) on error. */
async function detail<T>(path: string, mock: T, empty: T): Promise<T> {
  if (getMode() === "demo") return mock;
  try {
    const r = await fetch(`${API}${path}`, { signal: AbortSignal.timeout(20000) });
    if (!r.ok) return empty;
    const body = (await r.json()) as ApiEnvelope<T>;
    if (!body || !isEnvelopeState(body.state)) {
      return empty;
    }
    if (body.state === "unavailable" || body.data == null) {
      return empty;
    }
    return body.data;
  } catch {
    return empty; // honest: no data, not fabricated mock
  }
}

/** Envelope-aware thesis fetch for ThesisDetail — returns full state info for honest rendering. */
export async function fetchThesisEnvelope(t: string): Promise<ApiEnvelope<StockThesis>> {
  if (getMode() === "demo") {
    const d = thesisByTicker[t] ?? null;
    return {
      data: d,
      state: d ? "ok" : "unavailable",
      mode: "demo",
      observed_at: null,
      fetched_at: null,
      sources: ["bundled-sample"],
      warnings: ["Demo mode: bundled sample data."],
    };
  }
  try {
    const r = await fetch(`${API}/thesis/${t}`, { signal: AbortSignal.timeout(20000) });
    if (!r.ok) return unavailable<StockThesis>(`HTTP ${r.status} from /thesis/${t}`);
    const body = (await r.json()) as ApiEnvelope<StockThesis>;
    if (!body || !isEnvelopeState(body.state)) {
      return unavailable<StockThesis>("Malformed envelope from /thesis");
    }
    return normalizeEnvelope(body);
  } catch (e) {
    return unavailable<StockThesis>(`/thesis/${t} failed: ${(e as Error).message}`);
  }
}

/** Envelope-aware why fetch for ThesisDetail. */
export async function fetchWhyEnvelope(t: string): Promise<ApiEnvelope<WhyChanged>> {
  if (getMode() === "demo") {
    const d = whyChangedByTicker[t] ?? null;
    return {
      data: d,
      state: d ? "ok" : "unavailable",
      mode: "demo",
      observed_at: null,
      fetched_at: null,
      sources: ["bundled-sample"],
      warnings: [],
    };
  }
  try {
    const r = await fetch(`${API}/why/${t}`, { signal: AbortSignal.timeout(20000) });
    if (!r.ok) return unavailable<WhyChanged>(`HTTP ${r.status} from /why/${t}`);
    const body = (await r.json()) as ApiEnvelope<WhyChanged>;
    if (!body || !isEnvelopeState(body.state)) {
      return unavailable<WhyChanged>("Malformed envelope from /why");
    }
    return normalizeEnvelope(body);
  } catch (e) {
    return unavailable<WhyChanged>(`/why/${t} failed: ${(e as Error).message}`);
  }
}

export const fetchExposures = () =>
  detail<PortfolioExposure[]>("/exposures", mockExposures, []);
export const fetchWhy = (t: string) =>
  detail<WhyChanged | null>(`/why/${t}`, whyChangedByTicker[t] ?? null, null);
export const fetchEvidence = (t: string) =>
  detail<Evidence[]>(`/evidence/${t}`, evidenceByTicker[t] ?? [], []);
export const fetchThesis = (t: string) =>
  detail<StockThesis | null>(`/thesis/${t}`, thesisByTicker[t] ?? null, null);
