#!/usr/bin/env python3
"""Generate Generative UI mobile widget scenarios by category via vLLM.

Features:
- Uses OpenAI-compatible Chat Completions endpoint (vLLM serving)
- Generates 5 scenarios per model response by default
- Skips categories that already exist in the target CSV
- Avoids duplicates from examples and existing scenarios in prompt constraints
- Saves rows to CSV with generation date/model/prompt/scenario/category
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import re
from pathlib import Path
from typing import Iterable

from openai import OpenAI
from csv_io import open_csv_for_append

DEFAULT_CATEGORIES = [
    "쇼핑",
    "음악",
    "미디어",
    "캘린더",
    "여행",
    "요리",
    "운동",
]

RAW_EXAMPLES = [
    "flight check-in status",
    "flight boarding pass",
    "email compose draft",
    "calendar day agenda",
    "user profile summary",
    "login verification form",
    "sports player comparison",
    "restaurant nearby map",
    "countdown timer setup",
    "account balance overview",
    "shipping status timeline",
    "movie ticket checkout",
    "notification permission prompt",
    "workout plan detail",
    "credit card statement",
    "step counter trends",
    "event invitation builder",
    "weather current conditions",
    "purchase completion receipt",
    "event detail timeline",
    "recipe ingredient checklist",
    "product variant selector",
    "chat message thread",
    "music track queue",
    "analytics dashboard snapshot",
    "music player controls",
    "task priority board",
    "coffee order customization",
    "software subscription checkout",
    "contact card actions",
    "podcast episode details",
    "budget spending breakdown",
    "hotel search results",
    "hotel room comparison",
    "hotel detail overview",
    "hotel booking payment",
    "hotel booking confirmation",
]

CSV_FIELDS = ["created_at", "model", "prompt", "category", "scenario"]


def normalize_text(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"^[\-\d\.)\s]+", "", text)
    return re.sub(r"\s+", " ", text)


def unique_preserve_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = normalize_text(item)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item.strip())
    return out


def load_existing(csv_path: Path) -> tuple[set[str], set[str]]:
    existing_categories: set[str] = set()
    existing_scenarios: set[str] = set()

    if not csv_path.exists():
        return existing_categories, existing_scenarios

    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            category = (row.get("category") or "").strip()
            scenario = (row.get("scenario") or "").strip()
            if category:
                existing_categories.add(category)
            if scenario:
                existing_scenarios.add(normalize_text(scenario))

    return existing_categories, existing_scenarios


def build_prompt(category: str, examples: list[str], disallow: list[str]) -> str:
    example_text = "\n".join(f"- {item}" for item in examples)
    disallow_text = "\n".join(f"- {item}" for item in disallow) if disallow else "- (none)"

    return f"""You are generating scenario names for a Generative UI mobile widget dataset.

Category: {category}
Goal: Create multiple new scenario names for this category.
Language: English
Style: concrete mobile UI surface, short noun phrase (3-6 words), lowercase preferred.

Hard constraints:
1) Do NOT output anything except the scenario name.
2) Do NOT duplicate or paraphrase existing examples/disallowed list.
3) Must clearly belong to category '{category}'.
4) Keep it practical for mobile widget UIs.
5) Avoid abstract umbrella concepts (e.g., "hotel reservation", "travel booking", "music app").
6) Prefer one specific user intent or screen state (e.g., search results, comparison, details, checkout, confirmation, tracking, summary).
7) Each scenario should imply what the widget is helping the user do right now.

Reference examples (do not reuse):
{example_text}

Disallowed existing scenarios (do not reuse):
{disallow_text}
"""


def sanitize_scenario(text: str) -> str:
    cleaned = re.sub(r"^[\-\d\.)\s]+", "", text.strip())
    return re.sub(r"\s+", " ", cleaned)


def extract_scenarios(text: str) -> list[str]:
    if not text:
        return []
    lines = [line for line in text.strip().splitlines() if line.strip()]
    return [sanitize_scenario(line) for line in lines if sanitize_scenario(line)]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv-path", default="mobile_widget_scenarios.csv")
    parser.add_argument("--base-url", default=os.getenv("VLLM_BASE_URL", "http://localhost:8000/v1"))
    parser.add_argument("--api-key", default=os.getenv("VLLM_API_KEY", "EMPTY"))
    parser.add_argument("--model", default=os.getenv("VLLM_MODEL", "Qwen/Qwen2.5-7B-Instruct"))
    parser.add_argument("--temperature", type=float, default=0.8)
    parser.add_argument("--responses-per-category", type=int, default=1)
    parser.add_argument("--scenarios-per-response", type=int, default=5)
    parser.add_argument("--categories", nargs="*", default=DEFAULT_CATEGORIES)
    parser.add_argument("--max-examples", type=int, default=5)
    parser.add_argument("--max-disallow", type=int, default=5)
    args = parser.parse_args()

    csv_path = Path(args.csv_path)
    examples = unique_preserve_order(RAW_EXAMPLES)[: args.max_examples]

    existing_categories, existing_scenarios = load_existing(csv_path)

    client = OpenAI(base_url=args.base_url, api_key=args.api_key)

    rows_to_append: list[dict[str, str]] = []
    generated_norm: set[str] = set(existing_scenarios)

    target_categories = [c.strip() for c in args.categories if c.strip()]

    for category in target_categories:
        if category in existing_categories:
            print(f"[SKIP] Category already exists in CSV: {category}")
            continue

        disallow = unique_preserve_order(list(existing_scenarios))[: args.max_disallow]
        accepted = 0
        now = dt.datetime.now(dt.timezone.utc).isoformat()

        for _ in range(args.responses_per_category):
            prompt = build_prompt(category=category, examples=examples, disallow=disallow)
            prompt = (
                f"{prompt}\n"
                f"Output format: return exactly {args.scenarios_per_response} scenario names, "
                "one per line, no extra text."
            )
            completion = client.chat.completions.create(
                model=args.model,
                n=1,
                temperature=args.temperature,
                messages=[
                    {"role": "system", "content": "You produce concise scenario names."},
                    {"role": "user", "content": prompt},
                ],
            )

            for choice in completion.choices:
                scenarios = extract_scenarios(choice.message.content or "")
                for scenario in scenarios[: args.scenarios_per_response]:
                    key = normalize_text(scenario)
                    if not scenario:
                        continue
                    if key in generated_norm:
                        continue
                    if key in {normalize_text(e) for e in examples}:
                        continue

                    rows_to_append.append(
                        {
                            "created_at": now,
                            "model": args.model,
                            "prompt": prompt,
                            "category": category,
                            "scenario": scenario,
                        }
                    )
                    generated_norm.add(key)
                    accepted += 1

        requested = args.responses_per_category * args.scenarios_per_response
        print(f"[DONE] {category}: accepted {accepted} / requested {requested}")

    if not rows_to_append:
        print("No new scenarios generated.")
        return

    f, should_write_header = open_csv_for_append(csv_path)
    with f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if should_write_header:
            writer.writeheader()
        writer.writerows(rows_to_append)

    print(f"Saved {len(rows_to_append)} rows to {csv_path}")


if __name__ == "__main__":
    main()
