import type {
  DailyBrief,
  Evidence,
  EvidenceConflict,
  Holding,
  PortfolioExposure,
  StockThesis,
  WhyChanged,
} from "../types";

/* Mock data for the prototype. Shapes conform to design/contracts.ts. */

const msft: Holding = {
  ticker: "MSFT", name: "Microsoft", conviction: 78, prevConviction: 71,
  status: "watch", confidence: "Medium-high", coveragePct: 82, positionSize: "moderate",
  horizonMonths: 18, price: 462.11, dayChangePct: 0.4, currency: "USD", weightPct: 21,
  priceObservedAt: "2026-07-24T20:10:00Z", dataState: "ok", cardState: "default",
};

const nvda: Holding = {
  ticker: "NVDA", name: "NVIDIA", conviction: 64, prevConviction: 71,
  status: "re_evaluate", confidence: "Medium", coveragePct: 74, positionSize: "large",
  horizonMonths: 18, price: 172.3, dayChangePct: -2.1, currency: "USD", weightPct: 19,
  priceObservedAt: "2026-07-24T20:10:00Z", dataState: "ok", cardState: "default",
};

const googl: Holding = {
  ticker: "GOOGL", name: "Alphabet", conviction: 69, prevConviction: 69,
  status: "no_material_change", confidence: "Medium", coveragePct: 70, positionSize: "moderate",
  horizonMonths: 24, price: 201.5, dayChangePct: 0.9, currency: "USD", weightPct: 12,
  priceObservedAt: "2026-07-24T20:10:00Z", dataState: "ok", cardState: "default",
};

const qqq: Holding = {
  ticker: "QQQ", name: "Invesco QQQ Trust", conviction: 72, prevConviction: 72,
  status: "no_material_change", confidence: "Medium-high", coveragePct: 88, positionSize: "moderate",
  horizonMonths: 24, price: 611.2, dayChangePct: -1.2, currency: "USD", weightPct: 16,
  priceObservedAt: "2026-07-24T20:10:00Z", dataState: "stale_data", cardState: "default",
};

const avgo: Holding = {
  ticker: "AVGO", name: "Broadcom", conviction: 66, prevConviction: 66,
  status: "no_material_change", confidence: "Medium", coveragePct: 68, positionSize: "small",
  horizonMonths: 18, price: 288.0, dayChangePct: 0.2, currency: "USD", weightPct: 9,
  priceObservedAt: "2026-07-24T20:10:00Z", dataState: "ok", cardState: "default",
};

export const dailyBrief: DailyBrief = {
  date: "2026-07-24",
  overallStatus: "watch",
  needsAttention: [nvda, msft],
  worthWatching: [googl],
  noMaterialChange: [qqq, avgo],
  updatedAgoMinutes: 18,
  dataState: "ok",
};

export const whyChangedByTicker: Record<string, WhyChanged> = {
  MSFT: {
    ticker: "MSFT", prevConviction: 71, currentConviction: 78, status: "watch",
    asOf: "2026-07-24T19:52:00Z", dataState: "ok",
    drivers: [
      { label: "Azure guidance improved", points: 4, sign: "positive", thesisClaimId: "msft-cloud" },
      { label: "Valuation entered preferred range", points: 3, sign: "positive", thesisClaimId: "msft-val" },
      { label: "Positive estimate revisions", points: 2, sign: "positive", thesisClaimId: "msft-cloud" },
      { label: "AI capex uncertainty", points: -2, sign: "negative", thesisClaimId: "msft-capex" },
    ],
    meaningForYou:
      "The new evidence strengthens the growth thesis, but does not remove margin and capex risk. Over your 12–24 month horizon this is a Watch, not an action.",
  },
  NVDA: {
    ticker: "NVDA", prevConviction: 71, currentConviction: 64, status: "re_evaluate",
    asOf: "2026-07-24T19:40:00Z", dataState: "ok",
    drivers: [
      { label: "New export-control policy risk", points: -5, sign: "negative", thesisClaimId: "nvda-policy" },
      { label: "Datacenter demand still strong", points: 2, sign: "positive", thesisClaimId: "nvda-demand" },
      { label: "Valuation extended", points: -4, sign: "negative", thesisClaimId: "nvda-val" },
    ],
    meaningForYou:
      "A core assumption — unrestricted China access — is now in question, and your position is large. This warrants a re-evaluation of position size against your risk tolerance.",
  },
};

export const evidenceByTicker: Record<string, Evidence[]> = {
  MSFT: [
    {
      id: "ev-101", claim: "Azure growth remained above expectations at ~31% cc",
      type: "fact", sourceName: "Microsoft FY26 Q4 earnings transcript",
      sourceUrl: "https://example.com/msft-transcript", observedAt: "2026-07-22T21:00:00Z",
      fetchedAt: "2026-07-24T19:09:00Z", freshness: "fresh",
      interpretation: "Supports the demand component of your MSFT cloud thesis.",
      confidence: "High", thesisClaimId: "msft-cloud", sign: "positive",
    },
    {
      id: "ev-102", claim: "Forward P/E moved into your 28–30x preferred entry band",
      type: "fact", sourceName: "Market data (yfinance)", observedAt: "2026-07-24T20:00:00Z",
      fetchedAt: "2026-07-24T20:05:00Z", freshness: "fresh",
      interpretation: "Valuation is now inside the range you defined as attractive.",
      confidence: "High", thesisClaimId: "msft-val", sign: "positive",
    },
    {
      id: "ev-103", claim: "AI capex may pressure near-term operating margin",
      type: "model_interpretation", sourceName: "AlphaWatch synthesis",
      observedAt: "2026-07-24T19:00:00Z", fetchedAt: "2026-07-24T19:09:00Z", freshness: "fresh",
      interpretation: "A risk to the margin claim; not yet reflected in guidance.",
      confidence: "Medium", thesisClaimId: "msft-capex", sign: "negative",
    },
    {
      id: "ev-104", claim: "Capex guidance range implies continued heavy spend into FY27",
      type: "assumption", sourceName: "Analyst consensus (stale)",
      observedAt: "2026-06-30T00:00:00Z", fetchedAt: "2026-07-01T00:00:00Z", freshness: "stale",
      interpretation: "Older estimate; may not reflect the latest guidance.",
      confidence: "Low", thesisClaimId: "msft-capex", sign: "negative",
      qualityNote: "Source is 24 days old — refresh recommended.",
    },
  ],
  NVDA: [
    {
      id: "ev-201", claim: "New draft export rule could restrict advanced GPU sales to China",
      type: "fact", sourceName: "Federal Register notice",
      sourceUrl: "https://example.com/nvda-policy", observedAt: "2026-07-24T14:00:00Z",
      fetchedAt: "2026-07-24T18:30:00Z", freshness: "conflicting",
      interpretation: "Directly threatens the China-demand assumption in your thesis.",
      confidence: "Medium-high", thesisClaimId: "nvda-policy", sign: "negative",
      qualityNote: "One source reports a carve-out; conflict unresolved.",
    },
    {
      id: "ev-202", claim: "Hyperscaler capex commentary remains robust",
      type: "fact", sourceName: "Three cloud-provider transcripts",
      observedAt: "2026-07-20T00:00:00Z", fetchedAt: "2026-07-24T18:31:00Z", freshness: "fresh",
      interpretation: "Underlying datacenter demand still supports the thesis.",
      confidence: "High", thesisClaimId: "nvda-demand", sign: "positive",
    },
  ],
};

export const conflictsByTicker: Record<string, EvidenceConflict[]> = {
  NVDA: [
    {
      thesisClaimId: "nvda-policy", positive: 1, negative: 2, evidenceIds: ["ev-201", "ev-202"],
      unresolvedReason: "Sources disagree on whether a carve-out exempts current products.",
    },
  ],
};

export const thesisByTicker: Record<string, StockThesis> = {
  MSFT: {
    ticker: "MSFT", status: "watch", oneLiner: "Thesis strengthened this week", conviction: 78,
    confidence: "Medium-high", coveragePct: 82, horizonMonths: 18, dataState: "ok",
    asOf: "2026-07-24T19:52:00Z",
    whatChanged: whyChangedByTicker.MSFT.drivers,
    currentThesis: [
      { id: "msft-cloud", text: "AI demand supports durable double-digit cloud growth." },
      { id: "msft-val", text: "Reasonable valuation for the quality and durability of earnings." },
      { id: "msft-capex", text: "Capex is heavy but should convert to cloud revenue over time." },
    ],
    supporting: [],
    risks: [],
    whatWouldChangeMyMind: [
      "Azure growth below 20% for two consecutive quarters",
      "Operating margin deterioration beyond a set threshold",
      "AI capex rises without corresponding cloud revenue growth",
    ],
    catalysts: [
      { date: "2026-10-28", label: "FY26 Q1 earnings" },
      { date: "2026-09-15", label: "Ignite product event" },
    ],
    decisionHistory: [
      { date: "2026-07-24", from: 71, to: 78, note: "Thesis strengthened" },
      { date: "2026-07-18", from: 76, to: 71, note: "Capex uncertainty" },
      { date: "2026-07-10", from: 74, to: 76, note: "Valuation improved" },
    ],
    conflicts: [],
  },
};
thesisByTicker.MSFT.supporting = evidenceByTicker.MSFT.filter((e) => e.sign === "positive");
thesisByTicker.MSFT.risks = evidenceByTicker.MSFT.filter((e) => e.sign === "negative");

export const exposures: PortfolioExposure[] = [
  { factor: "AI infrastructure", level: "high", holdings: [
    { ticker: "MSFT", weightPct: 21 }, { ticker: "NVDA", weightPct: 19 },
    { ticker: "QQQ", weightPct: 16 }, { ticker: "AVGO", weightPct: 9 },
  ] },
  { factor: "Mega-cap technology", level: "high", holdings: [
    { ticker: "MSFT", weightPct: 21 }, { ticker: "GOOGL", weightPct: 12 }, { ticker: "QQQ", weightPct: 16 },
  ] },
  { factor: "Semiconductors", level: "medium", holdings: [
    { ticker: "NVDA", weightPct: 19 }, { ticker: "AVGO", weightPct: 9 },
  ] },
  { factor: "Interest-rate sensitivity", level: "medium", holdings: [
    { ticker: "QQQ", weightPct: 16 }, { ticker: "MSFT", weightPct: 21 },
  ] },
  { factor: "China policy exposure", level: "low", holdings: [{ ticker: "NVDA", weightPct: 19 }] },
];

export const allHoldings: Holding[] = [msft, nvda, googl, qqq, avgo];
