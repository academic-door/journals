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

    def test_search_page_loads_index_metadata_in_parallel(self) -> None:
        script = (ROOT / "public" / "search.js").read_text(encoding="utf-8")
        self.assertIn("api/v1/search/index.json", script)
        self.assertIn("Promise.allSettled([", script)
        self.assertIn("populateJournals()", script)

    def test_search_page_uses_china_dedicated_and_year_sliced_indexes(self) -> None:
        script = (ROOT / "public" / "search.js").read_text(encoding="utf-8")
        self.assertIn("api/v1/search/china-latest.json", script)
        self.assertIn("api/v1/search/years/${filters.year}.json", script)
        self.assertIn("api/v1/search/years/${year}.json", script)
        self.assertIn("继续载入更早年份", script)
        self.assertIn("显示更多结果", script)
        self.assertIn('class="search-result skeleton"', script)

    def test_main_navigation_links_to_search(self) -> None:
        source = (ROOT / "src" / "layouts" / "Layout.astro").read_text(
            encoding="utf-8"
        )
        self.assertIn('href={`${base}search/`}', source)


if __name__ == "__main__":
    unittest.main()
