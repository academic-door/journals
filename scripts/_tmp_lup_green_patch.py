from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: anchor count={count}, expected 1")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "collectors/elsevier.py",
    '''def _parse_repec_inventory(content: bytes, series_url: str) -> dict[str, Any]:
    soup = BeautifulSoup(content, "html.parser")
    heading = next(
        (
            node
            for node in soup.find_all("h3")
            if ISSUE_HEADING.search(node.get_text(" ", strip=True))
        ),
        None,
    )
    if heading is None:
        raise ElsevierCollectorError("RePEc serial page has no usable volume heading")
    match = ISSUE_HEADING.search(heading.get_text(" ", strip=True))
    assert match is not None
''',
    '''def _parse_repec_inventory(
    content: bytes,
    series_url: str,
    *,
    expected_volume: str = "",
    expected_issue: str = "",
) -> dict[str, Any]:
    soup = BeautifulSoup(content, "html.parser")
    headings: list[tuple[Any, re.Match[str]]] = []
    for node in soup.find_all("h3"):
        match = ISSUE_HEADING.search(node.get_text(" ", strip=True))
        if match is not None:
            headings.append((node, match))
    if not headings:
        raise ElsevierCollectorError("RePEc serial page has no usable volume heading")

    heading, match = headings[0]
    if expected_volume:
        wanted_issue = expected_issue.casefold()
        exact = next(
            (
                (node, candidate)
                for node, candidate in headings
                if candidate.group("volume") == expected_volume
                and (
                    not wanted_issue
                    or candidate.group("issue").casefold() == wanted_issue
                )
            ),
            None,
        )
        if exact is not None:
            heading, match = exact
''',
)

replace_once(
    "collectors/elsevier.py",
    '''    publication_lead_months: int = 1,
    doi_template: str = "",
    max_workers: int = DETAIL_WORKERS,
''',
    '''    publication_lead_months: int = 1,
    doi_template: str = "",
    expected_volume: str = "",
    expected_issue: str = "",
    max_workers: int = DETAIL_WORKERS,
''',
)

replace_once(
    "collectors/elsevier.py",
    '''        inventory = _parse_repec_inventory(
            _get(session, repec_series_url).content,
            repec_series_url,
        )
''',
    '''        inventory = _parse_repec_inventory(
            _get(session, repec_series_url).content,
            repec_series_url,
            expected_volume=expected_volume,
            expected_issue=expected_issue,
        )
''',
)

replace_once(
    "scripts/update_journals.py",
    '''def collector_for(config: dict[str, Any]) -> Callable[[], dict[str, Any]]:
''',
    '''def collector_for(
    config: dict[str, Any],
    *,
    expected_volume: str = "",
    expected_issue: str = "",
) -> Callable[[], dict[str, Any]]:
''',
)

replace_once(
    "scripts/update_journals.py",
    '''            publication_lead_months=int(config.get("publication_lead_months", 1)),
            doi_template=config.get("doi_template", ""),
        )
    if collector == "crossref":
''',
    '''            publication_lead_months=int(config.get("publication_lead_months", 1)),
            doi_template=config.get("doi_template", ""),
            expected_volume=expected_volume,
            expected_issue=expected_issue,
        )
    if collector == "crossref":
''',
)

replace_once(
    "scripts/update_journals.py",
    '''                issue = collector_for(config)()
''',
    '''                issue = collector_for(
                    config,
                    expected_volume=expected_volume,
                    expected_issue=expected_issue,
                )()
''',
)
