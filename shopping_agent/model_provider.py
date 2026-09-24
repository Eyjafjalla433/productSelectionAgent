"""Optional structured model providers for the C-layer orchestrator.

The submission remains model-free by default. Providers are opt-in and use only
Python's standard library so local OpenAI-compatible servers and DeepSeek share
one narrow JSON contract.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


MAX_RESPONSE_BYTES = 1_000_000


class ModelProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class StructuredModelResult:
    data: dict[str, Any]
    model: str
    provider: str
    usage: dict[str, int]
    latency_ms: float


class StructuredModelProvider(Protocol):
    name: str
    model: str

    def complete_json(self, *, system: str, user: str, max_tokens: int = 700) -> StructuredModelResult: ...


class OpenAICompatibleJsonProvider:
    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        model: str,
        api_key: str | None,
        timeout_seconds: float = 20.0,
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("model base_url must be an absolute http(s) URL")
        if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("unencrypted model endpoints are allowed only on localhost")
        if not model.strip():
            raise ValueError("model must not be blank")
        if timeout_seconds <= 0 or timeout_seconds > 120:
            raise ValueError("timeout_seconds must be between 0 and 120")
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.model = model.strip()
        self.api_key = api_key
        self.timeout_seconds = float(timeout_seconds)
        self._opener = opener

    def complete_json(self, *, system: str, user: str, max_tokens: int = 700) -> StructuredModelResult:
        if not 1 <= max_tokens <= 4096:
            raise ValueError("max_tokens must be between 1 and 4096")
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": max_tokens,
            "stream": False,
        }
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        started = time.perf_counter()
        try:
            with self._opener(request, timeout=self.timeout_seconds) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            raise ModelProviderError(f"{self.name} returned HTTP {exc.code}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ModelProviderError(f"{self.name} request failed: {type(exc).__name__}") from exc
        latency_ms = round((time.perf_counter() - started) * 1000, 3)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ModelProviderError("model response exceeded the one-megabyte limit")
        try:
            envelope = json.loads(raw.decode("utf-8"))
            choice = envelope["choices"][0]
            if choice.get("finish_reason") == "length":
                raise ModelProviderError("model JSON was truncated at max_tokens")
            content = choice["message"]["content"]
            data = json.loads(content)
        except ModelProviderError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise ModelProviderError("model returned an invalid JSON completion envelope") from exc
        if not isinstance(data, dict):
            raise ModelProviderError("model JSON content must be an object")
        usage = envelope.get("usage") or {}
        normalized_usage = {
            "prompt_tokens": max(0, int(usage.get("prompt_tokens") or 0)),
            "completion_tokens": max(0, int(usage.get("completion_tokens") or 0)),
        }
        return StructuredModelResult(data, self.model, self.name, normalized_usage, latency_ms)


def create_model_provider(
    mode: str,
    *,
    base_url: str | None = None,
    model: str | None = None,
    timeout_seconds: float = 20.0,
) -> StructuredModelProvider | None:
    if mode == "off":
        return None
    if mode == "deepseek":
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            raise ValueError("DEEPSEEK_API_KEY is required for --model-provider deepseek")
        return OpenAICompatibleJsonProvider(
            name="deepseek",
            base_url=base_url or "https://api.deepseek.com",
            model=model or os.environ.get("DEEPSEEK_MODEL", "deepseek-flash"),
            api_key=api_key,
            timeout_seconds=timeout_seconds,
        )
    if mode == "local":
        return OpenAICompatibleJsonProvider(
            name="local",
            base_url=base_url or os.environ.get("LOCAL_MODEL_BASE_URL", "http://127.0.0.1:11434/v1"),
            model=model or os.environ.get("LOCAL_MODEL_NAME", "qwen2.5:7b"),
            api_key=os.environ.get("LOCAL_MODEL_API_KEY"),
            timeout_seconds=timeout_seconds,
        )
    raise ValueError("model provider must be one of: off, local, deepseek")
