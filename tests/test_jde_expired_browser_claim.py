from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from scripts.expire_jde_183_browser_claim import ALLOWED, ISSUE, RSS_URL, ISSUE_URL, correct
from scripts.provenance_ledger import audit_claims

NOW = datetime(2026, 11, 3, tzinfo=timezone.utc)

def fixtures(root: Path):
    marker = {
        "captured_at": "2026-08-03T01:38:22.605Z",
        "pii_sequence_matched": True, "titles_matched": True,
    }
    q = {
        "rss_url": RSS_URL, "roster_match_scope": "rss-volume-items",
        "roster_match": True, "order_preserved": True,
        "official_item_count":36, "publishable_item_count":34,
        "excluded_item_count":2,
        "excluded_items":[{"article_type":"editorial"}, {"article_type":"correction"}],
        "doi_complete":34, "authors_complete":34, "abstract_en_complete":34,
        "translation_complete":34, "flags":[],
        "roster_authority":"official-issue-page",
        "roster_transport":"official-issue-page+browser-authorized",
        "order_verification":"official_verified",
        "browser_order_verification":marker,
        "browser_capture":{"captured_at":"2026-08-01T17:20:02.661Z"},
    }
    issue = {
        "journal_id":"jde", "issue_id":ISSUE, "volume":"183",
        "publication_date":"September 2026", "source_url":ISSUE_URL,
        "research_article_count":34, "expected_article_count":34,
        "content_status":"complete", "publication_state":"ready",
        "source_status":"official_verified", "quality":q,
        "articles":[{"sequence":i,"doi":f"10.1016/j.jdeveco.2026.{103833+i}",
                     "article_type":"research-article", "abstract_en":"text",
                     "abstract_cn":"译文", "translation":{"status":"complete"}}
                    for i in range(1,35)]
    }
    field = {
        "journal_id":"jde","latest_detected_issue_id":ISSUE,
        "latest_issue_id":ISSUE,"latest_ready_issue_id":ISSUE,
        "status":"ready",
        "source_status":"official_verified",
        "latest_detected_source_status":"official_verified",
        "roster_authority":"official-issue-page",
        "latest_detected_roster_authority":"official-issue-page",
        "roster_transport":"official-issue-page+browser-authorized",
        "latest_detected_roster_transport":"official-issue-page+browser-authorized",
        "order_verification":"official_verified",
        "latest_detected_order_verification":"official_verified",
    }
    documents = {
        ALLOWED[0]:issue, ALLOWED[1]:copy.deepcopy(issue),
        ALLOWED[2]:copy.deepcopy(issue),
        ALLOWED[3]:{"issues":[{"issue_id":ISSUE,"source_status":"official_verified","publication_state":"ready"},{"issue_id":"jde-182-c","source_status":"publisher_verified"}]},
        ALLOWED[4]:{"journals":[{"journal_id":"aer","source_status":"official_verified"},field]},
    }
    for path, value in documents.items():
        p=root/path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(value,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    ledger={"entries":{"jde":{
        "journal_id":"jde", "issue_id":ISSUE, "official_url":ISSUE_URL,
        "method":"browser-authorized", "item_count":36,
        "sequence_sha256":"source-ledger-stable-hash",
        "captured_at":"2026-08-03T01:38:22.605Z",
        "expires_at":"2026-11-01T01:38:22+00:00",
    }}}
    return documents,ledger


class JDEExpiredBrowserClaimTests(unittest.TestCase):
    def test_exact_scoped_downgrade_retains_articles_and_history(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            docs,ledger=fixtures(root)
            before={p:(root/p).read_bytes() for p in ALLOWED}
            changed=correct(root,ledger,now=NOW)
            self.assertEqual(list(ALLOWED),changed)
            self.assertEqual([],correct(root,ledger,now=NOW))
            current=json.loads((root/ALLOWED[0]).read_text())
            self.assertEqual(docs[ALLOWED[0]]["articles"],current["articles"])
            self.assertEqual("publisher_verified",current["source_status"])
            self.assertEqual("ready",current["publication_state"])
            self.assertEqual("publisher-rss",current["quality"]["roster_authority"])
            self.assertEqual("pending_official",current["quality"]["order_verification"])
            self.assertEqual(
                docs[ALLOWED[0]]["quality"]["browser_order_verification"],
                current["quality"]["historical_browser_order_verification"]
            )
            self.assertNotIn("browser_order_verification",current["quality"])
            self.assertNotIn("browser_capture",current["quality"])
            self.assertEqual([],audit_claims({"jde":current["quality"]},ledger,now=NOW))
            self.assertEqual(current,json.loads((root/ALLOWED[1]).read_text()))
            self.assertEqual(
                "publisher_verified",json.loads((root/ALLOWED[3]).read_text())["issues"][0]["source_status"]
            )
            self.assertNotEqual(before[ALLOWED[0]],(root/ALLOWED[0]).read_bytes())
            fields=json.loads((root/ALLOWED[4]).read_text())
            self.assertEqual("official_verified",fields["journals"][0]["source_status"])

    def test_refuses_nonexpired_or_diverged_before_writing_anything(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            docs,ledger=fixtures(root)
            before={p:(root/p).read_bytes() for p in ALLOWED}
            with self.assertRaisesRegex(ValueError,"not expired"):
                correct(root,ledger,now=datetime(2026,10,1,tzinfo=timezone.utc))
            self.assertEqual(before,{p:(root/p).read_bytes() for p in ALLOWED})
            altered=copy.deepcopy(docs[ALLOWED[1]])
            altered["articles"][0]["doi"]="10.1016/j.jdeveco.wrong"
            (root/ALLOWED[1]).write_text(json.dumps(altered),encoding="utf-8")
            snap={p:(root/p).read_bytes() for p in ALLOWED}
            with self.assertRaises(ValueError):correct(root,ledger,now=NOW)
            self.assertEqual(snap,{p:(root/p).read_bytes() for p in ALLOWED})

    def test_detected_issue_without_readiness_stamps_preserves_schema(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            docs,ledger=fixtures(root)
            detected=copy.deepcopy(docs[ALLOWED[2]])
            detected.pop("source_status")
            detected.pop("content_status")
            (root/ALLOWED[2]).write_text(json.dumps(detected,ensure_ascii=False))
            self.assertEqual(list(ALLOWED),correct(root,ledger,now=NOW))
            updated=json.loads((root/ALLOWED[2]).read_text())
            self.assertNotIn("source_status",updated)
            self.assertNotIn("content_status",updated)
            self.assertEqual("publisher-rss",updated["quality"]["roster_authority"])
            self.assertEqual([],correct(root,ledger,now=NOW))

    def test_ci_shadow_is_strictly_restricted_to_remediation_pr(self):
        path=Path(__file__).resolve().parents[1]/".github/workflows/test.yml"
        workflow=path.read_text(encoding="utf-8")
        self.assertIn(
            "github.event_name == 'pull_request' && github.event.pull_request.number == 526",
            workflow,
        )
        self.assertIn("python scripts/expire_jde_183_browser_claim.py",workflow)
        self.assertIn("python scripts/audit_public_data.py --strict-provenance",workflow)
        self.assertLess(
            workflow.index("python scripts/expire_jde_183_browser_claim.py"),
            workflow.index("python scripts/audit_public_data.py --strict-provenance"),
        )

    def test_refuses_bogus_source_grade_and_missing_ledger(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            _,ledger=fixtures(root)
            noledger={"entries":{}}
            with self.assertRaises(ValueError):correct(root,noledger,now=NOW)
            q=json.loads((root/ALLOWED[0]).read_text())
            q["quality"]["rss_url"]="https://example.org"
            (root/ALLOWED[0]).write_text(json.dumps(q),encoding="utf-8")
            with self.assertRaises(ValueError):correct(root,ledger,now=NOW)


if __name__ == "__main__":
    unittest.main()
