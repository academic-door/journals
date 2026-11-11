"""Regression tests for strict B8 first-probe warning qualification."""
import copy
import unittest
from scripts.b8_monitor_transition import qualified_pending_b8_warning


def transitional(warnings, *, count=54):
    return {
        "status": "degraded",
        "warning_journals": warnings,
        "failed_journals": [],
        "b8_transition_note": "IJIO bilingual source READY; first operational probe pending",
        "summary": {"warnings": len(warnings), "failed": 0, "configured_journals": count},
    }


class B8MonitorTransitionTests(unittest.TestCase):
    def test_ijio_exact_live_soft_warning_preservation(self):
        # Last healthy 53-journal monitor had ENERGY + LABECO warnings.
        # 54th title not yet operationally probed is an added warning.
        x=transitional(["ENERGY","LABECO","IJIO"])
        self.assertTrue(qualified_pending_b8_warning(54,x))
        self.assertTrue(qualified_pending_b8_warning(54,transitional(["IJIO"])))
        self.assertTrue(qualified_pending_b8_warning(
            54,transitional(["IJIO","LABECO","ENERGY"])
        ))

    def test_jfi_55th_first_probe_only_exact_warning_qualified(self):
        x = transitional(["JFI"], count=55)
        x["b8_transition_note"] = "JFI bilingual source READY; first operational probe pending"
        self.assertTrue(qualified_pending_b8_warning(55, x))
        for warnings in ([], ["JFI", "ENERGY"], ["JFI", "LABECO"], ["ENERGY"]):
            y = transitional(warnings, count=55)
            y["b8_transition_note"] = x["b8_transition_note"]
            self.assertFalse(qualified_pending_b8_warning(55, y))
        x["b8_transition_note"] = "unqualified source"
        self.assertFalse(qualified_pending_b8_warning(55, x))

    def test_jimf_56th_first_probe_only_exact_warning_qualified(self):
        x = transitional(["JIMF"], count=56)
        x["b8_transition_note"] = "JIMF bilingual source READY; first operational probe pending"
        self.assertTrue(qualified_pending_b8_warning(56, x))
        for warnings in ([], ["JIMF", "ENERGY"], ["JIMF", "LABECO"], ["JFI"]):
            y = transitional(warnings, count=56)
            y["b8_transition_note"] = x["b8_transition_note"]
            self.assertFalse(qualified_pending_b8_warning(56, y))
        x["b8_transition_note"] = "unqualified source"
        self.assertFalse(qualified_pending_b8_warning(56, x))

    def test_rejects_unexplained_degradation_or_missing_new_probe(self):
        bad=[
            ["ENERGY","LABECO"],["IJIO","UNKNOWN"],
            ["IJIO","ENERGY"],["IJIO","LABECO"],
            ["IJIO","ENERGY","LABECO","JIE"],
            ["ENERGY","LABECO","ENERGY","IJIO"],
        ]
        for w in bad:
            with self.subTest(warnings=w):
                self.assertFalse(qualified_pending_b8_warning(54,transitional(w)))

    def test_rejects_failures_counts_missing_explicit_note(self):
        base=transitional(["ENERGY","LABECO","IJIO"])
        variants=[
            ("status","healthy"),("failed_journals",["JIE"]),
            ("b8_transition_note","unverified claim"),
            ("warning_journals","ENERGY,LABECO,IJIO"),
        ]
        for key,val in variants:
            with self.subTest(key=key):
                x=copy.deepcopy(base)
                x[key]=val
                self.assertFalse(qualified_pending_b8_warning(54,x))
        for key,val in (("warnings",2),("failed",1)):
            x=copy.deepcopy(base)
            x["summary"][key]=val
            self.assertFalse(qualified_pending_b8_warning(54,x))

    def test_legacy_one_title_preprobe_rules_stay_strict(self):
        for count,title in ((51,"JAE"),(52,"ITPF"),(53,"JOEG")):
            x=transitional([title],count=count)
            self.assertTrue(qualified_pending_b8_warning(count,x))
            self.assertFalse(qualified_pending_b8_warning(count,transitional([title,"ENERGY"],count=count)))
        self.assertFalse(qualified_pending_b8_warning(57,transitional(["IJIO"])))
        self.assertFalse(qualified_pending_b8_warning(54,transitional([],count=54)))


if __name__=="__main__":
    unittest.main()
