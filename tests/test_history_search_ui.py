from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class HistoryAndSearchUiTests(unittest.TestCase):
    def test_public_reader_exposes_history_selector(self) -> None:
        source = (ROOT / "src" / "components" / "Top5Explorer.astro").read_text(
            encoding="utf-8"
        )
        self.assertIn('id="issue-history-select"', source)
        self.assertIn("fetchIssueIndex", source)
        self.assertIn("latest_issue_id", source)

    def test_search_page_lazy_loads_generated_indexes(self) -> None:
        page = (ROOT / "src" / "pages" / "search" / "index.astro").read_text(
            encoding="utf-8"
        )
        script = (ROOT / "public" / "search.js").read_text(encoding="utf-8")
        self.assertIn('id="global-search-history"', page)
        self.assertIn("api/v1/search/", script)
        self.assertIn('id="global-search-volume"', page)
        self.assertIn('id="global-search-issue"', page)
        self.assertIn('form?.addEventListener("submit"', script)
        self.assertNotIn("records={", page)

    def test_main_navigation_links_to_search(self) -> None:
        source = (ROOT / "src" / "layouts" / "Layout.astro").read_text(
            encoding="utf-8"
        )
        self.assertIn('href={`${base}search/`}', source)


if __name__ == "__main__":
    unittest.main()
