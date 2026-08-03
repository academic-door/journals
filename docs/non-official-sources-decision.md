# Non-official source policy and decision record

Status date: 2026-08-03 (data snapshot 581/581 complete, translations 100%)

## Decision

Academic Door keeps all 41 enabled journals. "Non-official transport" is an
accepted, monitored state for a defined set of journals; it is never a reason
to drop a journal, and it is tracked separately from real failures. The hard
gate stays "content complete" (roster, abstracts, translations), not "transport
class". Any journal whose collector reaches the official issue page (or whose
browser-confirmed order has a live provenance-ledger entry) is reported as
official.

## Categories (current live health, after 2026-08-03 verification)

| Category | Journals | Count | Action |
|---|---|---|---|
| Official transport | aer, qje, jde, jue, eer, jep, aeri, geb, jet, aejmicro, jebo, jie, jfe, aejapp, aejpol, jme, aejmacro, joe, foodpolicy | 19 | none |
| Browser-verified Elsevier (official) | jpube, jeem (verified 2026-08-03, ledger entries added) | 2 | health flips to official after deploy |
| Awaiting official confirmation | ajae, jf, rfs | 3 | no action; auto-promote when official page roster confirms (Crossref already has the issue) |
| Elsevier, next browser-verify candidates | wd, lup | 2 | verify with the existing logged-in browser session; same flow as the other 13 |
| Accepted non-official | jpe, res, ecta, ej, jeea, ier, te, rand, jeh, restat, qe, jle, jaere, landecon, ere | 15 | keep; monitored; see migration notes |

Total: 41.

## Why these 15 are accepted

Their collectors use publisher-operated feeds and metadata that the publisher
controls (Oxford University Press RSS, Wiley RSS, Chicago RePEc, MIT Press /
Cambridge Crossref metadata, Springer RePEc). These are not scraped third-party
mirrors. The risk is confined to roster/order authority (a feed can lag or
reorder), which `audit_source_alignment --strict` and the order-preservation
tests guard. Content completeness and translation quality are not affected.

## Rules

1. A journal claiming `official_verified` order must have a current
   provenance-ledger entry (`data/provenance/order-verification.json`) for the
   same issue. CI runs `audit_public_data.py --strict-provenance`.
2. `awaiting_official` (Crossref has a new issue, official page not yet) is not
   a failure: no issue, no alert, longer backoff. It only escalates after 7 days.
3. Journals on accepted non-official transports keep `degraded` site status but
   must keep `content_status: complete`; losing completeness raises a real alert.
4. Revisit this list quarterly, or when a publisher opens an official feed.

## Migration notes (non-blocking)

- wd, lup: browser-verify issue order (same flow as the 13 Elsevier journals).
- jpe, jle, jaere (Chicago): RePEc serial pages are the publisher path; revisit
  if Chicago exposes a native TOC API.
- res, ej, jeea, rfs (OUP): investigate the OUP RSS-to-issue mapping once
  Crossref volume rolls are confirmed for the new issue.
- ecta, ajae (Wiley): awaiting official/new-issue handling; re-check each cycle.
- ier, te, rand, qe, ere (RePEc): RePEc is stable; keep as-is unless a gap is
  observed.
- jeh, restat, landecon (Crossref): Crossref-only roster is accepted for these;
  official page confirmation is out of scope unless the site needs it.
