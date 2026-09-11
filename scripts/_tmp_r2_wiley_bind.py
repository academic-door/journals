from pathlib import Path


path = Path("config/field-history.yml")
text = path.read_text(encoding="utf-8")

bindings = {
    "AJAE": (
        "0002-9092",
        "data/provenance/expected-set-observations/ajae-2025-2026.json",
    ),
    "ECTA": (
        "0012-9682",
        "data/provenance/expected-set-observations/ecta-2025-2026.json",
    ),
    "IER": (
        "0020-6598",
        "data/provenance/expected-set-observations/ier-2025-2026.json",
    ),
    "TE": (
        "1933-6837",
        "data/provenance/expected-set-observations/te-2025-2026.json",
    ),
    "RAND": (
        "0741-6261",
        "data/provenance/expected-set-observations/rand-2025-2026.json",
    ),
    "JF": (
        "0022-1082",
        "data/provenance/expected-set-observations/jf-2025-2026.json",
    ),
}

for journal, (issn, evidence_path) in bindings.items():
    anchor = (
        f"  {journal}:\n"
        "    platform: crossref\n"
        f"    issn: {issn}\n"
    )
    replacement = (
        anchor
        + f"    observed_evidence_path: {evidence_path}\n"
        + "    allowed_host: onlinelibrary.wiley.com\n"
    )
    count = text.count(anchor)
    if count != 1:
        raise SystemExit(f"{journal}: anchor count={count}, expected 1")
    text = text.replace(anchor, replacement, 1)

path.write_text(text, encoding="utf-8")
