import json
import urllib.error

import pytest

from data_analyst_agent.providers import (
    HTTPConfig,
    Message,
    OpenAICompatibleProvider,
    ProviderError,
    ScriptedProvider,
    config_from_env,
)

QUESTION: list[Message] = [{"role": "user", "content": "how many cases?"}]


def test_scripted_provider_returns_replies_in_order_and_keeps_requests() -> None:
    provider = ScriptedProvider(["first", "second"])
    assert provider.complete(QUESTION) == "first"
    assert provider.complete(QUESTION) == "second"
    assert provider.calls == [QUESTION, QUESTION]


def test_scripted_provider_refuses_when_out_of_replies() -> None:
    provider = ScriptedProvider([])
    with pytest.raises(ProviderError, match="no reply left"):
        provider.complete(QUESTION)


def test_config_reads_environment_and_allows_missing_key() -> None:
    config = config_from_env(
        {"ANALYST_BASE_URL": "http://localhost:11434/v1", "ANALYST_MODEL": "local-model"}
    )
    assert config == HTTPConfig("http://localhost:11434/v1", "local-model", None)


def test_config_names_what_is_missing() -> None:
    with pytest.raises(ProviderError, match="ANALYST_BASE_URL, ANALYST_MODEL"):
        config_from_env({})


def test_config_rejects_a_url_without_http_scheme() -> None:
    with pytest.raises(ProviderError, match="http"):
        HTTPConfig("file:///etc/passwd", "m")


class FakeTransport:
    """Records the request and answers with a canned body. Never touches the network."""

    def __init__(self, body: bytes | Exception) -> None:
        self.body = body
        self.seen: list[tuple[str, dict[str, str], dict[str, object], float]] = []

    def __call__(self, url: str, headers: dict[str, str], body: bytes, timeout: float) -> bytes:
        self.seen.append((url, headers, json.loads(body), timeout))
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


def _reply(text: str) -> bytes:
    return json.dumps({"choices": [{"message": {"role": "assistant", "content": text}}]}).encode()


def test_openai_provider_posts_chat_request_and_returns_text() -> None:
    transport = FakeTransport(_reply("select 1"))
    config = HTTPConfig("https://api.example.test/v1/", "m-1", api_key="k-test", timeout_s=5)
    provider = OpenAICompatibleProvider(config, transport)

    assert provider.complete(QUESTION) == "select 1"

    [(url, headers, body, timeout)] = transport.seen
    assert url == "https://api.example.test/v1/chat/completions"
    assert headers["Authorization"] == "Bearer k-test"
    assert body == {"model": "m-1", "messages": QUESTION, "temperature": 0}
    assert timeout == 5


def test_openai_provider_sends_no_auth_header_without_a_key() -> None:
    transport = FakeTransport(_reply("ok"))
    OpenAICompatibleProvider(HTTPConfig("http://localhost:8080", "m"), transport).complete(QUESTION)
    [(_, headers, _, _)] = transport.seen
    assert "Authorization" not in headers


def test_openai_provider_wraps_network_errors() -> None:
    transport = FakeTransport(urllib.error.URLError("connection refused"))
    provider = OpenAICompatibleProvider(HTTPConfig("http://localhost:1", "m"), transport)
    with pytest.raises(ProviderError, match="could not be reached"):
        provider.complete(QUESTION)


@pytest.mark.parametrize(
    "body",
    [b"not json", b'{"choices": []}', b'{"choices": [{"message": {"content": 3}}]}'],
)
def test_openai_provider_rejects_unusable_responses(body: bytes) -> None:
    provider = OpenAICompatibleProvider(HTTPConfig("http://localhost:1", "m"), FakeTransport(body))
    with pytest.raises(ProviderError):
        provider.complete(QUESTION)
