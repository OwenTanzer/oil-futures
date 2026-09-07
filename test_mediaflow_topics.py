import unittest

from mediaflow_topics import is_china_item, is_russia_item


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


class ChinaTopicTests(unittest.TestCase):
    def test_matches_chinese_crude_import_story(self):
        item = {"title": "China's crude imports from Iran hit a record high"}
        self.assertTrue(is_china_item(item))

    def test_matches_singular_teapot_refiner(self):
        item = {"summary": "Shandong teapot refiner keeps buying discounted Iranian barrels."}
        self.assertTrue(is_china_item(item))

    def test_matches_plural_teapot_refiners_without_shandong(self):
        # Regression: the plural-only fixture must not rely on "Shandong"
        # also being present to pass.
        item = {"summary": "Independent teapot refiners keep buying discounted Iranian barrels."}
        self.assertTrue(is_china_item(item))

    def test_matches_state_oil_company(self):
        item = {"arc_summary": "Sinopec signed a new long-term supply deal."}
        self.assertTrue(is_china_item(item))

    def test_matches_additional_state_entities(self):
        self.assertTrue(is_china_item({"title": "CNPC and Unipec expand Gulf crude purchases"}))
        self.assertTrue(is_china_item({"title": "Sinochem finalizes a new term contract"}))
        self.assertTrue(is_china_item({"title": "China National Petroleum boosts refining runs"}))

    def test_matches_pla_navy_with_curly_apostrophe(self):
        item = {"summary": "The People’s Liberation Army Navy escorted the convoy."}
        self.assertTrue(is_china_item(item))

    def test_does_not_match_malacca_strait_without_china(self):
        # Malacca Strait stories about other parties (Indonesia, Vietnam,
        # generic shipping) must not be classified as China.
        item = {"summary": "Indonesia's coast guard boards a tanker in the Malacca Strait."}
        self.assertFalse(is_china_item(item))

    def test_matches_malacca_strait_when_china_present(self):
        item = {"summary": "Chinese tankers reroute away from the Malacca Strait chokepoint."}
        self.assertTrue(is_china_item(item))

    def test_does_not_match_south_china_sea_other_claimant(self):
        item = {"title": "Philippines and Vietnam dispute South China Sea drilling rights"}
        self.assertFalse(is_china_item(item))

    def test_does_not_match_unrelated_crisis_story(self):
        item = {"title": "Iran warns tankers against entering the Strait of Hormuz"}
        self.assertFalse(is_china_item(item))

    def test_ignores_china_only_in_collector_source_label(self):
        # A GNews/Bing query label like "GNews: china iran oil imports" is
        # stored in "source", not the article content -- it must not decide
        # topic membership on its own.
        item = {
            "source": "GNews: china iran oil imports",
            "title": "OPEC weighs another output increase",
            "summary": "Ministers meet in Vienna to discuss quota levels.",
        }
        self.assertFalse(is_china_item(item))

    def test_handles_missing_and_null_fields(self):
        self.assertFalse(is_china_item({"title": None}))


if __name__ == "__main__":
    unittest.main()
