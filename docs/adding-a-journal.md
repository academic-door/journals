# Adding a journal

1. Add the journal to `config/journals.yml`.
2. Reuse an existing publisher collector when possible.
3. Add a fixture that preserves the official issue order.
4. Add tests for roster count, order, DOI uniqueness, and missing fields.
5. Generate one issue JSON and inspect its quality report.
6. Add the journal to a collection only after the fixture passes.

Do not create a separate website or schema for one journal.
