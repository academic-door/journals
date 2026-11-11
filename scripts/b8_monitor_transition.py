"""Exact, reversible B8 first-probe warning acceptance.

A public title is not operationally healthy until first Monitor probe.
Only a known scoped transition may be accepted as degraded; historical
soft warnings are not silently cleared, and any novel warning/failure
remains a Public Health failure.
"""
from __future__ import annotations


PENDING_TITLE_BY_COUNT = {
    51: "JAE",
    52: "ITPF",
    53: "JOEG",
    54: "IJIO",
    55: "JFI",
    56: "JIMF",
    57: "EJPE",
}
# Nov 4 live 53-journal Monitor reported exactly two ongoing soft warnings
# for publisher roster/translation gates. The 54th IJIO transitional snapshot
# preserves these (not falsely green) and adds IJIO first-probe warning.
IJIO_KNOWN_SOFT_WARNINGS = frozenset({"ENERGY", "LABECO"})


def qualified_pending_b8_warning(expected: int, monitoring: dict) -> bool:
    title = PENDING_TITLE_BY_COUNT.get(expected)
    warnings = monitoring.get("warning_journals")
    summary = monitoring.get("summary")
    if (
        title is None or monitoring.get("status") != "degraded"
        or not isinstance(warnings, list)
        or not all(isinstance(w, str) and w for w in warnings)
        or len(warnings) != len(set(warnings))
        or not isinstance(summary, dict)
        or summary.get("warnings") != len(warnings)
        or summary.get("failed") != 0
        or monitoring.get("failed_journals") != []
    ):
        return False
    warning_set = set(warnings)
    if expected == 57:
        # The final B8 title accepts only its exact first-probe warning.
        return (
            monitoring.get("b8_transition_note")
            == "EJPE bilingual source READY; first operational probe pending"
            and warning_set == {"EJPE"}
        )
    if expected == 56:
        return (
            monitoring.get("b8_transition_note")
            == "JIMF bilingual source READY; first operational probe pending"
            and warning_set == {"JIMF"}
        )
    if expected == 55:
        # Only JFI's exact first operational warning is temporarily eligible.
        return (
            monitoring.get("b8_transition_note")
            == "JFI bilingual source READY; first operational probe pending"
            and warning_set == {"JFI"}
        )
    if expected == 54:
        if monitoring.get("b8_transition_note") != (
            "IJIO bilingual source READY; first operational probe pending"
        ):
            return False
        # Strictly two possible accepted states: newly pending IJIO alone,
        # or IJIO plus the precise two operational warnings present in the
        # last successful independent 53-journal Monitor run.
        return warning_set in (
            {"IJIO"},
            {"IJIO", *IJIO_KNOWN_SOFT_WARNINGS},
        )
    return warning_set == {title}
