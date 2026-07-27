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
- Reviewers do not push directly to the author's branch. Request changes and let
  the author push, so the branch protection rule "the most recent push needs a
  review from someone else" cannot deadlock a two-person team.
- Public JSON and RSS under `public/api/v1/` are a contract. Adding a field is
  fine; renaming or removing one needs a note in the pull request body.
- Derived outputs (issue archive copies, RSS feeds, the search index) are
  generated at run time and published through the `data` branch. Do not commit
  them to `main`; `main` keeps a lean baseline for local development.

## Required verification

- Run Python tests.
- Run the Astro build.
- Validate public JSON against the schema.
- Confirm no secrets or local absolute paths are staged.
- After deployment, read back the site, data API, Composer, health endpoint,
  and project manifest.
- Reuse the tokens and component classes in `src/styles/global.css`. A new page
  must not introduce a second set of spacing, radius, colour or type rules.
