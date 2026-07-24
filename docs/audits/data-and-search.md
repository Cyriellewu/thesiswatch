# Data and Search / Evidence Audit (READ-ONLY)
Model: Gemini 3.1 Pro · 2026-07-25

## 1. Current evidence flow & trust gaps
Evidence is generated offline in `tasks/thesis_autoevidence.py` (conviction factors, guru overlap, concentration flags, news-presence flag) as text strings with `auto:` source prefixes, merged with stored user evidence. `/api/evidence/{ticker}` maps these but **injects fabricated timestamps**.
Gaps: **fabricated freshness** (`observedAt`/`fetchedAt` = now); **zero provenance** (news reduced to a boolean "今日出现在相关新闻中", no URL/headline/quote); **superficial conflict detection** (`thesis_explain.py` only checks support_weight>0 AND counter_weight>0, no semantic mapping).

## 2. Source priority ladder
1. **SEC / Company IR / earnings** — financial ground truth. *Adapter exists: SECEdgarFetcher.*
2. **Federal Register / FRED / official gov** — macro/regulatory. *Adapter missing.*
3. **Reliable structured market-data** — price/volume. *Adapters: Finnhub, YFinanceNews.*
4. **Trusted news/RSS** — human-processed events. *Adapter: Reuters.*
5. **General web search** — discovery of candidate sources ONLY, never a primary citation. *Missing.*
6. **LLM** — extract/explain validated evidence only, never assert facts. *Integrated; needs strict UI isolation.*

## 3. Provider abstraction
Pipeline: `Source Adapter → Evidence Normalizer → Provenance Validator → Claim Matching`.
Evidence fields: `url`, `publisher`, `authority_tier` (1 SEC … 6 LLM), `observed_at`, `fetched_at`, `quote` (verbatim, no LLM rewrite), `stance` (support|counter), `confidence` (from tier), `resolvable` (bool, live ping).

## 4. Quality metrics (targets)
Citation resolvability 100%; freshness accuracy <1h delta; duplicate rate <5%; source authority mix >60% Tier 1–3; contradiction coverage >20%; unsupported-claim rate 0%; cost <$0.01/node; latency <30s from release to update.

## 5. Fact vs model separation contract
- **[FACT]** Tiers 1–3 only; requires `url` + verbatim `quote`; rendered as immutable.
- **[MODEL INTERPRETATION]** Tier 4 (news/analyst/guru); render with attribution ("According to Reuters…").
- **[ASSUMPTION]** user-entered (kind=user); subjective, needs future validation.
- **[COUNTERARGUMENT]** any of above with stance=counter; explicitly highlighted.

## 6. Tonight's minimal honest step
In `api_server.py` `/api/evidence/{ticker}`, **stop fabricating timestamps**: remove hardcoded `datetime.now()` for observedAt/fetchedAt; if unknown set `null` and attach `qualityNote="unverified: missing provenance timestamp"`.
Verified free public APIs (web_fetch confirmed):
- FRED — https://fred.stlouisfed.org/docs/api/fred/
- SEC EDGAR — https://www.sec.gov/edgar/sec-api-documentation (free REST via data.sec.gov)
- Federal Register — https://www.federalregister.gov/developers/api/v1
