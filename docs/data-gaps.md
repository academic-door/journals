# Data gap register

Known gaps that are visible on the site/API but are not yet filled. This file
is the honest record: gaps are tracked here instead of being silently patched
with fabricated data.

## EER Vol. 188 archive gap

- Status: open
- Discovered: 2026-08-03
- Evidence: `public/api/v1/journals/eer/issues/` archives `eer-187-c.json` and
  `eer-189-c.json`, but no `eer-188-c.json`. Vol. 189-C is the current
  continuous-publishing issue; Vol. 188-C was skipped by the archive rotation.
- Impact: none on current data (581/581 complete); historical archive has a
  gap for one EER issue.
- Why not auto-filled: the existing backfill pipeline only covers TOP5
  journals (AER/JPE/QJE/RES/ECTA). Building an Elsevier history fetcher is a
  real feature, not a small fix, and Crossref-only data would violate the
  official-roster quality gate for a publishable issue.
- Options:
  1. Reuse the browser-authorized snapshot flow to capture the archived Vol.
     188 issue page and import it as a history issue (most faithful).
  2. Extend `backfill_history.py` to Elsevier continuous-publishing journals.
- Do not close this entry until one option lands or the gap is accepted as
  permanent with a note here.

## Field journal history backfill (2025-2026) status

- Status: machinery shipped, data-source coverage partial.
- Shipped: `config/field-history.yml` (15 Elsevier field journals),
  Crossref volume discovery in `collectors/history.py`, an `elsevier` branch in
  `scripts/backfill_history.py`, resumable `backfill-field-history.yml`
  workflow, and a completeness guard
  (`history_completeness_block`) that refuses to archive a history volume with
  under half the journal's current issue size.
- Pilot (JDE 2025): pipeline collects, translates and archives end to end, but
  Crossref/OpenAlex coverage for some Elsevier continuous volumes is
  incomplete. Evidence: JDE Vol. 173 has only 4-7 items in Crossref/OpenAlex
  while neighbouring volumes have 34-45. The first archived
  `jde-173-c.json` (4 articles) was withdrawn from the data branch on
  2026-08-03 and its backfill state is marked blocked.
- Conclusion: Crossref-only auto-backfill is safe only where the completeness
  guard passes (volumes with plausible counts). Volumes that fail the guard
  need the official issue page via the browser-authorized capture flow.
- JDE 172-C remains translation_partial and can be resumed; the remaining
  volumes should be re-run with the guard in place before any rollout to the
  other 14 field journals.


## Elsevier abstract source upgrade (2026-08-03)

- Status: partial
- Switch: `update_journals.py --re-enrich-elsevier` (workflow input
  `re-enrich-elsevier`), added 2026-08-03, tested in CI.
- Done: 10 of 15 Elsevier journals now source abstracts from
  `elsevier-article-metadata` (186 articles); each article carries the
  `X-RateLimit-*` snapshot in `sources.abstract_lookup.rate_limit`. Measured
  weekly usage after the pass: 1,604 / 20,000 metadata requests (8%), reset
  2026-08-05.
- Resolved during the same day (with root-cause fixes):
  - EER, JET: Elsevier metadata appended the acknowledgment footnote to the
    abstract with a fused marker (`...run is low11The authors are grateful...`),
    whose digit tripped the translation numeric gate. Fixed by stripping
    appended footnotes from Elsevier abstracts
    (`_strip_abstract_footnotes`) and normalizing written month/number words in
    the Google fallback (`_normalize_written_number_translations`). Both now
    publish from `elsevier-article-metadata` with 0 translation failures.
  - WD, LUP: the in-progress October 2026 cover date was outside the
    `publication_lead_months: 1` horizon, so every CI re-collection failed
    since 2026-07-29. Set `publication_lead_months: 2` for both; they now
    re-collect and refresh from the Elsevier API (quota snapshots recorded).
- Still open, not a code bug:
  - GEB: one new article (`10.1016/j.geb.2026.07.006`, "Which probability
    measures are strict correlated equilibria?") has no abstract in any source
    yet (Crossref, Elsevier Article Metadata/Search/Scopus, OpenAlex all
    return none). The publication gate correctly preserves the complete
    21-article issue; the 22nd article publishes automatically when Elsevier
    indexes its abstract.
- Impact: none on published data (all 581 abstracts and translations remain
  complete).

