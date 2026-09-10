# R3 QE audit-semantics evidence — 2026-09-10

## Scope

Measured-scope R3 reconciliation for the nine QE `state entry absent from discovery snapshot` findings remaining after the AER/AERI/RES state repair.

## Read-only current-data probe

- Workflow run: `34507547448`
- Branch: `probe/r3-qe-state-20260910`
- Result: success
- Data source: current `data` branch fetched by the GitHub-hosted runner

The probe established two distinct cases:

1. `qe-14-1..4` and `qe-15-1..4` are legitimate 2023–2024 canonical archive inventory. All eight have READY state, matching public archive JSON, and matching entries in `public/api/v1/journals/qe/issues/index.json`.
2. `qe-17-4` has no archive and is explicitly recorded in `expected_issue_exclusions` as `not_yet_published`. The authoritative QE observation for 2025–2026 contains `qe-16-1..4` and `qe-17-1..3` only.

Deleting these state rows would either discard valid historical archive inventory or erase an explicit not-yet-published checkpoint. Expanding discovery from static configured ranges would violate the R1B authority contract.

## Decision

Keep authoritative discovery as the measured expected set and keep canonical archive inventory as a separate concept. The strict audit may tolerate a non-expected state row only when either:

- the row has an explicit `expected_issue_exclusions` entry; or
- it is independently proven canonical inventory: READY, source-verified archive exists, matching index entry exists, and state/archive/index readiness fields agree.

All other extra state rows remain audit errors. This deliberately does not mask stale ERE compound identities that have no archive/index.

## Acceptance target

- No QE state/discovery/archive mutation.
- Unit tests cover valid inventory-only rows, missing-index rows, unbacked orphan rows, and explicit exclusions.
- Current-data audit should remove exactly the nine QE false-positive/or-explicit-exclusion orphan findings while preserving unrelated errors and expected-set coverage counts.
