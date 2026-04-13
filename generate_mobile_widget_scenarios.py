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
import math
import re
from pathlib import Path
from typing import Iterable

from common.pipeline_runtime import add_openai_cli_args, create_openai_client, utc_now_iso
from common.schemas import STAGE1_FIELDS, STAGE1_REQUIRED_FIELDS, ensure_required_columns
from common.text import normalize_spaces, normalize_text as common_normalize_text, strip_list_prefix

DEFAULT_CATEGORIES = [
    "쇼핑",
    "음악",
    "미디어",
    "캘린더",
    "여행",
    "요리",
    "운동",
    "금융",
    "생산성",
    "커뮤니케이션",
    "헬스케어",
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

def normalize_text(text: str) -> str:
    return common_normalize_text(text, strip_prefix=True)


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


def load_existing(csv_path: Path) -> tuple[dict[str, set[str]], set[str]]:
    existing_by_category: dict[str, set[str]] = {}
    existing_scenarios: set[str] = set()

    if not csv_path.exists():
        return existing_by_category, existing_scenarios

    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        ensure_required_columns(
            reader.fieldnames,
            STAGE1_REQUIRED_FIELDS,
            label="Scenario CSV",
        )
        for row in reader:
            category = (row.get("category") or "").strip()
            scenario = (row.get("scenario") or "").strip()
            if category:
                existing_by_category.setdefault(category, set())
            if scenario:
                scenario_key = normalize_text(scenario)
                existing_scenarios.add(scenario_key)
                if category:
                    existing_by_category.setdefault(category, set()).add(scenario_key)

    return existing_by_category, existing_scenarios


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
    return normalize_spaces(strip_list_prefix(text.strip()))


def extract_scenarios(text: str) -> list[str]:
    if not text:
        return []
    lines = [line for line in text.strip().splitlines() if line.strip()]
    return [sanitize_scenario(line) for line in lines if sanitize_scenario(line)]


def is_valid_surface_form(s: str) -> bool:
    text = s.strip()
    if not text:
        return False

    if not (12 <= len(text) <= 64):
        return False

    words = [token for token in text.split() if token]
    if not (3 <= len(words) <= 6):
        return False

    if any(mark in text for mark in ".!?"):
        return False

    if text.count(":") > 1:
        return False

    if not re.fullmatch(r"[A-Za-z0-9:\- ]+", text):
        return False

    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv-path", default="mobile_widget_scenarios.csv")
    add_openai_cli_args(parser, default_temperature=0.8)
    parser.add_argument("--responses-per-category", type=int, default=1)
    parser.add_argument("--scenarios-per-response", type=int, default=5)
    parser.add_argument("--target-per-category", type=int, default=None)
    parser.add_argument("--categories", nargs="*", default=DEFAULT_CATEGORIES)
    parser.add_argument("--max-examples", type=int, default=5)
    parser.add_argument("--max-disallow", type=int, default=5)
    args = parser.parse_args()
    if args.scenarios_per_response <= 0:
        parser.error("--scenarios-per-response must be a positive integer")
    if args.responses_per_category <= 0:
        parser.error("--responses-per-category must be a positive integer")
    if args.target_per_category is not None and args.target_per_category <= 0:
        parser.error("--target-per-category must be a positive integer when provided")

    csv_path = Path(args.csv_path)
    examples = unique_preserve_order(RAW_EXAMPLES)[: args.max_examples]
    examples_norm = {normalize_text(e) for e in examples}

    existing_by_category, existing_scenarios = load_existing(csv_path)

    client = create_openai_client(args)

    rows_to_append: list[dict[str, str]] = []
    generated_norm: set[str] = set(existing_scenarios)
    target_per_category = args.target_per_category
    if target_per_category is None:
        target_per_category = args.responses_per_category * args.scenarios_per_response

    target_categories = [c.strip() for c in args.categories if c.strip()]

    for category in target_categories:
        category_existing = existing_by_category.get(category, set())
        existing_count = len(category_existing)
        print(f"[PROGRESS] {category}: existing {existing_count} / target {target_per_category}")

        needed = target_per_category - existing_count
        if needed <= 0:
            continue

        disallow = unique_preserve_order(list(existing_scenarios))[: args.max_disallow]
        accepted = 0
        now = utc_now_iso()
        planned_responses = max(1, math.ceil(needed / args.scenarios_per_response))

        for _ in range(planned_responses):
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
                    if accepted >= needed:
                        break

                    key = normalize_text(scenario)
                    if not scenario:
                        continue
                    if not is_valid_surface_form(scenario):
                        continue
                    if key in generated_norm:
                        continue
                    if key in examples_norm:
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

                if accepted >= needed:
                    break

            if accepted >= needed:
                break

        print(f"[DONE] {category}: accepted {accepted} / needed {needed} (target {target_per_category})")

    if not rows_to_append:
        print("No new scenarios generated.")
        return

    file_exists = csv_path.exists()
    write_mode = "a" if file_exists else "w"
    write_encoding = "utf-8" if file_exists else "utf-8-sig"
    with csv_path.open(write_mode, encoding=write_encoding, newline="") as f:
        writer = csv.DictWriter(f, fieldnames=STAGE1_FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows_to_append)

    print(f"Saved {len(rows_to_append)} rows to {csv_path}")


if __name__ == "__main__":
    main()
