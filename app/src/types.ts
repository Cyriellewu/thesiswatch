/**
 * AlphaWatch prototype contracts.
 *
 * These type-only exports model the locked journey:
 * Today → Why Changed → Evidence → Stock Thesis → Portfolio What-if.
 */

/** Daily, horizon-aware conclusion for a holding. */
export type ThesisStatus =
  | 'no_material_change'
  | 'watch'
  | 're_evaluate';

/** Data availability state owned by each renderable surface. */
export type DataState =
  | 'loading'
  | 'ok'
  | 'partial_data'
  | 'stale_data'
  | 'offline'
  | 'error';

/** Provenance category; interpretations and assumptions must not appear as facts. */
export type EvidenceType =
  | 'fact'
  | 'model_interpretation'
  | 'assumption'
  | 'counterargument';

/** Evidence quality state; unavailable means the referenced item cannot be opened. */
export type EvidenceFreshness =
  | 'fresh'
  | 'stale'
  | 'conflicting'
  | 'unavailable';

/** Display label derived from a confidence score bounded to [0, 1]. */
export type ConfidenceLabel =
  | 'Low'
  | 'Medium'
  | 'Medium-high'
  | 'High';

/** Direction of a driver or evidence item relative to the current thesis. */
export type DriverSign = 'positive' | 'negative' | 'neutral';

/** User-controlled presentation state; it never changes the computed thesis status. */
export type CardState =
  | 'default'
  | 'snoozed'
  | 'muted'
  | 'dismissed'
  | 'expanded';

/** Relative position sizing bucket used by mobile summaries. */
export type PositionSize = 'small' | 'moderate' | 'large';

/** Portfolio exposure severity. */
export type ExposureLevel = 'high' | 'medium' | 'low';

/** Surfaces in the locked AlphaWatch interaction chain. */
export type AskSurface =
  | 'today'
  | 'why_changed'
  | 'evidence'
  | 'stock_thesis'
  | 'portfolio_what_if';

/** Broad portfolio conclusion used by the daily brief. */
export type OverallStatus =
  | 'no_action'
  | 'watch'
  | 're_evaluate';

/** Supported what-if instructions; they are simulations, not trade orders. */
export type WhatIfActionType =
  | 'trim'
  | 'add'
  | 'remove'
  | 'initiate'
  | 'hold';

/**
 * A holding as shown on Today.
 * Numeric bounds are validation requirements for producers of this contract.
 */
export interface Holding {
  /** Exchange ticker, unique within a portfolio. */
  ticker: string;
  /** Human-readable security or company name. */
  name: string;
  /** Current conviction, bounded integer [0, 100]. */
  conviction: number;
  /** Previous comparable conviction, bounded integer [0, 100]. */
  prevConviction: number;
  /** Current horizon-aware daily status. */
  status: ThesisStatus;
  /** Confidence label for the status conclusion. */
  confidence: ConfidenceLabel;
  /** Usable evidence coverage, bounded integer [0, 100]. */
  coveragePct: number;
  /** Coarse sizing bucket; not an instruction to trade. */
  positionSize: PositionSize;
  /** Explicit investment horizon in whole months, greater than zero. */
  horizonMonths: number;
  /** Latest known price in `currency`, greater than or equal to zero. */
  price: number;
  /** One-day percentage move; context only until it passes the horizon gate. */
  dayChangePct: number;
  /** ISO 4217 currency code. */
  currency: string;
  /** Portfolio weight, bounded to [0, 100]. */
  weightPct: number;
  /** ISO 8601 timestamp for the latest price observation. */
  priceObservedAt: string | null;
  /** State of the data used to render this holding. */
  dataState: DataState;
  /** Current user presentation state for this dated card. */
  cardState: CardState;
}

/** A signed contributor to a conviction change. */
export interface ConvictionDriver {
  /** Concise, user-facing description of the changed input. */
  label: string;
  /** Signed conviction-point contribution; positive supports, negative weakens. */
  points: number;
  /** Explicit semantic direction, including neutral context. */
  sign: DriverSign;
  /** Related thesis claim when the driver is claim-specific. */
  thesisClaimId?: string;
}

/** Explanation shown after selecting a changed holding on Today. */
export interface WhyChanged {
  ticker: string;
  /** Conviction before the explained change, bounded integer [0, 100]. */
  prevConviction: number;
  /** Conviction after the explained change, bounded integer [0, 100]. */
  currentConviction: number;
  /** Ordered highest-impact first. */
  drivers: ConvictionDriver[];
  /** Horizon-aware interpretation written for this portfolio owner. */
  meaningForYou: string;
  status: ThesisStatus;
  /** ISO 8601 timestamp at which the explanation was generated. */
  asOf: string | null;
  dataState: DataState;
}

/** Atomic evidence item linked to a thesis claim. */
export interface Evidence {
  id: string;
  /** Source-grounded proposition; not the model's interpretation. */
  claim: string;
  type: EvidenceType;
  sourceName: string;
  /** Canonical source location when one is available. */
  sourceUrl?: string;
  /** ISO 8601 time the underlying event/value was observed. */
  observedAt: string | null;
  /** ISO 8601 time AlphaWatch retrieved the item. */
  fetchedAt: string | null;
  freshness: EvidenceFreshness;
  /** Explicit thesis meaning, visually separated from the source claim. */
  interpretation: string;
  confidence: ConfidenceLabel;
  thesisClaimId: string;
  /** Direction relative to the linked thesis claim. */
  sign: DriverSign;
  /** Human-readable reason when freshness is stale, conflicting, or unavailable. */
  qualityNote?: string;
}

/** Aggregate conflict summary without suppressing either side's evidence. */
export interface EvidenceConflict {
  thesisClaimId: string;
  /** Count of usable evidence items supporting the claim; non-negative integer. */
  positive: number;
  /** Count of usable evidence items opposing the claim; non-negative integer. */
  negative: number;
  /** IDs of all evidence items represented by this summary. */
  evidenceIds: string[];
  /** User-facing explanation of why the conflict remains unresolved. */
  unresolvedReason?: string;
}

/** A durable, independently testable component of an investment thesis. */
export interface ThesisClaim {
  id: string;
  text: string;
}

/** A dated thesis event used by the Stock Thesis timeline. */
export interface DecisionHistoryEntry {
  /** ISO 8601 date or timestamp. */
  date: string;
  /** Previous conviction, bounded integer [0, 100]. */
  from: number;
  /** New conviction, bounded integer [0, 100]. */
  to: number;
  note: string;
}

/** A known event that may affect the thesis. */
export interface ThesisCatalyst {
  /** ISO 8601 date or timestamp. */
  date: string;
  label: string;
}

/** Full thesis view reached after reviewing Evidence. */
export interface StockThesis {
  ticker: string;
  status: ThesisStatus;
  /** Conclusion-first thesis summary. */
  oneLiner: string;
  /** Current conviction, bounded integer [0, 100]. */
  conviction: number;
  confidence: ConfidenceLabel;
  /** Usable evidence coverage, bounded integer [0, 100]. */
  coveragePct: number;
  /** Conviction changes over one day, one week, and one month. */
  whatChanged: ConvictionDriver[];
  currentThesis: ThesisClaim[];
  supporting: Evidence[];
  risks: Evidence[];
  /** Explicit falsifiers or review conditions. */
  whatWouldChangeMyMind: string[];
  catalysts: ThesisCatalyst[];
  decisionHistory: DecisionHistoryEntry[];
  /** Unresolved evidence conflicts, retained rather than averaged away. */
  conflicts: EvidenceConflict[];
  /** Investment horizon used to gate signals, in whole months. */
  horizonMonths: number;
  dataState: DataState;
  /** ISO 8601 timestamp for this thesis snapshot. */
  asOf: string | null;
}

/** Current exposure to a portfolio-level factor. */
export interface PortfolioExposure {
  factor: string;
  level: ExposureLevel;
  holdings: Array<{
    ticker: string;
    /** Contribution to this exposure, bounded to [0, 100]. */
    weightPct: number;
  }>;
}

/** One simulated portfolio change; never an executable trade instruction. */
export interface WhatIfAction {
  id: string;
  type: WhatIfActionType;
  ticker: string;
  /** Existing portfolio weight, bounded to [0, 100]. */
  fromWeightPct: number;
  /** Simulated portfolio weight, bounded to [0, 100]. */
  toWeightPct: number;
}

/** Before/after value and signed change for a percentage-based metric. */
export interface WhatIfMetricDelta {
  beforePct: number;
  afterPct: number;
  /** `afterPct - beforePct`, preserving sign. */
  deltaPct: number;
}

/** Before/after result of applying one or more simulated actions. */
export interface WhatIfResult {
  actions: WhatIfAction[];
  concentration: {
    /** Largest single-position portfolio weight. */
    largestPosition: WhatIfMetricDelta;
    /** Combined weight of the five largest positions. */
    topFive: WhatIfMetricDelta;
  };
  exposure: Array<{
    factor: string;
    before: ExposureLevel;
    after: ExposureLevel;
    /** Signed percentage-point exposure change. */
    deltaPct: number;
  }>;
  cash: WhatIfMetricDelta;
  /** Exposures after the simulation. */
  resultingExposures: PortfolioExposure[];
  dataState: DataState;
  /** ISO 8601 timestamp for the inputs used by the simulation. */
  asOf: string;
}

/** Conclusion-first payload for the Today surface. */
export interface DailyBrief {
  /** ISO 8601 calendar date. */
  date: string;
  overallStatus: OverallStatus;
  needsAttention: Holding[];
  worthWatching: Holding[];
  noMaterialChange: Holding[];
  /** @deprecated backend no longer sends this; use the envelope's fetched_at instead. */
  updatedAgoMinutes?: number;
  dataState: DataState;
}

/** Server-declared availability state for the typed API envelope (ADR-001). */
export type EnvelopeState = 'ok' | 'stale' | 'partial' | 'unavailable';

/** Server-declared data provenance mode (ADR-004). */
export type EnvelopeMode = 'demo' | 'live';

/**
 * The single typed envelope every /api/* response is wrapped in. The frontend reads
 * `state`/`mode`/`warnings` to render honestly and NEVER silently substitutes mock data.
 */
export interface ApiEnvelope<T> {
  data: T | null;
  state: EnvelopeState;
  mode: EnvelopeMode;
  observed_at: string | null;
  fetched_at: string | null;
  sources: string[];
  warnings: string[];
}

/** Current page and entity context supplied to the contextual question UI. */
export interface AskContext {
  surface: AskSurface;
  ticker?: string;
  thesisClaimId?: string;
  evidenceId?: string;
  whatIfActionIds?: string[];
  /** Investment horizon inherited from the holding, in whole months. */
  horizonMonths?: number;
}

/** A context-aware suggested question keyed to one locked-chain surface. */
export interface AskSuggestion {
  id: string;
  surface: AskSurface;
  question: string;
  /** Optional semantic intent used to group equivalent wording. */
  intent?: string;
  /** Entity context required before this question should be displayed. */
  requires: Array<'ticker' | 'thesisClaimId' | 'evidenceId' | 'whatIfAction'>;
}
