import json
import unittest
from pathlib import Path
import yaml

from collectors.oup import _resolve_journal
from scripts.validate_journal_expansion_b8 import validate_batch

ROOT = Path(__file__).resolve().parents[1]

class B8SourceContractTests(unittest.TestCase):
    def test_batch_sources_are_isolated(self):
        manifest = json.loads((ROOT / "config/journal-expansion-b8.json").read_text())
        live = yaml.safe_load((ROOT / "config/journals.yml").read_text())["journals"]
        result = validate_batch(manifest, live)
        self.assertEqual(8, result["approved_staged"])
        self.assertEqual(7, result["promoted"])
        self.assertEqual(0, result["production_verification_pending"])
        self.assertEqual("partial_rollout", manifest["launch_state"])
        ijio = next(x for x in manifest["journals"] if x["key"] == "IJIO")
        self.assertEqual("production_verified", ijio["rollout_status"])
        self.assertEqual("108/c", ijio["sample_issue"])
        self.assertEqual("academic-door/journals/actions/runs/40874257900", ijio["metadata_seed_evidence"])
        ijio_evidence = ijio["production_acceptance"]
        self.assertEqual("SUCCESS_54_OF_54", ijio_evidence["public_health_result"])
        self.assertEqual("ijio-108-c", ijio_evidence["audited_issue"])
        self.assertEqual("repec-publisher-supplied", ijio_evidence["roster_authority"])
        self.assertEqual("metadata_first_rights_qualified_v1", ijio_evidence["public_mode"])
        self.assertEqual(6, ijio_evidence["ready_research_articles"])
        self.assertEqual(6, ijio_evidence["metadata_complete_articles"])
        self.assertEqual(6, ijio_evidence["rights_withheld_articles"])
        self.assertEqual(0, ijio_evidence["abstracts_en_visible"])
        self.assertEqual(0, ijio_evidence["translations_cn_visible"])
        self.assertEqual("HTTP_404_VERIFIED", ijio_evidence["unreviewed_109_public_archive"])
        self.assertEqual("7f9174dfeeeb95e5c9f8f30673d90de199b4da10", ijio_evidence["current_data_snapshot"])
        jfi = next(x for x in manifest["journals"] if x["key"] == "JFI")
        self.assertEqual("production_verified", jfi["rollout_status"])
        self.assertEqual("67/c", jfi["sample_issue"])
        self.assertEqual("publisher_supplied_repec_serial_issue_verified", jfi["source_status"])
        self.assertEqual("academic-door/journals/actions/runs/41603938416", jfi["acceptance_evidence"])
        self.assertEqual("academic-door/journals/actions/runs/42840930986", jfi["metadata_seed_evidence"])
        jfi_evidence = jfi["production_acceptance"]
        self.assertEqual("SUCCESS_55_OF_55", jfi_evidence["public_health_result"])
        self.assertEqual("jfi-67-c", jfi_evidence["audited_issue"])
        self.assertEqual(7, jfi_evidence["ready_research_articles"])
        self.assertEqual(7, jfi_evidence["rights_withheld_articles"])
        self.assertEqual(0, jfi_evidence["abstracts_en_visible"])
        self.assertEqual(0, jfi_evidence["translations_cn_visible"])
        self.assertEqual("HTTP_404_VERIFIED", jfi_evidence["unreviewed_68_public_archive"])
        self.assertEqual("09621ebea708e5254ac6f4570cdbc3d9f0860743", jfi_evidence["current_data_snapshot"])
        self.assertTrue(live["JFI"]["enabled"])
        self.assertEqual("elsevier", live["JFI"]["collector"])
        jimf = next(x for x in manifest["journals"] if x["key"] == "JIMF")
        self.assertEqual("production_verified", jimf["rollout_status"])
        self.assertEqual("168/c", jimf["sample_issue"])
        self.assertEqual("publisher_supplied_repec_serial_issue_verified", jimf["source_status"])
        self.assertEqual("academic-door/journals/actions/runs/41603938416", jimf["acceptance_evidence"])
        self.assertEqual("academic-door/journals/actions/runs/43036220306", jimf["metadata_seed_evidence"])
        jimf_accept = jimf["production_acceptance"]
        self.assertEqual("SUCCESS_56_OF_56", jimf_accept["public_health_result"])
        self.assertEqual("jimf-168-c", jimf_accept["audited_issue"])
        self.assertEqual(6, jimf_accept["ready_research_articles"])
        self.assertEqual(6, jimf_accept["rights_withheld_articles"])
        self.assertEqual(0, jimf_accept["abstracts_en_visible"])
        self.assertEqual(0, jimf_accept["translations_cn_visible"])
        self.assertEqual("871c383fb779cc3c57bb7f891065a8b7989a2632", jimf_accept["current_data_snapshot"])
        self.assertTrue(live["JIMF"]["enabled"])
        self.assertEqual("elsevier", live["JIMF"]["collector"])
        joeg = next(x for x in manifest["journals"] if x["key"] == "JOEG")
        self.assertEqual("production_verified", joeg["rollout_status"])
        self.assertEqual("academic-door/journals/actions/runs/40857386807", joeg["metadata_seed_evidence"])
        evidence_joeg = joeg["production_acceptance"]
        self.assertEqual("SUCCESS_53_OF_53", evidence_joeg["public_health_result"])
        self.assertEqual("joeg-26-5", evidence_joeg["audited_issue"])
        self.assertEqual("official-issue-page", evidence_joeg["roster_authority"])
        self.assertEqual({"comment": 3, "research_article": 6}, evidence_joeg["article_types"])
        self.assertEqual({"correction": 1}, evidence_joeg["excluded_items"])
        self.assertEqual(9, evidence_joeg["rights_withheld_articles"])
        self.assertEqual(0, evidence_joeg["abstracts_en_visible"])
        self.assertEqual(0, evidence_joeg["translations_cn_visible"])
        itpf = next(x for x in manifest["journals"] if x["key"] == "ITPF")
        self.assertEqual("production_verified", itpf["rollout_status"])
        self.assertEqual("academic-door/journals/actions/runs/40832087337", itpf["metadata_seed_evidence"])
        evidence_itpf = itpf["production_acceptance"]
        self.assertEqual("SUCCESS_52_OF_52", evidence_itpf["public_health_result"])
        self.assertEqual("itpf-33-5", evidence_itpf["audited_issue"])
        self.assertEqual("repec-publisher-supplied", evidence_itpf["roster_authority"])
        self.assertEqual(11, evidence_itpf["rights_withheld_articles"])
        self.assertEqual(0, evidence_itpf["abstracts_en_visible"])
        self.assertEqual(0, evidence_itpf["translations_cn_visible"])
        jae = next(item for item in manifest["journals"] if item["key"] == "JAE")
        self.assertEqual("production_verified", jae["rollout_status"])
        evidence = jae["production_acceptance"]
        self.assertEqual("SUCCESS_51_OF_51", evidence["public_health_result"])
        self.assertEqual("jae-41-6", evidence["audited_issue"])
        self.assertEqual(12, evidence["rights_withheld_articles"])
        self.assertEqual(0, evidence["abstracts_en_visible"])
        self.assertEqual(0, evidence["translations_cn_visible"])
        self.assertTrue(live["JEGROWTH"]["enabled"])
        self.assertEqual("springer", live["JEGROWTH"]["collector"])
        self.assertEqual(49, result["existing_live"])

    def test_joeg_uses_existing_oup_family(self):
        spec, url = _resolve_journal("joeg", None)
        self.assertEqual("joeg", spec["journal_id"])
        self.assertEqual("https://academic.oup.com/joeg/issue", url)
