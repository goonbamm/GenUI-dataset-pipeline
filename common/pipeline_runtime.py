"""Shared runtime helpers for multi-stage dataset generation scripts."""

from __future__ import annotations

import argparse
import datetime as dt
import os

from openai import OpenAI


def add_openai_cli_args(parser: argparse.ArgumentParser, *, default_temperature: float) -> None:
    """Add shared OpenAI-compatible endpoint/model CLI options."""
    parser.add_argument("--base-url", default=os.getenv("VLLM_BASE_URL", "http://localhost:8000/v1"))
    parser.add_argument("--api-key", default=os.getenv("VLLM_API_KEY", "EMPTY"))
    parser.add_argument("--model", default=os.getenv("VLLM_MODEL", "Qwen/Qwen2.5-7B-Instruct"))
    parser.add_argument("--temperature", type=float, default=default_temperature)


def create_openai_client(args: argparse.Namespace, *, http_client=None) -> OpenAI:
    """Create an OpenAI client from parsed CLI args."""
    if http_client is None:
        return OpenAI(base_url=args.base_url, api_key=args.api_key)
    return OpenAI(base_url=args.base_url, api_key=args.api_key, http_client=http_client)


def utc_now_iso() -> str:
    """Return timezone-aware UTC ISO8601 timestamp."""
    return dt.datetime.now(dt.timezone.utc).isoformat()
