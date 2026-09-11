from pathlib import Path

import yaml


path = Path("config/field-history.yml")
data = yaml.safe_load(path.read_text(encoding="utf-8"))

bindings = {
    "AJAE": "data/provenance/expected-set-observations/ajae-2025-2026.json",
    "ECTA": "data/provenance/expected-set-observations/ecta-2025-2026.json",
    "IER": "data/provenance/expected-set-observations/ier-2025-2026.json",
    "TE": "data/provenance/expected-set-observations/te-2025-2026.json",
    "RAND": "data/provenance/expected-set-observations/rand-2025-2026.json",
    "JF": "data/provenance/expected-set-observations/jf-2025-2026.json",
}

for journal, evidence_path in bindings.items():
    definition = data["journals"][journal]
    definition["observed_evidence_path"] = evidence_path
    definition["allowed_host"] = "onlinelibrary.wiley.com"

path.write_text(
    yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
    encoding="utf-8",
)
