"""LLM access: Groq -> Gemini/OpenAI (if keyed) -> deterministic mock.

All agents go through this module. Structured output is validated by Pydantic;
on invalid JSON we send one repair prompt, then fall through to the next
provider. No API key ever appears in logs.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Optional, Type, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from .config import settings

T = TypeVar("T", bound=BaseModel)

FAILURE_INJECTION: dict[str, str] = {}


def set_failure_injection(mode: str) -> None:
    """Demo toggle: 'bad_json' makes the next structured calls return garbage."""
    FAILURE_INJECTION["mode"] = mode


class LLMError(RuntimeError):
    """Raised when every provider failed to produce valid output."""


class ChatMessage(BaseModel):
    role: str = "user"
    content: str = ""


def _extract_json(text: str) -> Any:
    """Pull the first JSON object/array out of a model response."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = min(
            (i for i in (text.find("{"), text.find("[")) if i != -1),
            default=-1,
        )
        if start == -1:
            raise
        decoder = json.JSONDecoder()
        obj, _ = decoder.raw_decode(text[start:])
        return obj


class Provider:
    """Base class: name + chat()."""

    name = "base"

    async def chat(
        self, messages: list[ChatMessage], temperature: float = 0.2
    ) -> str:
        raise NotImplementedError


class GroqProvider(Provider):
    name = "groq"
    URL = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model

    async def chat(self, messages, temperature=0.2) -> str:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                self.URL,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "messages": [m.model_dump() for m in messages],
                    "temperature": temperature,
                    "response_format": {"type": "json_object"},
                },
            )
            if resp.status_code == 400:
                payload = dict(resp.json())
                payload.pop("response_format", None)
                resp = await client.post(
                    self.URL,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]


class GeminiProvider(Provider):
    name = "gemini"

    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model

    async def chat(self, messages, temperature=0.2) -> str:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent"
        )
        system = "\n".join(m.content for m in messages if m.role == "system")
        history = [
            {"role": "model" if m.role == "assistant" else "user",
             "parts": [{"text": m.content}]}
            for m in messages if m.role != "system"
        ]
        body: dict[str, Any] = {"contents": history}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        body["generationConfig"] = {"temperature": temperature}
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                url, params={"key": self.api_key}, json=body
            )
            resp.raise_for_status()
            data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]


class OpenAIProvider(Provider):
    name = "openai"
    URL = "https://api.openai.com/v1/chat/completions"

    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model

    async def chat(self, messages, temperature=0.2) -> str:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                self.URL,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "messages": [m.model_dump() for m in messages],
                    "temperature": temperature,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]


MOCK_RESPONSES: dict[str, Any] = {}


def register_mock(schema_name: str, payload: Any) -> None:
    """Let tests / demo mode pin a canned structured response."""
    MOCK_RESPONSES[schema_name] = payload


class MockProvider(Provider):
    """Deterministic last resort so the pipeline runs with zero keys."""

    name = "mock"

    async def chat(self, messages, temperature=0.2) -> str:
        prompt = "\n".join(m.content for m in messages)
        match = re.search(r"Schema name:\s*(\w+)", prompt)
        schema_name = match.group(1) if match else ""
        if schema_name in MOCK_RESPONSES:
            payload = MOCK_RESPONSES[schema_name]
            if callable(payload):
                payload = payload()
            if isinstance(payload, str):
                return payload
            return json.dumps(payload)
        return json.dumps({"note": "mock response"})


def build_providers() -> list[Provider]:
    """Ordered chain: Groq (if keyed) -> Gemini -> OpenAI -> mock."""
    providers: list[Provider] = []
    if settings.groq_api_key:
        providers.append(GroqProvider(settings.groq_api_key, settings.groq_model))
        if settings.groq_fallback_model and settings.groq_fallback_model != settings.groq_model:
            providers.append(
                GroqProvider(settings.groq_api_key, settings.groq_fallback_model)
            )
    if settings.gemini_api_key:
        providers.append(GeminiProvider(settings.gemini_api_key, settings.gemini_model))
    if settings.openai_api_key:
        providers.append(OpenAIProvider(settings.openai_api_key, settings.openai_model))
    providers.append(MockProvider())
    return providers


class LLMClient:
    """Chat + validated structured output across the provider chain."""

    def __init__(self, providers: Optional[list[Provider]] = None) -> None:
        self.providers = providers if providers is not None else build_providers()
        self.last_provider: str = ""

    async def chat(
        self,
        prompt: str,
        system: str = "",
        temperature: float = 0.2,
    ) -> str:
        messages = []
        if system:
            messages.append(ChatMessage(role="system", content=system))
        messages.append(ChatMessage(role="user", content=prompt))
        errors: list[str] = []
        for provider in self.providers:
            try:
                text = await provider.chat(messages, temperature)
                self.last_provider = provider.name
                return text
            except (httpx.HTTPError, KeyError, IndexError, TypeError) as exc:
                errors.append(f"{provider.name}: {type(exc).__name__}")
                await asyncio.sleep(0.5)
        raise LLMError("all providers failed: " + ", ".join(errors))

    async def structured(
        self,
        prompt: str,
        schema: Type[T],
        system: str = "",
        temperature: float = 0.1,
    ) -> T:
        """Return schema-validated output; one repair retry per provider."""
        schema_hint = (
            f"Schema name: {schema.__name__}\n"
            "Respond with ONLY a JSON object matching this JSON schema:\n"
            + json.dumps(schema.model_json_schema(), ensure_ascii=False)
        )
        base_system = (system + "\n\n" if system else "") + schema_hint
        errors: list[str] = []
        injected_bad = FAILURE_INJECTION.get("mode") == "bad_json"
        for provider in self.providers:
            messages = [
                ChatMessage(role="system", content=base_system),
                ChatMessage(role="user", content=prompt),
            ]
            try:
                raw = await provider.chat(messages, temperature)
            except (httpx.HTTPError, KeyError, IndexError, TypeError) as exc:
                errors.append(f"{provider.name}: transport {type(exc).__name__}")
                continue
            if injected_bad:
                FAILURE_INJECTION["mode"] = ""
                raw = "{'broken': json,,"  # deliberately unparseable
                errors.append(f"{provider.name}: injected bad_json")
            try:
                parsed = schema.model_validate(_extract_json(raw))
                self.last_provider = provider.name
                return parsed
            except (ValidationError, json.JSONDecodeError, ValueError) as exc:
                errors.append(f"{provider.name}: invalid output")
                repair_prompt = (
                    ChatMessage(role="user", content=prompt),
                    ChatMessage(role="assistant", content=raw[:2000]),
                    ChatMessage(
                        role="user",
                        content=(
                            f"Your JSON failed validation: {exc}. "
                            "Reply with ONLY the corrected JSON object."
                        ),
                    ),
                )
                try:
                    raw2 = await provider.chat(list(repair_prompt), temperature)
                    parsed = schema.model_validate(_extract_json(raw2))
                    self.last_provider = provider.name
                    return parsed
                except (
                    ValidationError, json.JSONDecodeError, ValueError,
                    httpx.HTTPError, KeyError, IndexError, TypeError,
                ) as exc2:
                    errors.append(f"{provider.name}: repair failed {type(exc2).__name__}")
        raise LLMError("no provider produced valid JSON: " + "; ".join(errors))


llm_client = LLMClient()
