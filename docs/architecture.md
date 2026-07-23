# Architecture

```text
Official sources
→ collectors
→ normalization and enrichment
→ translation
→ quality gate
→ public issue JSON
→ journal site / Composer / feeds / project manifest
```

## Source authority

1. Official issue page: roster and order.
2. Official article page: title, authors, abstract, DOI.
3. Crossref: enrichment only.
4. Other public metadata: explicit fallback with provenance.

## Minimal issue states

- `detected`
- `incomplete`
- `ready`
- `error`

The public site may show incomplete data with a visible warning. Composer may
load it for manual repair. The pipeline must never silently stall.
