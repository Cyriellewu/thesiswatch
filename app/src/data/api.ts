/**
 * Real-data layer. Fetches the live ThesisWatch engine (FastAPI on /api/*),
 * and falls back to bundled mock data when the API is unreachable (so the UI
 * still renders offline / in a static preview).
 */
import type { DailyBrief, Evidence, PortfolioExposure, StockThesis, WhyChanged } from "../types";
import {
  dailyBrief as mockDailyBrief,
  evidenceByTicker,
  exposures as mockExposures,
  thesisByTicker,
  whyChangedByTicker,
} from "./mock";

const API = (import.meta as any).env?.VITE_API ?? "/api";

async function get<T>(path: string, fallback: T): Promise<T> {
  try {
    const r = await fetch(`${API}${path}`, { signal: AbortSignal.timeout(20000) });
    if (!r.ok) throw new Error(String(r.status));
    return (await r.json()) as T;
  } catch {
    return fallback;
  }
}

export const fetchDailyBrief = () => get<DailyBrief>("/today", mockDailyBrief);
export const fetchExposures = () => get<PortfolioExposure[]>("/exposures", mockExposures);
export const fetchWhy = (t: string) =>
  get<WhyChanged | null>(`/why/${t}`, whyChangedByTicker[t] ?? null);
export const fetchEvidence = (t: string) =>
  get<Evidence[]>(`/evidence/${t}`, evidenceByTicker[t] ?? []);
export const fetchThesis = (t: string) =>
  get<StockThesis | null>(`/thesis/${t}`, thesisByTicker[t] ?? null);
