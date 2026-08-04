import json
import unittest

from mediaflow_classify import ARCS, classify_batch, normalize_result


class StubProvider:
    def __init__(self, response_text):
        self.response_text = response_text
        self.calls = []

    def generate(self, system_prompt, user_payload, max_tokens):
        self.calls.append((system_prompt, user_payload, max_tokens))
        return self.response_text


class ClassifierProviderBoundaryTests(unittest.TestCase):
    def test_classify_batch_uses_provider_and_preserves_compact_payload(self):
        batch = [
            {
                "id": "x1",
                "source": "Test Wire",
                "title": "Tankers divert from Hormuz",
                "summary": "Traffic falls.",
            }
        ]
        provider = StubProvider(
            '[{"id":"x1","arc":"STRAIT_SHIPPING",'
            '"summary":"Tankers divert from Hormuz.","conflict":false}]'
        )

        result = classify_batch(provider, batch)

        self.assertEqual(result[0]["arc"], "STRAIT_SHIPPING")
        system_prompt, payload, max_tokens = provider.calls[0]
        self.assertIn("STRAIT_SHIPPING", system_prompt)
        self.assertEqual(json.loads(payload)[0]["id"], "x1")
        self.assertNotIn('", "', payload)
        self.assertNotIn('": "', payload)
        self.assertEqual(max_tokens, 2048)

    def test_existing_normalization_is_provider_independent(self):
        item = {
            "id": "x1",
            "source": "Test Wire",
            "title": "Brent rises",
            "summary": "Oil prices move higher.",
        }

        result = normalize_result(
            {
                "id": "x1",
                "arc": "NOT_AN_ARC",
                "summary": None,
                "conflict": "true",
            },
            item,
            ARCS,
        )

        self.assertEqual(result["arc"], "MARKET")
        self.assertEqual(result["summary"], "Brent rises")
        self.assertTrue(result["conflict"])


if __name__ == "__main__":
    unittest.main()
