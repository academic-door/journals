from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ComposerUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = (ROOT / "src/pages/composer/index.astro").read_text(encoding="utf-8")
        cls.css = (ROOT / "src/styles/global.css").read_text(encoding="utf-8")
        cls.explorer = (ROOT / "src/components/Top5Explorer.astro").read_text(
            encoding="utf-8"
        )

    def test_compact_classic_theme_is_default(self):
        self.assertIn('fontSize: "14"', self.page)
        self.assertIn("--composer-body-size, 14px", self.css)

    def test_style_controls_are_persisted_and_applied(self):
        for control_id in (
            "font-size-select",
            "line-height-select",
            "font-family-select",
            "accent-color",
            "custom-css",
        ):
            self.assertIn(f'id="{control_id}"', self.page)
        self.assertIn("styleSettings,", self.page)
        self.assertIn("applyStyleSettings();", self.page)

    def test_custom_css_is_scoped_and_blocks_external_resources(self):
        self.assertIn('style is:inline id="composer-custom-style"', self.page)
        self.assertIn("#wechat-preview ${value}", self.page)
        self.assertIn("@import|@font-face|url", self.page)
        self.assertIn("image-set", self.page)
        self.assertIn('css.includes("\\\\")', self.page)

    def test_wechat_toc_uses_original_hanging_indent_layout(self):
        self.assertIn('<p class="toc-item">', self.page)
        self.assertIn('<br/><span class="toc-title-cn">', self.page)
        self.assertIn('"textIndent"', self.page)
        self.assertIn('${ordered[1]}. ${renderInline(ordered[2])}', self.page)
        self.assertNotIn('<span class="toc-number">', self.page)
        self.assertNotIn('<table class="toc-item"', self.page)

    def test_rich_copy_does_not_freeze_mobile_preview_dimensions(self):
        properties = self.page.split("const properties = [", 1)[1].split("];", 1)[0]
        for dimension in ('"width"', '"minWidth"', '"maxWidth"', '"height"', '"minHeight"', '"maxHeight"'):
            self.assertNotIn(dimension, properties)

    def test_issue_periods_are_present_in_public_snapshots(self):
        import json

        for journal_id in ("aer", "jpe", "qje", "res", "ecta"):
            issue = json.loads(
                (ROOT / "public/api/v1/journals" / journal_id / "issues/current.json")
                .read_text(encoding="utf-8")
            )
            self.assertRegex(issue["publication_date"], r"(?:\d{4}-\d{2}|\d{4}年\d+月|[A-Za-z]+\s+\d{4})")

    def test_issue_period_display_uses_spaced_chinese_date(self):
        self.assertIn("return `${iso[1]} 年 ${Number(iso[2])} 月`;", self.page)
        self.assertIn("return `${month[2]} 年 ${months[month[1].toLowerCase()]} 月`;", self.page)

    def test_homepage_uses_concise_abstract_labels(self):
        explorer = (ROOT / "src/components/Top5Explorer.astro").read_text(encoding="utf-8")
        self.assertIn('class="abstract-label">Abstract</p>', explorer)
        self.assertIn('class="abstract-label">摘要</p>', explorer)
        self.assertNotIn('class="abstract-label">English Abstract</p>', explorer)
        self.assertNotIn('class="abstract-label">中文摘要</p>', explorer)

    def test_content_page_keeps_source_status_out_of_reader_view(self):
        explorer = (ROOT / "src/components/Top5Explorer.astro").read_text(encoding="utf-8")
        self.assertNotIn("官网目录已核对", explorer)
        self.assertNotIn("目录顺序待官网复核", explorer)
        self.assertNotIn("Crossref 备用来源", explorer)
        architecture = (ROOT / "docs/architecture.md").read_text(encoding="utf-8")
        self.assertIn("状态页与公共 API", architecture)

    def test_composer_toolbar_stays_on_one_line_on_desktop(self):
        self.assertIn(".toolbar-actions { display: flex; flex-wrap: nowrap;", self.css)
        self.assertIn(".composer-toolbar #journal-select { width: 180px;", self.css)
        self.assertIn(".composer-toolbar #issue-select { width: 180px;", self.css)
        self.assertIn(".composer-toolbar #theme-select { width: 200px;", self.css)
        self.assertIn(".toolbar-actions { width: 100%; margin-left: 0; flex-wrap: wrap; }", self.css)

    def test_china_filter_and_traceable_relevance_hook_exist(self):
        explorer = (ROOT / "src/components/Top5Explorer.astro").read_text(encoding="utf-8")
        self.assertIn('id="china-filter"', explorer)
        self.assertIn("china_relevance", explorer)
        self.assertIn("state.chinaOnly", explorer)
        self.assertIn("chinaRelevance(article)", self.page)
        self.assertIn("与中国相关的研究有", self.page)

    def test_switching_journals_resets_stale_filters(self):
        explorer = (ROOT / "src/components/Top5Explorer.astro").read_text(
            encoding="utf-8"
        )
        load_issue = explorer.split("const loadIssue = async (journal) => {", 1)[1].split(
            "};", 1
        )[0]
        reset_filters = explorer.split("const resetArticleFilters = () => {", 1)[1].split(
            "};", 1
        )[0]
        self.assertIn("resetArticleFilters();", load_issue)
        self.assertIn('state.query = "";', reset_filters)
        self.assertIn("state.chinaOnly = false;", reset_filters)
        self.assertIn('searchInput.value = "";', reset_filters)
        self.assertIn('chinaToggle.textContent = "仅看中国相关";', reset_filters)
        self.assertIn("本期没有识别到中国相关论文", explorer)

    def test_first_issue_is_embedded_and_other_issues_are_prefetched(self):
        explorer = (ROOT / "src/components/Top5Explorer.astro").read_text(
            encoding="utf-8"
        )
        self.assertIn("readFileSync", explorer)
        self.assertIn("initialCollection", explorer)
        self.assertIn(
            'const initialIssuePath = initialIssueUrl.replace(/^.*\\/api\\/v1\\//, "");',
            explorer,
        )
        self.assertIn("new URL(initialIssuePath, publicApi)", explorer)
        self.assertNotIn(
            'initialJournal.latest_detected_issue_url ? "detected" : "current"',
            explorer,
        )
        self.assertIn("Promise.resolve(initialIssue)", explorer)
        self.assertNotIn("fetch(collectionUrl)", explorer)
        self.assertIn('"pointerenter", warmIssue', explorer)
        self.assertIn('"requestIdleCallback" in window', explorer)

    def test_classic_theme_defaults_are_frozen(self):
        self.assertIn("const DEFAULT_STYLE = Object.freeze({", self.page)
        self.assertIn('value="wechat-default">学术传送门经典（默认）', self.page)

    def test_composer_loads_top5_and_field_collections(self):
        self.assertIn('fetch(`${base}api/v1/index.json`)', self.page)
        self.assertIn("collections.flatMap", self.page)
        fields_page = (ROOT / "src/pages/fields/index.astro").read_text(encoding="utf-8")
        self.assertIn('Top5Explorer', fields_page)
        self.assertIn('collectionId="fields"', fields_page)
        self.assertIn('Econ Field Journals · Academic Door', fields_page)

    def test_composer_loads_historical_issue_indexes(self):
        self.assertIn(
            "api/v1/journals/${journal.journal_id}/issues/index.json",
            self.page,
        )
        self.assertIn("fetchIssueHistory", self.page)
        self.assertIn("historyCache", self.page)
        self.assertIn("!excludedIds.has(issue.issue_id)", self.page)
        self.assertIn("ready.concat(detected, archived)", self.page)
        self.assertIn("latest_detected_issue_url", self.page)
        self.assertIn('requestedParams.get("issue")', self.page)
        self.assertIn("option.dataset.issueId === requestedIssue", self.page)

    def test_composer_blocks_export_until_detected_issue_is_ready(self):
        self.assertIn('id="publication-readiness"', self.page)
        self.assertIn("const issueReadiness", self.page)
        self.assertIn("publishingButtons.forEach", self.page)
        self.assertIn("button.disabled = true", self.page)
        self.assertIn("等待英文摘要", self.page)
        self.assertIn("中文翻译中", self.page)
        self.assertIn("可复制发布", self.explorer)

    def test_composer_selection_and_order_are_persisted(self):
        self.assertIn('id="select-china"', self.page)
        self.assertIn('id="select-all"', self.page)
        self.assertIn('draggable="true"', self.page)
        self.assertIn('data-move="-1"', self.page)
        self.assertIn('data-move="1"', self.page)
        self.assertIn("issuePreferences,", self.page)
        self.assertIn("selected: [...selectedIds]", self.page)
        self.assertIn("order: [...pickerOrder]", self.page)

    def test_composer_outputs_only_selected_articles_in_chosen_order(self):
        self.assertIn(
            "orderedIssueArticles(issue).filter((article) => selected.has(article.paper_id))",
            self.page,
        )
        self.assertIn("editor.value = articleMarkdown(currentIssue, selectedIds);", self.page)
        self.assertIn("regenerateFromSelection", self.page)
        self.assertIn("本次选取其中 ${articles.length} 篇", self.page)

    def test_composer_picker_has_accessible_move_controls(self):
        self.assertIn('aria-label="上移《${escapeHtml', self.page)
        self.assertIn('aria-label="下移《${escapeHtml', self.page)
        self.assertIn(".move-actions button", self.css)

    def test_field_collection_contains_all_a_tier_journals(self):
        import yaml

        config = yaml.safe_load((ROOT / "config/collections.yml").read_text(encoding="utf-8"))
        journals = config["collections"]["fields"]["journals"]
        self.assertEqual(36, len(journals))
        self.assertEqual(36, len(set(journals)))
        self.assertTrue({"JDE", "JPubE", "JEEM", "JUE", "AJAE"}.issubset(journals))

    def test_field_journals_reuse_top5_reader_with_compact_selectors(self):
        explorer = (ROOT / "src/components/Top5Explorer.astro").read_text(
            encoding="utf-8"
        )
        self.assertIn('id="field-filter"', explorer)
        self.assertIn('id="journal-select"', explorer)
        self.assertIn("select.onchange", explorer)
        self.assertIn('class="paper-entry"', explorer)
        self.assertNotIn("field-journal-grid", explorer)

    def test_catalog_toolbars_use_collection_specific_responsive_grids(self):
        explorer = (ROOT / "src/components/Top5Explorer.astro").read_text(
            encoding="utf-8"
        )
        self.assertIn('`journal-toolbar--${collectionId}`', explorer)
        self.assertIn("field-category-filter", explorer)
        self.assertIn("journal-select-filter", explorer)
        self.assertIn(".journal-toolbar--top5 {", self.css)
        self.assertIn(".journal-toolbar--fields {", self.css)
        self.assertIn("grid-template-columns: repeat(6, minmax(0, 1fr));", self.css)
        self.assertIn(".journal-toolbar--top5 .journal-tabs { grid-column: 1 / -1; }", self.css)

    def test_field_names_are_consistent_across_config_and_public_api(self):
        import json
        import yaml

        config = yaml.safe_load((ROOT / "config/collections.yml").read_text(encoding="utf-8"))
        index = json.loads((ROOT / "public/api/v1/index.json").read_text(encoding="utf-8"))
        fields = json.loads(
            (ROOT / "public/api/v1/collections/fields.json").read_text(encoding="utf-8")
        )
        collection = next(
            item for item in index["collections"] if item["id"] == "fields"
        )
        self.assertEqual("Econ Field Journals", config["collections"]["fields"]["name"])
        self.assertEqual("领域顶刊", config["collections"]["fields"]["name_cn"])
        self.assertEqual("Econ Field Journals", collection["title"])
        self.assertEqual("领域顶刊", collection["title_cn"])
        self.assertEqual("Econ Field Journals", fields["title"])
        self.assertEqual("领域顶刊", fields["title_cn"])

    def test_field_collection_cards_have_issue_metadata(self):
        import json

        fields = json.loads(
            (ROOT / "public/api/v1/collections/fields.json").read_text(encoding="utf-8")
        )
        self.assertEqual(36, len(fields["journals"]))
        for journal in fields["journals"]:
            self.assertTrue(journal["latest_issue_label"], journal["journal_id"])
            self.assertTrue(journal["publication_date"], journal["journal_id"])


if __name__ == "__main__":
    unittest.main()
