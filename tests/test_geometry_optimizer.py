import unittest

import fitz

from scripts.geometry_optimizer import optimize_geometry, should_suppress


class GeometryOptimizerTests(unittest.TestCase):
    def test_manual_widget_is_never_changed(self):
        doc = fitz.open()
        doc.new_page(width=612, height=792)
        geometry = {
            "fields": {
                "county": {
                    "type": "text",
                    "widgets": [{
                        "page": 0,
                        "rect": [72, 100, 240, 113],
                        "locked": True,
                        "geometry_source": "manual",
                    }],
                }
            }
        }
        optimized, changes = optimize_geometry(
            geometry,
            {"fields": [{"field_id": "county"}]},
            doc,
        )
        self.assertEqual(geometry, optimized)
        self.assertEqual([], changes)
        doc.close()

    def test_explicit_suppression_removes_inferred_widget(self):
        doc = fitz.open()
        doc.new_page(width=612, height=792)
        geometry = {
            "fields": {
                "caption": {
                    "type": "text",
                    "widgets": [{"page": 0, "rect": [72, 100, 240, 113]}],
                }
            }
        }
        schema = {
            "fields": [{"field_id": "caption", "suppress_geometry": True}]
        }
        optimized, changes = optimize_geometry(geometry, schema, doc)
        self.assertEqual([], optimized["fields"]["caption"]["widgets"])
        self.assertEqual("suppress", changes[0]["action"])
        doc.close()

    def test_court_only_and_wet_ink_are_suppressed(self):
        self.assertTrue(should_suppress(
            "judge_date",
            {"court_only": True, "fill_strategy": {"source": "left_blank"}},
        ))
        self.assertTrue(should_suppress(
            "affiant_signature",
            {"fill_strategy": {"source": "wet_ink"}},
        ))


if __name__ == "__main__":
    unittest.main()
