"""Talk to a language model. The agent only needs one thing from it: text back for a list
of chat messages. Tests use ``ScriptedProvider``; real runs use an OpenAI-compatible
chat endpoint configured from the environment, so the same code works with a hosted
API or a local server.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, TypedDict


class Message(TypedDict):
    role: str
    content: str


class ProviderError(RuntimeError):
    """The model could not be reached or answered in a shape we cannot use."""


class Provider(Protocol):
    def complete(self, messages: Sequence[Message]) -> str: ...


class ScriptedProvider:
    """Returns canned replies in order and keeps every request it was given."""

    def __init__(self, replies: Iterable[str]) -> None:
        self._replies: deque[str] = deque(replies)
        self.calls: list[list[Message]] = []

    def complete(self, messages: Sequence[Message]) -> str:
        self.calls.append(list(messages))
        if not self._replies:
            raise ProviderError("scripted provider has no reply left")
        return self._replies.popleft()


ENV_BASE_URL = "ANALYST_BASE_URL"
ENV_MODEL = "ANALYST_MODEL"
ENV_API_KEY = "ANALYST_API_KEY"


@dataclass(frozen=True)
class HTTPConfig:
    base_url: str
    model: str
    api_key: str | None = None
    timeout_s: float = 60.0

    def __post_init__(self) -> None:
        scheme = urllib.parse.urlsplit(self.base_url).scheme
        if scheme not in ("http", "https"):
            raise ProviderError(f"{ENV_BASE_URL} must start with http:// or https://")


def config_from_env(env: Mapping[str, str]) -> HTTPConfig:
    """Read the endpoint, model and optional key. Local servers need no key."""
    missing = [name for name in (ENV_BASE_URL, ENV_MODEL) if not env.get(name)]
    if missing:
        raise ProviderError("missing environment variables: " + ", ".join(missing))
    return HTTPConfig(
        base_url=env[ENV_BASE_URL],
        model=env[ENV_MODEL],
        api_key=env.get(ENV_API_KEY) or None,
    )


# The transport sends one POST and returns the response body. Injected so tests never
# touch the network.
Transport = Callable[[str, dict[str, str], bytes, float], bytes]


def _urllib_transport(url: str, headers: dict[str, str], body: bytes, timeout: float) -> bytes:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data: bytes = response.read()
        return data


class OpenAICompatibleProvider:
    """A client for any server that implements ``POST /chat/completions``."""

    def __init__(self, config: HTTPConfig, transport: Transport = _urllib_transport) -> None:
        self.config = config
        self._transport = transport

    def complete(self, messages: Sequence[Message]) -> str:
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        body = json.dumps(
            {"model": self.config.model, "messages": list(messages), "temperature": 0}
        ).encode("utf-8")
        url = self.config.base_url.rstrip("/") + "/chat/completions"
        try:
            raw = self._transport(url, headers, body, self.config.timeout_s)
        except (urllib.error.URLError, TimeoutError, OSError) as err:
            raise ProviderError(f"model endpoint could not be reached: {err}") from err
        return _content(raw)


def _content(raw: bytes) -> str:
    try:
        data: Any = json.loads(raw)
        text = data["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as err:
        raise ProviderError("model response has no message content") from err
    if not isinstance(text, str):
        raise ProviderError("model response content is not text")
    return text
