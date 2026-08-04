import unittest

from mediaflow_topics import is_russia_item


class RussiaTopicTests(unittest.TestCase):
    def test_matches_russian_oil_story(self):
        item = {"title": "Russian oil exports fall after new sanctions"}
        self.assertTrue(is_russia_item(item))

    def test_matches_ukrainian_energy_attack(self):
        item = {"arc_summary": "Ukraine strikes a refinery near Ust-Luga."}
        self.assertTrue(is_russia_item(item))

    def test_matches_pipeline_without_country_in_title(self):
        item = {"summary": "Flows through the Druzhba pipeline are suspended."}
        self.assertTrue(is_russia_item(item))

    def test_does_not_match_unrelated_crisis_story(self):
        item = {"title": "Iran warns tankers against entering the Strait of Hormuz"}
        self.assertFalse(is_russia_item(item))

    def test_handles_missing_and_null_fields(self):
        self.assertFalse(is_russia_item({"title": None}))


if __name__ == "__main__":
    unittest.main()
