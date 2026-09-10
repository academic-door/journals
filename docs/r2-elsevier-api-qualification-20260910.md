# R2 Elsevier API qualification — 2026-09-10

Authority: Parent Decision 0015 / `academic-door-main-control/governance/SHARED_SCHOLARLY_API_POLICY.md`.

Scope: bounded read-only qualification for Journals R2. No `data` mutation, no completeness-authority change, no Semantic Scholar usage expansion, and no scheduled/bulk provider workload was introduced.

## Verdict

**NOT QUALIFIED for authoritative expected-set discovery.**

The current Elsevier credentials and Article Metadata API are valid for known-item metadata recovery, including volume / issue / cover-date fields when the indexed record carries them, but the qualified official API surfaces did not demonstrate complete issue-container enumeration or a complete, repeatable year-level expected set. Under Decision 0015, Elsevier therefore remains metadata / candidate evidence for Journals R2; `crossref_candidate` completeness semantics are unchanged.

## Credential / entitlement evidence

GitHub Actions runner observations confirmed both organization secret bindings were present without exposing their values:

- `ELSEVIER_API_KEY`: configured
- `ELSEVIER_INST_TOKEN`: configured

Known-item Article Metadata queries succeeded with HTTP 200 / `X-ELS-Status: OK`:

- JCE DOI `10.1016/j.jce.2026.01.006` → volume `54`, issue `2`, cover date `2026-06-30`, PII present.
- JDE DOI `10.1016/j.jdeveco.2026.103834` → volume `183`, issue identifier absent, cover date `2026-09-30`, PII present.

This proves the credentials are active for legitimate metadata retrieval. It does not prove issue-container enumeration authority.

## Enumeration / coverage evidence

Article Metadata API diagnostics for JCE used these bounded field-query shapes:

1. `ISSN(0147-5967) AND VOLUME(54)`
2. `ISSN(0147-5967)`
3. `ISSN(0147-5967) AND PUB-DATE AFT 2024 AND PUB-DATE BEF 2027`

All returned HTTP 200 but `X-ELS-Status: NO_SEARCH_RESULTS(Result set was empty)` and `totalResults=0`. Because a known JCE DOI from volume 54 is retrievable through the same credentials and endpoint, the successful known-item path cannot be treated as evidence that year/ISSN enumeration is complete.

The Elsevier Serial Title API is documented as serial-title metadata lookup/search by ISSN (Scopus content), not an issue-container/history enumeration API. The ScienceDirect API specification likewise exposes serial metadata, article retrieval/search, and related metadata surfaces, but no dedicated complete issue-list endpoint was identified during this qualification.

Therefore the qualification cannot establish:

- exhaustive historical issue identity for a year,
- absence of zero-article or otherwise unreturned issues,
- complete issue membership from an independently complete issue set,
- a publisher-authoritative current expected issue set / freshness boundary.

## Immutable observations / request evidence

### Run 34499485933 — Article Metadata year/ISSN qualification

Generated/observed window: `2026-09-10T16:02:00Z`–`2026-09-10T16:02:04Z`.

Request fingerprints (SHA-256 of normalized non-secret query params):

- JCE 2025: `603fa6535bf275d41a937f7191e6943e52d4007641f72cddf898e31922377b71`
- JCE 2026: `acb4ba8237be39399cba49bee2ce37720dc3cb87a69bc3fdc93662bc01b94a2a`
- ECOLECON 2025: `8b7eb06ad6976e9cc95cd8d60cff4a72b15917f2792f773ec6ea69b8d051fb81`
- ECOLECON 2026: `71c9a219a3283218bed70544bd78c13f45e942e41b95489d7fbea4241d469c51`

Five request attempts were made in this run, including one bounded JCE repeat. The apparent repeatability was an empty-result repeat and is explicitly **not** accepted as expected-set repeatability.

Quota telemetry: weekly limit header `20000`; remaining decreased from `19731` to `19727`; reset header `1789129211`. This was far above the Decision 0015 25% reserve.

### Run 34499651237 — query-shape diagnostic

Observed at: `2026-09-10T16:03:33Z`.

Request fingerprints:

- known volume: `fe14f7b7a57afac7ae459638b2a1ed47c68aa9bbc4bdd82617f5d448ace3f5c6`
- ISSN only: `41b2c2ff2ee4cb7f80e6f430556cc4c91e01cec9cd22928142022a37818b7cb5`
- broad year window: `5f9b18c7b9786a8858a729cfc97780b314a2d0b4088ed7319a0ee269d48cbb1e`

All three returned HTTP 200 / zero results. Quota remaining decreased `19726` → `19724`.

### Run 34499999985 — known-item control

Observed at: `2026-09-10T16:06:46Z`.

Request fingerprints:

- JCE known DOI: `b3955aec7c6e772aa49c18047ebc9b6c7cbee431e1033ef75970a3a82342e130`
- JDE known DOI: `8d1d058effca961565785696b1af67a1abc3b46c9d74615ec67546ea9b5f879c`

Both returned one valid record with HTTP 200 / `X-ELS-Status: OK`. Quota remaining `19723` → `19722` of `20000`.

## Quota / retention policy check

The **current** Elsevier API Service Agreement (as read on 2026-09-10) does not state a fixed inactivity-retention interval. It reserves Elsevier's right to monitor usage and throttle, suspend, or deactivate the API Service for suspected unauthorized use, and the term continues until credentials are deactivated or the applicable institutional agreement terminates.

A 90-day unused-key deactivation clause appears in the archived 2024 API Service Agreement, and older TDM-specific terms contain different inactivity language. Those archived/specialized provisions are **not treated as the current general API retention contract**.

Therefore there is no current evidence requiring an artificial keepalive cadence. Existing legitimate Journals metadata/recovery use and this bounded qualification also demonstrate present activity. No new keepalive workload is introduced.

Decision 0015 controls remain:

- priority: `LIVE_FRESHNESS > CURRENT_REPAIR > ROUTINE_MONITOR > HISTORICAL_BACKFILL`;
- preserve at least 25% provider quota for live/current work;
- routine/backfill work stops at or below the reserve;
- automated request rate remains below the policy ceiling;
- no Semantic Scholar scheduled/bulk expansion while the Parent pressure checkpoint remains active.

## Next safe action

Do not change Elsevier journals from `crossref_candidate` on the basis of these APIs. Continue using Elsevier official APIs where they legitimately improve known-item article metadata / issue-membership recovery. Any future attempt to promote Elsevier to expected-set authority requires new evidence of a complete publisher-owned issue enumeration surface or a separately approved observation contract; completeness semantics must not be weakened.