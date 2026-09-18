# Freshness discovery source matrix

Status: current owner-repo architecture reference for the 49 enabled journals.

Runtime truth remains in `config/journals.yml` and `config/mailbox-announcements.yml`.
This document explains the publisher-family routing and authority contract; it is
not a second executable source configuration.

## Authority contract

Freshness discovery has three distinct layers:

1. **Announcement / issue existence** — first-party current/archive pages,
   RSS/Atom, association announcements, or the dedicated project mailbox may
   establish that a newer issue exists. Announcement evidence never asserts
   article membership, order, abstracts, translation completeness, or READY.
2. **Roster / detected content** — a publication-ready official or accepted
   publisher-supplied roster transport must establish article membership and
   order before a new issue may be treated as canonical detected content.
   A candidate explicitly marked `crossref_provisional_roster` remains
   source-pending even when an announcement matches its volume/issue.
3. **READY** — the existing content, provenance, source-alignment, translation,
   privacy, and publication gates all remain mandatory. Announcement evidence
   cannot bypass them.

Reader-facing latest selection keeps the last READY issue available while a
newer first-party announcement may be exposed separately as
`latest_announced_*`. A stale or lower-authority detected snapshot cannot
replace a same/newer READY snapshot.

The project-mailbox lane is read-only and announcement-only. It uses readonly
IMAP selection plus `BODY.PEEK[]`, persists no message body/subject, and
requires sender-domain + subject + official-link-host allowlists.

## Publisher-family matrix

| Publisher family | Enabled journals | Primary discovery / issue surfaces | Additional first-party announcement lane | Publisher-supplied / audited fallback | Roster authority notes |
| --- | --- | --- | --- | --- | --- |
| American Economic Association | AER, JEP, AERI, AEJMICRO, AEJAPP, AEJPOL, AEJMACRO | AEA current-issue pages | None configured | Crossref fallback where configured | Official AEA issue collector remains the canonical roster path; fallback does not weaken existing gates. |
| University of Chicago Press | JPE, JLE, JAERE | UChicago current TOC; JPE also has official RSS | Mailbox allowlist: JPE, JAERE | Publisher-supplied RePEc series configured for JPE/JLE/JAERE | RePEc serial sections may carry publisher-supplied roster authority; mailbox is issue-existence only. |
| Oxford University Press | QJE, RES, EJ, JEEA, RFS | OUP issue pages + official RSS feeds | None configured | Crossref fallback | Official RSS/issue alignment may establish publisher roster; Crossref-only candidates remain subject to current collector policy. |
| Wiley | ECTA, AJAE, IER, TE, RAND, JF, QE | Wiley current TOC; official RSS where configured; RePEc-primary collectors for IER/TE/RAND/QE | ECTA: Econometric Society volume page; RAND: Wiley recent-issues page; mailbox allowlist: ECTA, RAND | Publisher-supplied RePEc series where configured; Crossref fallback on selected titles | Announcement sources prove issue existence only. RePEc serial pages can be publication-ready when the issue section exists and the collector stamps `roster_authority=repec-publisher-supplied`. |
| Elsevier / ScienceDirect | JDE, JPubE, JEEM, JUE, EER, GEB, JET, JEBO, JIE, JFE, WD, JME, JOE, FOODPOLICY, LUP, JHE, JCE, CER, ENERGY, ECOLECON, LABECO, RED, JEDC | ScienceDirect official issue/current surfaces; official browser evidence for bounded blocked cases | None configured in mailbox v1 | RePEc serial URLs are configured for the family; audited ScienceDirect API/browser evidence is used where applicable | Exact official order/provenance gates remain mandatory; browser evidence is bounded bibliographic evidence, never credentials/session payload. |
| Springer | ERE | Springer volumes/issues page | Mailbox allowlist: ERE | Publisher-supplied RePEc series `kap/enreec`; Crossref fallback | RePEc is the current machine-usable roster transport; mailbox remains announcement-only. |
| Cambridge University Press | JEH | Publisher latest-issue page | None configured | Crossref collector | Existing source-alignment policy governs accepted transport; no new announcement authority is introduced here. |
| MIT Press | RESTAT | Publisher issue page | None configured | Crossref collector | Existing source-alignment policy governs accepted transport. |
| University of Wisconsin Press | LANDECON | Publisher current page | None configured | Crossref collector | Existing source-alignment policy governs accepted transport. |

Inventory count: **49 enabled journals**.

## Current bounded mailbox allowlist

The dedicated project mailbox may emit normalized `official_newsletter`
announcements only for:

- JPE
- ECTA
- RAND
- JAERE
- ERE

The canonical sender/subject/link-host rules are in
`config/mailbox-announcements.yml`. Adding another journal or changing the
mailbox authority envelope requires the same permission/security review used
for the original mailbox lane.

## Operational invariants

- `data/monitoring/state.json` is the durable monitor state on the `data`
  branch.
- `public/api/v1/monitoring.json` is the public monitor-health projection.
- Collection JSON exposes `latest_ready_*`, `latest_detected_*`,
  `latest_display_*`, and additive `latest_announced_*` fields.
- `audit_source_alignment --strict`, strict provenance checks, freshness
  invariants, privacy audit, and the full test suite remain the acceptance
  gates.
- `awaiting_official` is a source-readiness state, not an operational failure.
  The monitor applies bounded retry/backoff and preserves the last READY issue.

## Current reference case: ECTA 94(5)

As of 2026-09-18, Econometrica Vol. 94 No. 5 is a useful reference for the
layering contract:

- the Econometric Society volume page provides first-party
  `association_announcement` evidence for Vol. 94 No. 5 / September 2026;
- the last READY reader issue remains Vol. 94 No. 4;
- Crossref exposes a 94(5) candidate, but it is explicitly provisional and is
  not promoted from announcement + metadata alone;
- the configured publisher-supplied RePEc serial had not yet exposed 94(5), so
  the monitor correctly remained `awaiting_official` and applied backoff.

This is expected behavior, not a freshness-architecture regression.
