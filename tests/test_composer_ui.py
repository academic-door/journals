from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ComposerUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = (ROOT / "src/pages/composer/index.astro").read_text(encoding="utf-8")
        cls.css = (ROOT / "src/styles/global.css").read_text(encoding="utf-8")

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

    def test_wechat_toc_uses_table_layout_for_copy_compatibility(self):
        self.assertIn('<table class="toc-item" role="presentation">', self.page)
        self.assertIn("borderCollapse", self.page)
        self.assertNotIn('<section class="toc-item">', self.page)

    def test_issue_periods_are_present_in_public_snapshots(self):
        import json

        for journal_id in ("aer", "jpe", "qje", "res", "ecta"):
            issue = json.loads(
                (ROOT / "public/api/v1/journals" / journal_id / "issues/current.json")
                .read_text(encoding="utf-8")
            )
            self.assertRegex(issue["publication_date"], r"(?:\d{4}-\d{2}|\d{4}年\d+月|[A-Za-z]+\s+\d{4})")


if __name__ == "__main__":
    unittest.main()
