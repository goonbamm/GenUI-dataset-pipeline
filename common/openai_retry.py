"""Shared OpenAI chat completion retry helpers."""

from __future__ import annotations

import time

from openai import OpenAI


class UnsupportedNError(RuntimeError):
    """Raised when server does not support n>1 in chat.completions.create."""


def is_n_unsupported_error(error: Exception) -> bool:
    text = str(error).lower()
    patterns = [
        "does not support n",
        "unsupported value: 'n'",
        "unsupported parameter",
        "unexpected keyword argument 'n'",
        "n must be 1",
        "n is not supported",
        "only support n=1",
    ]
    return any(pattern in text for pattern in patterns)


def create_completion_with_retry(
    client: OpenAI,
    *,
    model: str,
    temperature: float,
    messages: list[dict[str, str]],
    n: int = 1,
    max_retries: int = 3,
    initial_backoff_sec: float = 1.0,
):
    attempt = 0
    while True:
        try:
            return client.chat.completions.create(
                model=model,
                n=n,
                temperature=temperature,
                messages=messages,
            )
        except Exception as error:
            if n > 1 and is_n_unsupported_error(error):
                raise UnsupportedNError(str(error)) from error
            attempt += 1
            if attempt > max_retries:
                raise
            time.sleep(initial_backoff_sec * (2 ** (attempt - 1)))
