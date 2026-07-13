"""``build_llm`` must forward the configured API key to every provider.

Regression: the Ollama branch silently dropped ``cfg.api_key``, so a key set in
the env (``DT_LLM__API_KEY``) never reached the wire. Ollama Cloud needs a Bearer
token, and the ollama client otherwise falls back to the ambient ``OLLAMA_API_KEY``
shell variable — meaning a stale shell key would mask the configured one. The key
rides in as a lowercase ``authorization`` header so it reliably wins that fallback.
"""

from __future__ import annotations

import pytest

from dyon.core.config import LLMConfig, TwinConfig
from dyon.intelligent.agent import build_llm


def _auth(llm) -> str | None:
    # ChatOllama -> ollama.Client -> httpx client; headers live two layers down.
    return llm._client._client.headers.get("authorization")


def test_ollama_forwards_api_key_as_bearer(monkeypatch):
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    cfg = TwinConfig(
        llm=LLMConfig(provider="ollama", model="m:cloud", api_key="real-key")
    )
    llm = build_llm(cfg)
    assert _auth(llm) == "Bearer real-key"
    # The async client (the agent's actual path) must carry it too.
    assert llm._async_client._client.headers.get("authorization") == "Bearer real-key"


def test_ollama_api_key_overrides_ambient_env(monkeypatch):
    # A stale OLLAMA_API_KEY in the shell must not win over the configured key.
    monkeypatch.setenv("OLLAMA_API_KEY", "stale-shell-key")
    cfg = TwinConfig(
        llm=LLMConfig(provider="ollama", model="m:cloud", api_key="configured-key")
    )
    assert _auth(build_llm(cfg)) == "Bearer configured-key"


def test_ollama_without_key_falls_back_to_ambient(monkeypatch):
    # Local Ollama (no key configured) keeps working via the ambient var.
    monkeypatch.setenv("OLLAMA_API_KEY", "local-key")
    cfg = TwinConfig(llm=LLMConfig(provider="ollama", model="m"))
    assert _auth(build_llm(cfg)) == "Bearer local-key"


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_cloud_providers_accept_api_key(provider):
    # Smoke-check the other branches still construct with a key present.
    cfg = TwinConfig(
        llm=LLMConfig(provider=provider, model="x", api_key="k", temperature=0.0)
    )
    assert build_llm(cfg) is not None
