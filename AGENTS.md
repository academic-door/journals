# Agent working agreement

## Mission

Build the Academic Door unified journal data engine, TOP5/Field Journals
public site, and Composer. Optimize for a 10–20 minute human publishing flow.

## Required boundaries

- Do not modify the `academic-door.github.io`, `nber-working-papers-cn`, or
  `econ-paper-monitor` repositories from this repository.
- Do not add Notion or WeChat API as a required publishing step.
- Never commit credentials, private drafts, local absolute paths, PDFs, or raw
  publisher HTML.
- Official issue pages determine issue membership and article order.
- Crossref may enrich missing fields but must not determine the issue roster.
- Missing data must remain visible; never fabricate metadata or translations.
- Code changes use a branch and pull request. Generated data is validated
  before deployment.

## Required verification

- Run Python tests.
- Run the Astro build.
- Validate public JSON against the schema.
- Confirm no secrets or local absolute paths are staged.
- After deployment, read back the site, data API, Composer, health endpoint,
  and project manifest.
