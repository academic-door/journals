# R3 ERE compound-issue recovery evidence — 2026-09-10

## Scope

Bounded R3 reconciliation for the two official Springer compound issues that were present in canonical discovery/state but lacked publication-ready archives:

- `ere-85-3-4` — Volume 85, Issue 3-4, August 2023
- `ere-86-1-2` — Volume 86, Issue 1-2, October 2023

No publisher authority semantics were relaxed. The recovery used publisher-owned Springer issue/article pages and the existing official-roster publication gates.

## Implementation prerequisites

Three narrow implementation fixes were accepted before data recovery:

- PR #241 — preserve Springer compound issue labels during official archive discovery.
- PR #242 — accept Springer canonical compound issue routes where the URL carries the first issue number (`3-4 -> /85-3`, `1-2 -> /86-1`) while mismatches fail closed.
- PR #243 — preserve the complete compound issue label in the generic official-roster archive builder instead of splitting the issue ID from the right.

## Recovery execution

Bounded recovery run: GitHub Actions run `34519576994`, job `103013436729`.

The recovery manifest was intentionally built from the authoritative `field-2023-2024.json` discovery shard so stale overlapping operational state could not replace the exact Springer issue URLs. Hard assertions required:

- `ere-85-3-4` -> `https://link.springer.com/journal/10640/volumes-and-issues/85-3`
- `ere-86-1-2` -> `https://link.springer.com/journal/10640/volumes-and-issues/86-1`

Official Springer capture succeeded with no failures:

- `ere-85-3-4`: 8 official research items, 0 excluded.
- `ere-86-1-2`: 10 official research items, 0 excluded.

Both issues then passed archive construction, translation, source verification, privacy, public-data, source-alignment, full unit-test, and snapshot gates. The exact compound identities were preserved through the final archives.

History audit for this bounded recovery improved from 214 errors to 210 errors. All four target errors (archive missing + archive index missing for each issue) were removed; no claim is made here that the remaining 210 legacy errors are all actionable under current authority.

## Canonical data result

Canonical data commit:

`cf60011ae1eb738de630953fdd8b012f32e41711`

It adds:

- official Springer roster evidence for both compound issues;
- publication-ready archives for both compound issues;
- translations required by the publication gate;
- state reconciliation for the same exact issue IDs;
- rebuilt derived journal/search/status indexes.

Independent readback after the push confirmed:

- `ere-85-3-4`: volume `85`, issue `3-4`, August 2023, 8/8 research articles, READY, official Springer source.
- `ere-86-1-2`: volume `86`, issue `1-2`, October 2023, 10/10 research articles, READY, official Springer source.

## Follow-up debt

The repository still contains overlapping historical state windows (`field-2023-2024.json` and `field-2023-2026.json`). This recovery intentionally did not redesign their global precedence. A separate R3 tranche should make discovery/issue observation merging deterministic by freshness/authority rather than file iteration order, with regression coverage and live-data audit before mutation.
