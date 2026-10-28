import logging
from types import SimpleNamespace

import pytest

import llm_client
from llm_client import LLMTimeoutError, ask_llm


def chunk(text: str, done: bool = False, done_reason: str | None = None):
    return SimpleNamespace(message=SimpleNamespace(content=text), done=done, done_reason=done_reason, eval_count=3 if done else None)


@pytest.fixture
def fake_ollama(monkeypatch):
    """Replace the Ollama client with one that streams the given chunks and records the call options."""
    calls = []

    def install(chunks):
        class FakeClient:
            def __init__(self, **kwargs):
                pass

            def chat(self, **kwargs):
                calls.append(kwargs)
                return iter(chunks)

        monkeypatch.setattr(llm_client, "Client", FakeClient)
        return calls

    return install


def test_streamed_chunks_are_joined_and_limits_are_sent(fake_ollama):
    calls = fake_ollama([chunk("import "), chunk("cadquery"), chunk("", done=True, done_reason="stop")])

    assert ask_llm("hi", temperature=0) == "import cadquery"
    options = calls[0]["options"]
    assert options["num_predict"] == llm_client.settings.max_tokens
    assert options["num_ctx"] == llm_client.settings.context_tokens
    assert options["temperature"] == 0


def test_cut_answer_is_logged(fake_ollama, caplog):
    fake_ollama([chunk("x = 1"), chunk("", done=True, done_reason="length")])

    with caplog.at_level(logging.WARNING):
        ask_llm("hi")

    assert "cut at" in caplog.text


def test_slow_answer_raises_timeout(fake_ollama, monkeypatch):
    fake_ollama([chunk("a"), chunk("b"), chunk("c")])
    monkeypatch.setattr(llm_client.settings, "timeout_s", -1)

    with pytest.raises(LLMTimeoutError):
        ask_llm("hi")
