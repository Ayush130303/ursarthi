"""LLM client with graceful degradation.

Default: Groq (free tier). Works with any OpenAI-compatible server (OpenAI,
Ollama, Together, LM Studio) via LLM_BASE_URL. When no key is configured or the API
fails (quota, network), callers fall back to extractive answers so the
product keeps working.
"""
from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Iterator

from app.core.config import get_settings

log = logging.getLogger(__name__)


class LLMUnavailable(RuntimeError):
    pass


class LLMClient:
    def __init__(self):
        s = get_settings()
        self.model = s.llm_model
        self.temperature = s.llm_temperature
        self.max_tokens = s.llm_max_tokens
        self._extra = {"reasoning_effort": s.llm_reasoning_effort} if s.llm_reasoning_effort and "gpt-oss" in s.llm_model else {}
        self._client = None
        self._down_until = 0.0  # circuit breaker after failures (quota, outage)
        local = any(h in s.llm_base_url for h in ("localhost", "127.0.0.1"))
        if s.llm_api_key or local:
            from openai import OpenAI

            self._client = OpenAI(
                api_key=s.llm_api_key or "not-needed",  # local servers (Ollama) ignore the key
                base_url=s.llm_base_url,
                timeout=s.llm_timeout_seconds,
                max_retries=1,
            )

    @property
    def available(self) -> bool:
        return self._client is not None and time.time() >= self._down_until

    def _trip(self, exc: Exception) -> None:
        """Skip the LLM for 60 s after a failure so users get fast fallback answers."""
        self._down_until = time.time() + 60
        log.warning("LLM call failed (%s); using extractive answers for 60 s", type(exc).__name__)

    def complete(self, messages: list[dict], json_mode: bool = False, max_tokens: int | None = None) -> str:
        if not self.available:
            raise LLMUnavailable("No LLM configured")
        kwargs = dict(model=self.model, messages=messages, temperature=self.temperature,
                      max_tokens=max_tokens or self.max_tokens, extra_body=self._extra or None)
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            resp = self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            self._trip(exc)
            raise LLMUnavailable(str(exc)) from exc
        return resp.choices[0].message.content or ""

    def stream(self, messages: list[dict]) -> Iterator[str]:
        if not self.available:
            raise LLMUnavailable("No LLM configured")
        try:
            stream = self._client.chat.completions.create(
                model=self.model, messages=messages, temperature=self.temperature,
                max_tokens=self.max_tokens, stream=True, extra_body=self._extra or None,
            )
        except Exception as exc:
            self._trip(exc)
            raise LLMUnavailable(str(exc)) from exc
        try:
            for event in stream:
                if event.choices and event.choices[0].delta and event.choices[0].delta.content:
                    yield event.choices[0].delta.content
        except Exception as exc:  # connection dropped mid-answer
            self._trip(exc)
            raise LLMUnavailable(str(exc)) from exc


def parse_json(text: str) -> dict:
    """Parse JSON from a model reply, tolerating ```json fences."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object in model output")
    return json.loads(text[start:end + 1])


_llm: LLMClient | None = None


def get_llm() -> LLMClient:
    global _llm
    if _llm is None:
        _llm = LLMClient()
    return _llm


def set_llm_for_tests(client) -> None:
    global _llm
    _llm = client
