"""
Define entry point to local llm and system configuration
"""

import logging
import sys
import time
from dataclasses import dataclass
from typing import Any

from ollama import Client

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "qwen2.5-coder:3b"

SYSTEM_PROMPT = (
    "You are an assistant for parametric CAD generation. "
    "Follow the user's requested output format exactly. "
    "Never silently invent unspecified engineering dimensions. "
    "Report any inconsistency or uncertainty when designing."
)


@dataclass
class LLMSettings:
    # Hard limit on the length of one answer; small models can otherwise repeat themselves forever
    max_tokens: int = 1500
    # Ollama's default context is small and it silently drops the start of longer prompts
    context_tokens: int = 8192
    # Wall-clock limit for one answer, checked while the answer streams in
    timeout_s: float = 300.0
    # Echo the answer to the terminal token by token, to see what the model is doing
    stream_to_terminal: bool = False


settings = LLMSettings()


class LLMTimeoutError(TimeoutError):
    """Raised when the model takes longer than settings.timeout_s for one answer."""


def ask_llm(
    prompt: str | None = None,
    model: str = DEFAULT_MODEL,
    *,
    messages: list[dict[str, str]] | None = None,
    schema: dict[str, Any] | None = None,
    temperature: float | None = None,
) -> str | None:
    """
    Send a single prompt, or a full conversation via `messages`, to the local LLM.
    If `schema` is given, the output is constrained to JSON matching that JSON schema.
    """
    if messages is None:
        if prompt is None:
            raise ValueError("Either prompt or messages must be given")
        messages = [{"role": "user", "content": prompt}]

    options: dict[str, Any] = {"num_predict": settings.max_tokens, "num_ctx": settings.context_tokens}
    if temperature is not None:
        options["temperature"] = temperature

    logger.debug("Prompt to %s:\n%s", model, messages[-1]["content"])
    logger.info("Waiting for %s (%d messages, up to %d tokens)...", model, len(messages), settings.max_tokens)

    # The client timeout covers connecting and each streamed chunk; the total time is checked below
    client = Client(timeout=60)
    stream = client.chat(
        model=model,
        messages=[{"role": "system", "content": SYSTEM_PROMPT}, *messages],
        format=schema,
        options=options,
        stream=True,
    )

    start = time.perf_counter()
    parts: list[str] = []
    done_reason = None
    token_count = 0
    for chunk in stream:
        text = chunk.message.content or ""
        parts.append(text)
        if settings.stream_to_terminal:
            sys.stderr.write(text)
            sys.stderr.flush()
        if chunk.done:
            done_reason, token_count = chunk.done_reason, chunk.eval_count or 0
        elif time.perf_counter() - start > settings.timeout_s:
            raise LLMTimeoutError(f"The model did not finish its answer within {settings.timeout_s:g} s")

    if settings.stream_to_terminal:
        sys.stderr.write("\n")

    content = "".join(parts)
    elapsed = time.perf_counter() - start
    logger.info("%s answered in %.1f s (%d tokens)", model, elapsed, token_count)
    if done_reason == "length":
        logger.warning("The answer was cut at %d tokens, the model was probably repeating itself", settings.max_tokens)
    logger.debug("Response from %s:\n%s", model, content)

    return content
