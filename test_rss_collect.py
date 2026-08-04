import unittest

from rss_collect import is_relevant


class RelevanceFilterTests(unittest.TestCase):
    def test_accepts_sparse_refinery_headline(self):
        entry = {
            "title": "Ukraine strikes Russian refinery",
            "summary": "",
            "tags": [],
        }
        self.assertTrue(is_relevant(entry))

    def test_rejects_unrelated_ukraine_headline(self):
        entry = {
            "title": "Ukraine announces a new education policy",
            "summary": "",
            "tags": [],
        }
        self.assertFalse(is_relevant(entry))


if __name__ == "__main__":
    unittest.main()
