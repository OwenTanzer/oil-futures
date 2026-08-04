import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from classifier_providers import (
    GeminiProvider,
    OpenAIProvider,
    ProviderConfig,
    load_provider_config,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse(self.payload)


class ProviderConfigTests(unittest.TestCase):
    def test_anthropic_remains_the_default(self):
        with tempfile.TemporaryDirectory() as directory:
            keys_file = Path(directory) / "keys.env"
            keys_file.write_text("ANTHROPIC_API_KEY=test-claude\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                config = load_provider_config(keys_file)

        self.assertEqual(config.name, "anthropic")
        self.assertEqual(config.model, "claude-haiku-4-5-20251001")
        self.assertEqual(config.api_key, "test-claude")

    def test_chatgpt_alias_and_model_override_select_openai(self):
        environment = {
            "CLASSIFIER_PROVIDER": "chatgpt",
            "CLASSIFIER_MODEL": "test-openai-model",
            "OPENAI_API_KEY": "test-openai",
            "CLASSIFIER_API_TIMEOUT_SECONDS": "12.5",
        }
        with patch.dict(os.environ, environment, clear=True):
            config = load_provider_config(Path("missing.env"))

        self.assertEqual(config.name, "openai")
        self.assertEqual(config.model, "test-openai-model")
        self.assertEqual(config.api_key, "test-openai")
        self.assertEqual(config.timeout_seconds, 12.5)

    def test_gemini_accepts_google_api_key(self):
        with patch.dict(
            os.environ,
            {"CLASSIFIER_PROVIDER": "gemini", "GOOGLE_API_KEY": "test-google"},
            clear=True,
        ):
            config = load_provider_config(Path("missing.env"))

        self.assertEqual(config.name, "gemini")
        self.assertEqual(config.api_key, "test-google")

    def test_unknown_provider_fails_before_any_request(self):
        with patch.dict(
            os.environ, {"CLASSIFIER_PROVIDER": "unknown"}, clear=True
        ):
            with self.assertRaisesRegex(ValueError, "Unsupported CLASSIFIER_PROVIDER"):
                load_provider_config(Path("missing.env"))


class ProviderRequestTests(unittest.TestCase):
    def test_openai_responses_api_text_is_extracted(self):
        session = FakeSession(
            {
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": '[{"id":"x1"}]'}
                        ],
                    }
                ]
            }
        )
        provider = OpenAIProvider(
            ProviderConfig("openai", "test-model", "secret", 9), session
        )

        text = provider.generate("system", "payload", 123)

        self.assertEqual(text, '[{"id":"x1"}]')
        url, request = session.calls[0]
        self.assertEqual(url, "https://api.openai.com/v1/responses")
        self.assertEqual(request["headers"]["Authorization"], "Bearer secret")
        self.assertEqual(request["json"]["instructions"], "system")
        self.assertEqual(request["json"]["input"], "payload")
        self.assertEqual(request["timeout"], 9)

    def test_gemini_text_is_extracted_and_key_stays_in_header(self):
        session = FakeSession(
            {
                "candidates": [
                    {"content": {"parts": [{"text": '[{"id":"x1"}]'}]}}
                ]
            }
        )
        provider = GeminiProvider(
            ProviderConfig("gemini", "models/test model", "secret", 7), session
        )

        text = provider.generate("system", "payload", 456)

        self.assertEqual(text, '[{"id":"x1"}]')
        url, request = session.calls[0]
        self.assertTrue(url.endswith("/models/test%20model:generateContent"))
        self.assertNotIn("secret", url)
        self.assertEqual(request["headers"]["x-goog-api-key"], "secret")
        self.assertEqual(
            request["json"]["generationConfig"]["responseMimeType"],
            "application/json",
        )
        self.assertEqual(request["timeout"], 7)

    def test_empty_openai_response_is_rejected(self):
        provider = OpenAIProvider(
            ProviderConfig("openai", "test-model", "secret"), FakeSession({})
        )

        with self.assertRaisesRegex(ValueError, "did not contain output text"):
            provider.generate("system", "payload", 123)


if __name__ == "__main__":
    unittest.main()
