/**
 * Explicit Demo/Live mode (ADR-004, Q1 provisional = fail-closed + manual Demo toggle).
 *
 * Live (default): fetch real sources; on failure the UI shows an honest `unavailable`
 * state — never silently-substituted sample data.
 * Demo: the user *explicitly* opts into clearly-labeled sample data (e.g. to try the
 * product with no keys). Demo data is always tagged `mode:"demo"` so it can never be
 * mistaken for real data.
 */
export type UiMode = "live" | "demo";

const KEY = "thesiswatch.mode";

export function getMode(): UiMode {
  try {
    return localStorage.getItem(KEY) === "demo" ? "demo" : "live";
  } catch {
    return "live";
  }
}

export function setMode(mode: UiMode): void {
  try {
    localStorage.setItem(KEY, mode);
  } catch {
    /* storage unavailable — mode stays default for this session */
  }
}

export function isDemo(): boolean {
  return getMode() === "demo";
}
