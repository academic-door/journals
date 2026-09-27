# Public Composer Migration Inventory — G12

Status: **PREP / FREEZE ONLY**  
Owner: **④ 期刊体系 | 总控** for the public Journals side  
Cross-subsystem counterpart: **⑥ Composer | 总控**  
Parent dependency: final public product-map / naming / navigation decision

This document inventories the current public Composer implementation in
`academic-door/journals`. It does **not** authorize the final public cutover.
Current canonical governance keeps the full authenticated publication workbench
in private Composer and leaves Journals with only the public Preview / Entry
surface plus upstream identity handoff.

## Freeze rule

Until the Parent/⑥ terminal public contract is accepted:

- do not add new editor, renderer, theme, custom-CSS, copy/export, draft-state,
  publication-history, or private-workflow capability to the public Journals
  Composer;
- correctness/security fixes may still be made when required;
- do not remove the context-preserving Journals → private Composer handoff
  contract (`journal` / `issue`) without replacement evidence.

## Current public implementation to retire or relocate

### `src/pages/composer/index.astro`

Current public page materially implements a workbench, including:

- journal / issue selection;
- article selection and ordering;
- Markdown editing and regeneration;
- phone/WeChat preview;
- typography / line-height / accent controls;
- custom CSS scoping/filtering;
- local draft/settings persistence;
- rich-text clipboard conversion;
- Markdown copy/export;
- HTML export;
- publication-readiness gating;
- issue-history loading and deep-link handling.

**Terminal direction:** the workbench-owned implementation belongs in private
`academic-door/academic-door-composer`. Public Journals may retain only a
bounded read-only Preview / Entry surface after the cross-subsystem contract is
accepted.

### `src/pages/themes/index.astro`

Current public Theme Lab exposes formal and experimental themes plus a live
switcher.

**Terminal direction:** retire or relocate Theme Lab with the private Composer
workbench. A future public capability showcase, if desired, should publish only
bounded static/read-only output rather than the experimental theme engine.

### `src/styles/global.css`

Composer-specific selectors include workbench layout, preview, style controls,
theme classes and Theme Lab styling.

**Migration rule:** remove only selectors proven unused after the public
workbench/theme route is replaced. Do not perform a broad stylesheet cleanup in
advance of the accepted public replacement.

### `src/layouts/Layout.astro`

Current primary navigation exposes `微信编辑器`.

**Decision dependency:** final removal/renaming/navigation placement waits for
the Parent public-portal / shared-navigation decision. No new first-class
public workbench positioning should be added meanwhile.

### `src/components/Top5Explorer.astro`

Current Journals reader surface builds a context link to public Composer.

**Must preserve across migration:** canonical `journal` / `issue` identity
handoff. The terminal link target/label may change to a bounded public entry or
authenticated private Composer according to the accepted cross-subsystem
contract.

## Public tests/contracts that currently freeze obsolete ownership

### `tests/test_composer_ui.py`

The current suite intentionally asserts many full-workbench features in the
public Journals repository: style controls, custom CSS, clipboard/export,
selection/order, history picker, themes and workspace layout.

**Migration rule:** after replacement acceptance, replace these assertions with
boundary regression tests that prove:

1. public Journals no longer ships editing/custom-CSS/copy-export/theme-engine
   workbench code;
2. the public Preview / Entry surface is read-only;
3. `journal` / `issue` identity survives the handoff;
4. private/authenticated workbench ownership is not reintroduced here.

### `README.md`, `AGENTS.md`, `docs/architecture.md`

These current public repo contracts still describe Journals as owning the full
Composer implementation.

**Migration rule:** reconcile them in the same accepted cutover PR so future
agents do not rebuild the old public workbench from stale contracts.

## Replacement evidence already available

Durable owner history already records replacement-first private Composer work:

- Journals issue #231 — private Composer materialization handoff;
- Journals issue #233 — accepted READY-email migration / private workbench
  destination;
- current canonical topology — ⑥ owns authenticated private workbench UX,
  renderer/theme/copy-export/publication ledger/history; ④ owns public
  Preview / Entry and upstream journal identity/data.

The final cutover still waits for the current Parent public product-map decision
and any necessary ④↔⑥ confirmation of the terminal entry/showcase contract.

## Safe public showcase boundary

If the final product decision retains a public Composer capability page, the
Journals side should ship only material that is intentionally public, such as:

- static screenshot(s) or pre-rendered representative output;
- capability description;
- read-only canonical public sample metadata;
- optional context-preserving authenticated entry link.

It should not ship the private renderer, template engine, custom CSS logic,
draft state, copy/export pipeline, theme experimentation engine, or publication
history/runtime.

A public screenshot/output can always be copied as an image. The meaningful IP
boundary is therefore **implementation/runtime non-disclosure**, not trying to
make public pixels impossible to save.

## Acceptance gate for final public cutover

Do not execute the final removal until all are true:

1. Parent resolves the public portal / shared navigation / product naming
   dependency relevant to this surface;
2. ⑥ confirms the private workbench remains the accepted terminal runtime and
   deep-link target;
3. a bounded public Preview / Entry or showcase contract is explicit;
4. Journals tests/contracts are updated in the same change;
5. production verification confirms TOP5/Field/Search/API remain independent of
   the retired public editor;
6. accepted `journal` / `issue` identity handoff still works.

Until then: **freeze, inventory, and avoid new migration debt.**
