import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "scripts"))

from fill_plan import build_plan
from find_forms import find_forms
from route_form import _extract_json
from verify_fill_geometry import overlapping_option_errors


class AuditRegressionTests(unittest.TestCase):
    def test_router_extracts_nested_json(self):
        parsed = _extract_json(
            'preface {"form_id":"DE-101","meta":{"reason":"nested"}} trailing'
        )
        self.assertEqual(parsed["form_id"], "DE-101")
        self.assertEqual(parsed["meta"]["reason"], "nested")

    def test_two_letter_prefix_influences_search(self):
        forms = find_forms("show me PP conservatorship forms")["forms"]
        self.assertTrue(forms)
        self.assertTrue(all(f["form_id"].startswith("PP-") for f in forms[:3]))

    def test_plan_reports_narrative_provenance(self):
        case = {
            "narrative_facts": {
                "died_more_than_3_years_circumstances": "Known circumstances"
            }
        }
        plan = build_plan("DE-101(I)", case, ROOT)
        fid = "died_more_than_3_years_circumstances"
        self.assertEqual(plan["resolved"][fid], "Known circumstances")
        self.assertEqual(
            plan["provenance"][fid]["origin"], "narrative_composed"
        )

    def test_substantial_option_overlap_is_an_error(self):
        geometry = {
            "fields": {
                "choice": {
                    "options": [
                        {"value": "a", "page": 0, "rect": [10, 10, 20, 20]},
                        {"value": "b", "page": 0, "rect": [10, 10.2, 20, 20.2]},
                    ]
                }
            }
        }
        self.assertEqual(len(overlapping_option_errors("X", geometry)), 1)

    def test_de101i_options_do_not_overlap(self):
        geometry = json.loads(
            (ROOT / "repo/forms/DE-101(I)/fill_geometry.json").read_text()
        )
        self.assertEqual(overlapping_option_errors("DE-101(I)", geometry), [])


if __name__ == "__main__":
    unittest.main()
