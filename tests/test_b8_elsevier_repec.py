from pathlib import Path
import json
import unittest

F = Path(__file__).resolve().parents[1] / "tests/fixtures/b8/elsevier-repec-observed-volumes.json"

class ElsevierB8RepecTests(unittest.TestCase):
    def test_four_publisher_supplied_volumes_remain_non_promoted(self):
        data = json.loads(F.read_text(encoding="utf-8"))
        volumes = data["journals"]
        self.assertEqual(["IJIO","JFI","EJPE","JIMF"], [v["journal"] for v in volumes])
        self.assertEqual([6,7,17,6], [v["observed_source_item_count"] for v in volumes])
        for volume in volumes:
            pi = volume["observed_source_order_pii"]
            self.assertEqual(len(pi), volume["observed_source_item_count"])
            self.assertEqual(len(pi), len(set(pi)))
            self.assertTrue(all(x.startswith("S") and len(x) == 17 for x in pi))
            self.assertEqual("publisher_supplied_repec_serial", volume["source_authority"])
            self.assertFalse(volume["first_party_sciencedirect_toc_verified"])
            self.assertFalse(volume["doi_type_abstract_cn_translation_verified"])
            self.assertFalse(volume["release_eligible"])
        self.assertEqual("S017626802600073X", volumes[2]["observed_source_order_pii"][-1])
