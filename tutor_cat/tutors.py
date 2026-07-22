"""LLM tutor adapters (OpenAI / Anthropic / Google) with on-disk response caching.

Credentials come from environment variables (OPENAI_API_KEY, ANTHROPIC_API_KEY,
GOOGLE_API_KEY); model IDs are configurable. SDK imports are lazy so the package
works when only some providers are installed.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Protocol

from .schemas import Scenario


class TutorClient(Protocol):
    name: str
    model: str

    def respond(self, scenario: Scenario) -> str: ...


# Dataset context roles -> API roles (APIs only accept user/assistant/system).
_ROLE_MAP = {"student": "user", "tutor": "assistant", "user": "user",
             "assistant": "assistant", "system": "system"}


def _messages_for(scenario: Scenario) -> list[dict[str, str]]:
    """Replay conversation context (if any), then the scenario prompt."""
    messages = [
        {"role": _ROLE_MAP.get(t.get("role", "user"), "user"), "content": t.get("content", "")}
        for t in scenario.conversation_context
    ]
    messages.append({"role": "user", "content": scenario.prompt})
    return messages


def _retry(fn, max_retries: int = 3):
    last: Exception | None = None
    for attempt in range(max_retries):
        try:
            return fn()
        except Exception as e:  # provider SDK exception types vary
            last = e
            time.sleep(2.0 * (attempt + 1))
    raise RuntimeError(f"tutor call failed after {max_retries} attempts: {last!r}")


class OpenAITutor:
    """OpenAI API or any OpenAI-compatible gateway (TrueFoundry, LiteLLM, vLLM...).

    Set base_url + api_key_env in the tutor's config entry to route through a
    gateway; otherwise defaults to api.openai.com with OPENAI_API_KEY."""

    def __init__(self, name: str, model: str, temperature: float | None = 0.0,
                 max_tokens: int | None = None, base_url: str | None = None,
                 api_key_env: str | None = None):
        import os

        from openai import OpenAI  # lazy

        self.name = name
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        api_key = os.environ.get(api_key_env) if api_key_env else None
        self._client = OpenAI(base_url=base_url, api_key=api_key) if (base_url or api_key) else OpenAI()

    def respond(self, scenario: Scenario) -> str:
        kwargs: dict = {"model": self.model, "messages": _messages_for(scenario)}
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        if self.max_tokens is not None:
            kwargs["max_completion_tokens"] = self.max_tokens

        def call():
            try:
                return self._client.chat.completions.create(**kwargs)
            except Exception as e:
                # Some reasoning models reject explicit temperature; retry without it.
                if "temperature" in str(e) and "temperature" in kwargs:
                    kwargs.pop("temperature")
                    return self._client.chat.completions.create(**kwargs)
                raise

        completion = _retry(call)
        return completion.choices[0].message.content or ""


class AnthropicTutor:
    def __init__(self, name: str, model: str, temperature: float | None = 0.0,
                 max_tokens: int | None = None):
        import anthropic  # lazy

        self.name = name
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens or 4096  # required by the API
        self._client = anthropic.Anthropic()

    def respond(self, scenario: Scenario) -> str:
        kwargs: dict = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": _messages_for(scenario),
        }
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        message = _retry(lambda: self._client.messages.create(**kwargs))
        return "".join(block.text for block in message.content if block.type == "text")


class GoogleTutor:
    def __init__(self, name: str, model: str, temperature: float | None = 0.0,
                 max_tokens: int | None = None):
        from google import genai  # lazy

        self.name = name
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._client = genai.Client()

    def respond(self, scenario: Scenario) -> str:
        from google.genai import types  # lazy

        # Flatten any context into a single text prompt (contexts are rare in v1).
        parts = [
            f"[{t.get('role', '?')}] {t.get('content', '')}"
            for t in scenario.conversation_context
        ]
        parts.append(scenario.prompt)
        config = types.GenerateContentConfig(
            temperature=self.temperature,
            max_output_tokens=self.max_tokens,
        )
        result = _retry(
            lambda: self._client.models.generate_content(
                model=self.model, contents="\n\n".join(parts), config=config
            )
        )
        return result.text or ""


class CachedTutor:
    """Wraps a TutorClient with a cache at cache_dir/<model>/<scenario_id>.json,
    so re-runs (e.g. baseline vs CAT) never re-call the API for the same scenario."""

    def __init__(self, inner: TutorClient, cache_dir: str | Path):
        self.inner = inner
        self.name = inner.name
        self.model = inner.model
        safe_model = re.sub(r"[^A-Za-z0-9._-]", "_", inner.model)
        self._dir = Path(cache_dir) / safe_model
        self._dir.mkdir(parents=True, exist_ok=True)

    def respond(self, scenario: Scenario) -> str:
        path = self._dir / f"{scenario.scenario_id}.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))["response"]
        response = self.inner.respond(scenario)
        path.write_text(
            json.dumps(
                {"model": self.model, "scenario_id": scenario.scenario_id, "response": response},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return response


_PROVIDERS = {
    "openai": OpenAITutor,
    "anthropic": AnthropicTutor,
    "google": GoogleTutor,
}


def build_tutor(spec: dict, cache_dir: str | Path) -> CachedTutor:
    """spec: {name, provider, model, temperature?, max_tokens?, base_url?, api_key_env?}
    from config.yaml. base_url/api_key_env apply to the 'openai' provider only
    (OpenAI-compatible gateways such as TrueFoundry)."""
    provider = spec["provider"]
    if provider not in _PROVIDERS:
        raise ValueError(f"unknown tutor provider '{provider}' (expected {list(_PROVIDERS)})")
    kwargs = dict(
        name=spec["name"],
        model=spec["model"],
        temperature=spec.get("temperature", 0.0),
        max_tokens=spec.get("max_tokens"),
    )
    if provider == "openai":
        kwargs["base_url"] = spec.get("base_url")
        kwargs["api_key_env"] = spec.get("api_key_env")
    tutor = _PROVIDERS[provider](**kwargs)
    return CachedTutor(tutor, cache_dir)
