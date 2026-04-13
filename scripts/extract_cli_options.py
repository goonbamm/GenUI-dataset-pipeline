#!/usr/bin/env python3
"""Extract argparse option names/defaults from pipeline scripts.

Usage:
  python scripts/extract_cli_options.py
  python scripts/extract_cli_options.py generate_widget_tool_calls.py
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

DEFAULT_TARGETS = [
    "generate_mobile_widget_scenarios.py",
    "generate_widget_tool_calls.py",
    "generate_widget_example_json.py",
    "generate_genui_tsx.py",
    "run_pipeline.py",
]

OPENAI_SHARED_ARGS = [
    ("--base-url", "http://localhost:8000/v1 (or $VLLM_BASE_URL)"),
    ("--api-key", "EMPTY (or $VLLM_API_KEY)"),
    ("--model", "Qwen/Qwen2.5-7B-Instruct (or $VLLM_MODEL)"),
    ("--temperature", "script default"),
]


def _literal_or_none(node: ast.AST):
    try:
        return ast.literal_eval(node)
    except Exception:
        return None


def _extract_options(script_path: Path) -> list[tuple[str, str]]:
    src = script_path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    found: list[tuple[str, str]] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "add_argument":
            long_flags: list[str] = []
            for arg in node.args:
                value = _literal_or_none(arg)
                if isinstance(value, str) and value.startswith("--"):
                    long_flags.append(value)

            if not long_flags:
                continue

            option_name = " / ".join(long_flags)
            default_text = "(no explicit default)"

            for kw in node.keywords:
                if kw.arg == "default":
                    default_value = _literal_or_none(kw.value)
                    default_text = repr(default_value)
                elif kw.arg == "action":
                    action_name = None
                    if isinstance(kw.value, ast.Attribute):
                        action_name = kw.value.attr
                    elif isinstance(kw.value, ast.Constant):
                        action_name = str(kw.value.value)

                    if action_name == "BooleanOptionalAction":
                        default_text = "bool (supports --foo / --no-foo)"
                    elif action_name == "store_true":
                        default_text = "False (flag)"

            found.append((option_name, default_text))
            continue

        if isinstance(func, ast.Name) and func.id == "add_openai_cli_args":
            found.extend(OPENAI_SHARED_ARGS)

    # preserve order, remove duplicates
    deduped: list[tuple[str, str]] = []
    seen: set[str] = set()
    for key, value in found:
        if key in seen:
            continue
        seen.add(key)
        deduped.append((key, value))
    return deduped


def main() -> int:
    targets = [Path(p) for p in (sys.argv[1:] or DEFAULT_TARGETS)]

    for target in targets:
        if not target.exists():
            print(f"[WARN] not found: {target}")
            continue

        print(f"\n## {target}")
        for option, default_text in _extract_options(target):
            print(f"- `{option}` (default: {default_text})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
