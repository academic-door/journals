from pathlib import Path
import unittest
import yaml


ROOT = Path(__file__).resolve().parents[1]


class PublicProductBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.composer = (ROOT / "src/pages/composer/index.astro").read_text(encoding="utf-8")
        cls.themes = (ROOT / "src/pages/themes/index.astro").read_text(encoding="utf-8")
        cls.layout = (ROOT / "src/layouts/Layout.astro").read_text(encoding="utf-8")
        cls.home = (ROOT / "src/pages/index.astro").read_text(encoding="utf-8")
        cls.status = (ROOT / "src/pages/status/index.astro").read_text(encoding="utf-8")
        cls.explorer = (ROOT / "src/components/Top5Explorer.astro").read_text(encoding="utf-8")
        cls.css = (ROOT / "src/styles/global.css").read_text(encoding="utf-8")

    def test_journals_root_is_a_real_hub_not_top5_duplicate(self):
        self.assertIn("journal-home", self.home)
        self.assertIn("顶刊之门", self.home)
        self.assertIn("领域之门", self.home)
        self.assertNotIn("<Top5Explorer", self.home)

    def test_parent_and_child_brand_are_separate(self):
        self.assertIn('href="https://academic-door.github.io/"', self.layout)
        self.assertIn('class="child-brand" href={base}>期刊</a>', self.layout)
        self.assertIn("顶刊之门", self.layout)
        self.assertIn("领域之门", self.layout)
        self.assertIn("跨刊检索", self.layout)

    def test_primary_nav_is_reader_first(self):
        nav = self.layout.split('class="reader-nav"', 1)[1].split("</nav>", 1)[0]
        self.assertIn("顶刊之门", nav)
        self.assertIn("领域之门", nav)
        self.assertIn("跨刊检索", nav)
        self.assertNotIn("Composer", nav)
        self.assertNotIn("数据状态", nav)

    def test_public_composer_is_read_only_preview(self):
        self.assertIn("Composer Preview", self.composer)
        self.assertIn("发布预览", self.composer)
        self.assertIn("进入 Composer 工作台", self.composer)
        self.assertIn("academic-door-composer.academic-door.workers.dev", self.composer)
        self.assertIn("READ-ONLY SHOWCASE", self.composer)
        for forbidden in (
            'id="markdown-editor"',
            'id="copy-rich"',
            'id="copy-markdown"',
            'id="export-markdown"',
            'id="export-html"',
            'id="custom-css"',
            'id="theme-select"',
            "localStorage",
            "navigator.clipboard",
            "COMPOSER_FORMAT_VERSION",
        ):
            self.assertNotIn(forbidden, self.composer)

    def test_public_composer_preserves_journal_issue_identity(self):
        self.assertIn('params.get("journal")', self.composer)
        self.assertIn('params.get("issue")', self.composer)
        self.assertIn('privateUrl.searchParams.set("journal", journal)', self.composer)
        self.assertIn('privateUrl.searchParams.set("issue", issue)', self.composer)
        self.assertIn("api/v1/journals/\${encodeURIComponent(journal)}/issues/\${encodeURIComponent(issue)}.json", self.composer)

    def test_public_composer_does_not_ship_private_theme_engine(self):
        for selector in (
            ".theme-wechat-default",
            ".theme-academic-simple",
            ".theme-grace",
            ".style-settings-panel",
            ".composer-toolbar",
            "#markdown-editor",
        ):
            self.assertNotIn(selector, self.css)

    def test_theme_lab_is_retired_and_noindex(self):
        self.assertIn("Theme Lab 已迁移", self.themes)
        self.assertIn("noindex", self.themes)
        self.assertNotIn("data-preview-theme", self.themes)
        self.assertNotIn("LIVE PREVIEW", self.themes)

    def test_status_is_reader_safe_static_first(self):
        self.assertIn("DATA RELIABILITY", self.status)
        self.assertIn("公开状态不是内部运维仪表盘", self.status)
        self.assertIn("来源与目录核验分开计算", self.status)
        self.assertIn('journal.order_verification === "official_verified"', self.status)
        self.assertIn("source-audit.json", self.status)
        self.assertIn("backfill-status.json", self.status)
        self.assertNotIn("history-table", self.status)
        self.assertNotIn("quality-table", self.status)

    def test_status_uses_only_small_runtime_freshness_fetch(self):
        self.assertIn("api/v1/slo.json", self.status)
        self.assertNotIn("Promise.all([", self.status)
        self.assertNotIn('fetch(\`\${base}api/v1/backfill-status.json\`', self.status)
        self.assertNotIn('fetch(\`\${base}api/v1/source-audit.json\`', self.status)

    def test_translation_provenance_is_public(self):
        self.assertIn("中文标题与摘要由 Academic Door 辅助翻译整理", self.layout)
        self.assertIn("研究引用与正式判断请以期刊原文为准", self.layout)

    def test_canonical_and_social_metadata_exist(self):
        self.assertIn('rel="canonical"', self.layout)
        self.assertIn('property="og:title"', self.layout)
        self.assertIn('property="og:description"', self.layout)
        self.assertIn('meta name="robots"', self.layout)

    def test_catalog_links_use_canonical_door_routes(self):
        self.assertIn('href={\`\${base}top5/\`}>顶刊之门</a>', self.explorer)
        self.assertIn('href={\`\${base}fields/\`}>领域之门</a>', self.explorer)
        self.assertIn("查看发布预览", self.explorer)

    def test_homepage_reader_keeps_concise_abstract_labels(self):
        self.assertIn('class="abstract-label">Abstract</p>', self.explorer)
        self.assertIn('class="abstract-label">摘要</p>', self.explorer)

    def test_content_page_surfaces_source_pending_without_internal_provenance(self):
        self.assertIn("内容已齐，待来源核验", self.explorer)
        self.assertNotIn("Crossref 备用来源", self.explorer)

    def test_top5_tabs_use_roving_keyboard_navigation(self):
        self.assertIn('tabindex="\${active && enabled ? "0" : "-1"}"', self.explorer)
        for key in ("ArrowLeft", "ArrowRight", "Home", "End"):
            self.assertIn(key, self.explorer)
        self.assertIn("state.pendingTabFocus", self.explorer)

    def test_field_collection_reuses_reader(self):
        fields_page = (ROOT / "src/pages/fields/index.astro").read_text(encoding="utf-8")
        self.assertIn("Top5Explorer", fields_page)
        self.assertIn('collectionId="fields"', fields_page)
        self.assertIn("领域之门", fields_page)

    def test_field_collection_contains_all_journals(self):
        config = yaml.safe_load((ROOT / "config/collections.yml").read_text(encoding="utf-8"))
        journals = config["collections"]["fields"]["journals"]
        self.assertEqual(44, len(journals))
        self.assertEqual(44, len(set(journals)))

    def test_editorial_visual_language_is_local_not_shared_runtime(self):
        self.assertIn(".journal-hero", self.css)
        self.assertIn(".door-card", self.css)
        self.assertIn(".public-composer-preview", self.css)
        self.assertIn(".status-metric-grid", self.css)
        self.assertNotIn("@academic-door/design", self.layout)
        self.assertNotIn("@academic-door/design", self.css)

    def test_mobile_parent_return_remains_available(self):
        self.assertIn(".parent-brand span { display: none; }", self.css)
        self.assertIn(".parent-brand img", self.css)
        self.assertNotIn(".parent-brand { display: none", self.css)


if __name__ == "__main__":
    unittest.main()
