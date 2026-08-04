"""Provider adapters for MediaFlow's LLM classifier."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

import requests


DEFAULT_MODELS = {
    "anthropic": "claude-haiku-4-5-20251001",
    "openai": "gpt-5-mini",
    "gemini": "gemini-2.5-flash",
}

PROVIDER_ALIASES = {
    "anthropic": "anthropic",
    "claude": "anthropic",
    "openai": "openai",
    "chatgpt": "openai",
    "gemini": "gemini",
    "google": "gemini",
}

API_KEY_ENV_VARS = {
    "anthropic": ("ANTHROPIC_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
}

MODEL_ENV_VARS = {
    "anthropic": "ANTHROPIC_MODEL",
    "openai": "OPENAI_MODEL",
    "gemini": "GEMINI_MODEL",
}

DEFAULT_KEYS_FILE = Path.home() / ".claude" / "keys.env"
DEFAULT_TIMEOUT_SECONDS = 90.0


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    model: str
    api_key: str
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS


class ClassificationProvider(Protocol):
    """Small common boundary used by the classifier retry pipeline."""

    def generate(self, system_prompt: str, user_payload: str, max_tokens: int) -> str:
        """Return the model's response text."""


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def _normalized_provider_name(value: str) -> str:
    requested = value.strip().lower()
    try:
        return PROVIDER_ALIASES[requested]
    except KeyError as exc:
        supported = ", ".join(DEFAULT_MODELS)
        raise ValueError(
            f"Unsupported CLASSIFIER_PROVIDER {value!r}; choose one of: {supported}"
        ) from exc


def load_provider_config(keys_file: Path | None = None) -> ProviderConfig:
    """Resolve provider, model, key, and timeout from environment settings."""
    provider = _normalized_provider_name(
        os.environ.get("CLASSIFIER_PROVIDER", "anthropic")
    )
    model = (
        os.environ.get("CLASSIFIER_MODEL")
        or os.environ.get(MODEL_ENV_VARS[provider])
        or DEFAULT_MODELS[provider]
    ).strip()
    if not model:
        raise ValueError("Classifier model cannot be empty")

    configured_keys_file = os.environ.get("CLASSIFIER_KEYS_FILE")
    source_path = (
        Path(configured_keys_file).expanduser()
        if configured_keys_file
        else keys_file or DEFAULT_KEYS_FILE
    )
    file_values = _read_env_file(source_path)
    key_names = API_KEY_ENV_VARS[provider]
    api_key = next(
        (
            value.strip()
            for key_name in key_names
            if (value := os.environ.get(key_name) or file_values.get(key_name))
            and value.strip()
        ),
        "",
    )
    if not api_key:
        joined = " or ".join(key_names)
        raise ValueError(
            f"{joined} not found in the environment or {source_path}"
        )

    timeout_raw = os.environ.get(
        "CLASSIFIER_API_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS)
    )
    try:
        timeout_seconds = float(timeout_raw)
    except ValueError as exc:
        raise ValueError("CLASSIFIER_API_TIMEOUT_SECONDS must be a number") from exc
    if timeout_seconds <= 0:
        raise ValueError("CLASSIFIER_API_TIMEOUT_SECONDS must be greater than zero")

    return ProviderConfig(provider, model, api_key, timeout_seconds)


class AnthropicProvider:
    def __init__(self, config: ProviderConfig):
        import anthropic

        self.model = config.model
        self.client = anthropic.Anthropic(
            api_key=config.api_key, timeout=config.timeout_seconds
        )

    def generate(self, system_prompt: str, user_payload: str, max_tokens: int) -> str:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_payload}],
        )
        text_blocks = [
            block.text
            for block in response.content
            if getattr(block, "type", None) == "text" and getattr(block, "text", None)
        ]
        if not text_blocks:
            raise ValueError("Anthropic response did not contain text")
        return "".join(text_blocks)


class OpenAIProvider:
    def __init__(self, config: ProviderConfig, session: requests.Session | None = None):
        self.model = config.model
        self.api_key = config.api_key
        self.timeout_seconds = config.timeout_seconds
        self.session = session or requests.Session()
        self.base_url = os.environ.get(
            "OPENAI_BASE_URL", "https://api.openai.com/v1"
        ).rstrip("/")

    def generate(self, system_prompt: str, user_payload: str, max_tokens: int) -> str:
        response = self.session.post(
            f"{self.base_url}/responses",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "instructions": system_prompt,
                "input": user_payload,
                "max_output_tokens": max_tokens,
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()

        if isinstance(data.get("output_text"), str) and data["output_text"]:
            return data["output_text"]
        text_parts = [
            content.get("text", "")
            for item in data.get("output", [])
            if item.get("type") == "message"
            for content in item.get("content", [])
            if content.get("type") == "output_text"
        ]
        text = "".join(text_parts)
        if not text:
            raise ValueError("OpenAI response did not contain output text")
        return text


class GeminiProvider:
    def __init__(self, config: ProviderConfig, session: requests.Session | None = None):
        self.model = config.model.removeprefix("models/")
        self.api_key = config.api_key
        self.timeout_seconds = config.timeout_seconds
        self.session = session or requests.Session()
        self.base_url = os.environ.get(
            "GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta"
        ).rstrip("/")

    def generate(self, system_prompt: str, user_payload: str, max_tokens: int) -> str:
        model_path = quote(self.model, safe="")
        response = self.session.post(
            f"{self.base_url}/models/{model_path}:generateContent",
            headers={
                "x-goog-api-key": self.api_key,
                "Content-Type": "application/json",
            },
            json={
                "systemInstruction": {"parts": [{"text": system_prompt}]},
                "contents": [
                    {"role": "user", "parts": [{"text": user_payload}]}
                ],
                "generationConfig": {
                    "maxOutputTokens": max_tokens,
                    "responseMimeType": "application/json",
                },
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        text_parts = [
            part.get("text", "")
            for candidate in data.get("candidates", [])[:1]
            for part in candidate.get("content", {}).get("parts", [])
            if isinstance(part.get("text"), str)
        ]
        text = "".join(text_parts)
        if not text:
            reason = ""
            if data.get("candidates"):
                reason = data["candidates"][0].get("finishReason", "")
            detail = f" (finish reason: {reason})" if reason else ""
            raise ValueError(f"Gemini response did not contain text{detail}")
        return text


def create_provider(config: ProviderConfig) -> ClassificationProvider:
    if config.name == "anthropic":
        return AnthropicProvider(config)
    if config.name == "openai":
        return OpenAIProvider(config)
    if config.name == "gemini":
        return GeminiProvider(config)
    raise ValueError(f"Unsupported classifier provider: {config.name}")
