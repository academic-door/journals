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
