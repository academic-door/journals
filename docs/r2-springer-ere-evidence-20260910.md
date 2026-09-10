# R2 Springer ERE evidence — 2026-09-10

Scope: bounded singleton Springer-family tranche for `ERE` only.

## Authoritative publisher observation

Official archive: `https://link.springer.com/journal/10640/volumes-and-issues`

GitHub-hosted runner live probe matched the exact 2025–2026 issue inventory:

- 2025 / volume 88: issues 1–12
- 2026 / volume 89: issues 1–9

Expected IDs:

- `ere-88-1` … `ere-88-12`
- `ere-89-1` … `ere-89-9`

Verification run: `34461944824` — live exact-set probe, ERE 2026 publication-readiness check, full unit suite, and bounded implementation persistence all succeeded.

## Authority contract

- ERE moves from Crossref candidate discovery to live Springer official-archive discovery.
- Crossref is not promoted to authoritative expected-set evidence.
- Freshness is produced only by execution-time retrieval of the official Springer archive.
- 2026 archives `ere-89-1` … `ere-89-9` were verified publication-ready before PR creation.
- The durable scheduled caller is bounded to ERE, uses `refresh_discovery_only=true`, and disables new translations.
- No schema, completeness semantics, shared capability, or new runtime/service dependency is introduced.
