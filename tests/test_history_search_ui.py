from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class HistoryAndSearchUiTests(unittest.TestCase):
    def test_public_reader_exposes_history_selector(self) -> None:
        source = (ROOT / "src/components/Top5Explorer.astro").read_text(encoding="utf-8")
        self.assertIn('id="issue-history-select"', source)
        self.assertIn("fetchIssueIndex", source)
        self.assertIn("latest_issue_id", source)

    def test_search_page_lazy_loads_generated_indexes(self) -> None:
        page = (ROOT / "src/pages/search/index.astro").read_text(encoding="utf-8")
        script = (ROOT / "public/search.js").read_text(encoding="utf-8")
        self.assertIn('id="global-search-history"', page)
        self.assertIn("api/v1/search/", script)
        self.assertIn('id="global-search-volume"', page)
        self.assertIn('id="global-search-issue"', page)
        self.assertIn('form?.addEventListener("submit"', script)
        self.assertNotIn("records={", page)

    def test_search_page_loads_index_metadata_in_parallel(self) -> None:
        script = (ROOT / "public/search.js").read_text(encoding="utf-8")
        self.assertIn("api/v1/search/index.json", script)
        self.assertIn("Promise.allSettled([", script)
        self.assertIn("populateJournals()", script)

    def test_search_page_uses_china_dedicated_and_year_sliced_indexes(self) -> None:
        script = (ROOT / "public/search.js").read_text(encoding="utf-8")
        self.assertIn("api/v1/search/china-latest.json", script)
        self.assertIn("api/v1/search/years/\${filters.year}.json", script)
        self.assertIn("api/v1/search/years/\${year}.json", script)
        self.assertIn("继续载入更早年份", script)
        self.assertIn("显示更多结果", script)
        self.assertIn('class="search-result skeleton"', script)

    def test_reader_surfaces_do_not_expose_non_ready_history(self) -> None:
        explorer = (ROOT / "src/components/Top5Explorer.astro").read_text(encoding="utf-8")
        script = (ROOT / "public/search.js").read_text(encoding="utf-8")
        generator = (ROOT / "scripts/update_journals.py").read_text(encoding="utf-8")
        self.assertIn('entry.publication_state === "ready"', explorer)
        self.assertNotIn(' · 待来源核验', explorer)
        self.assertNotIn('record.publication_state === "source_pending"', script)
        self.assertNotIn("内容已齐，待来源核验", script)
        self.assertIn('issue_publication_state(archived) == "ready"', generator)

    def test_search_retries_evict_failed_cache_and_ignore_stale_requests(self) -> None:
        script = (ROOT / "public/search.js").read_text(encoding="utf-8")
        self.assertIn("cache.delete(endpoint)", script)
        self.assertIn("let activeSearchGeneration = 0", script)
        self.assertIn("isCurrentSearch(generation)", script)
        self.assertIn("loadNextYear(filters, generation)", script)

    def test_search_does_not_repeat_english_title_without_chinese_title(self) -> None:
        script = (ROOT / "public/search.js").read_text(encoding="utf-8")
        self.assertIn("record.title_cn && record.title_cn !== record.title_en", script)

    def test_bulk_result_regions_are_not_live_announcements(self) -> None:
        search_page = (ROOT / "src/pages/search/index.astro").read_text(encoding="utf-8")
        explorer = (ROOT / "src/components/Top5Explorer.astro").read_text(encoding="utf-8")
        self.assertIn('id="global-search-status" class="global-search-status" aria-live="polite"', search_page)
        self.assertNotIn(
            'id="global-search-results" class="search-results" aria-live="polite"',
            search_page,
        )
        self.assertIn('id="issue-summary" class="issue-summary" aria-live="polite"', explorer)
        self.assertNotIn('id="article-list" class="article-list" aria-live="polite"', explorer)

    def test_top5_tabs_control_one_real_tabpanel(self) -> None:
        explorer = (ROOT / "src/components/Top5Explorer.astro").read_text(encoding="utf-8")
        self.assertIn('role={collectionId === "top5" ? "tabpanel" : "region"}', explorer)
        self.assertIn('aria-label={collectionId === "fields" ? "当前卷期" : undefined}', explorer)
        self.assertIn('aria-controls="issue-panel"', explorer)
        self.assertIn('id="journal-tab-\${escapeHtml(journal.journal_id)}"', explorer)
        self.assertIn('issuePanel.setAttribute("aria-labelledby", activeTab.id)', explorer)

    def test_status_separates_source_acceptance_from_official_order(self) -> None:
        page = (ROOT / "src/pages/status/index.astro").read_text(encoding="utf-8")
        self.assertIn("sourceAccepted", page)
        self.assertIn('journal.order_verification === "official_verified"', page)
        self.assertIn("达到当前可发布来源契约", page)
        self.assertIn("目录顺序待官方复核", page)
        self.assertNotIn("数据快照在正常更新窗口内", page)

    def test_status_consumes_release_freshness_contract_without_large_client_fetches(self) -> None:
        page = (ROOT / "src/pages/status/index.astro").read_text(encoding="utf-8")
        self.assertIn("api/v1/slo.json", page)
        self.assertIn("最近一次已建立权威 expected set", page)
        self.assertNotIn('fetch(\`\${base}api/v1/backfill-status.json\`', page)
        self.assertNotIn('fetch(\`\${base}api/v1/source-audit.json\`', page)

    def test_reader_footer_discloses_ai_assisted_translation(self) -> None:
        layout = (ROOT / "src/layouts/Layout.astro").read_text(encoding="utf-8")
        self.assertIn("中文标题与摘要由 Academic Door 辅助翻译整理", layout)
        self.assertIn("研究引用与正式判断请以期刊原文为准", layout)

    def test_main_navigation_links_to_canonical_routes(self) -> None:
        source = (ROOT / "src/layouts/Layout.astro").read_text(encoding="utf-8")
        self.assertIn('match: "/journals/top5/"', source)
        self.assertIn('match: "/journals/fields/"', source)
        self.assertIn('match: "/journals/search/"', source)


if __name__ == "__main__":
    unittest.main()
