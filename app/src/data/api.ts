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

function unavailable<T>(reason: string): ApiEnvelope<T> {
  return {
    data: null, state: "unavailable", mode: getMode(),
    observed_at: null, fetched_at: null, sources: [], warnings: [reason],
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
    const states = ["ok", "stale", "partial", "unavailable"];
    if (!body || typeof body.state !== "string" || !states.includes(body.state)) {
      return unavailable<DailyBrief>("Malformed envelope from /today");
    }
    // Normalize array/mode fields so consumers can dereference warnings/sources safely
    // even if a backend response omits or malforms them.
    return {
      ...body,
      mode: body.mode === "demo" ? "demo" : "live",
      warnings: Array.isArray(body.warnings) ? body.warnings : [],
      sources: Array.isArray(body.sources) ? body.sources : [],
    };
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
    return (await r.json()) as T;
  } catch {
    return empty; // honest: no data, not fabricated mock
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

