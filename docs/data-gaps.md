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

## Elsevier abstract source upgrade (2026-08-03)

- Status: partial
- Switch: `update_journals.py --re-enrich-elsevier` (workflow input
  `re-enrich-elsevier`), added 2026-08-03, tested in CI.
- Done: 10 of 15 Elsevier journals now source abstracts from
  `elsevier-article-metadata` (186 articles); each article carries the
  `X-RateLimit-*` snapshot in `sources.abstract_lookup.rate_limit`. Measured
  weekly usage after the pass: 1,604 / 20,000 metadata requests (8%), reset
  2026-08-05.
- Not converted, with blocker:
  - EER, JET: the rebuilt issue fails the publication gate because exactly one
    translation does not complete per run (`translation incomplete: 15/16` and
    `13/14`); the previous complete issue is preserved. Re-run when the
    translation provider is healthy, or identify the failing article.
  - GEB: the RSS-rebuilt issue has one article with no abstract
    (`abstract_en_incomplete`), so the gate preserves the previous issue.
  - WD, LUP: pre-existing collector failure
    (`RePEc candidate is outside the configured publication horizon: Vol.
    206/169 October 2026`) blocks every re-collection, not just the upgrade.
- Impact: none on published data (all 581 abstracts and translations remain
  complete). The five journals keep their previous official-preview / OpenAlex
  sources until the blockers are resolved.

