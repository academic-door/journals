# R3 ERE compound-issue evidence — 2026-09-10

## Scope

ERE 2023 historical reconciliation for the two remaining compound-issue findings:

- `ere-85-3-4`
- `ere-86-1-2`

## Current publisher truth

Springer Nature's live ERE archive identifies the 2023 issues as:

- Volume 85, Issue 3-4 — August 2023 — canonical issue page `/journal/10640/volumes-and-issues/85-3`
- Volume 86, Issue 1-2 — October 2023 — canonical issue page `/journal/10640/volumes-and-issues/86-1`

The canonical Springer URL encodes only the first issue number for a combined issue. The complete issue identity appears in the archive link text / issue-page heading.

## Reproduction

Read-only runner probe `34508948509` used the pre-fix parser against the live Springer archive. It returned 22 issues for 2023–2024 but incorrectly emitted `ere-85-3` and `ere-86-1` because the parser trusted the URL suffix.

## Fix verification

Read-only runner probe `34509479679` used the fixed parser against the live Springer archive and returned exactly 22 issues:

- 2023: `ere-84-1..4`, `ere-85-1`, `ere-85-2`, `ere-85-3-4`, `ere-86-1-2`, `ere-86-3`, `ere-86-4`
- 2024: `ere-87-1..12`

It also verified that the official URLs for the two compound identities remain `/85-3` and `/86-1`, and that the incorrect identities `ere-85-3` / `ere-86-1` are absent.

## Safety boundary

The parser accepts a compound label from Springer link text only when its first issue number matches the issue number encoded by the canonical URL. Otherwise it falls back to the URL identity. Allowed-host validation is unchanged.

This PR changes parser semantics only. It does not mutate canonical discovery/state/archive data. The subsequent R3 data tranche must refresh ERE 2023–2024 discovery with this parser before attempting the two missing historical archives.
