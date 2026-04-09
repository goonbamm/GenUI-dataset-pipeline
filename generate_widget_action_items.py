#!/usr/bin/env python3
"""Generate action items for widget scenarios via vLLM (OpenAI-compatible API).

Stage 2 helper script:
- Reads scenario rows from stage1 CSV (default: mobile_widget_scenarios.csv)
- For each scenario, requests up to N action items (default: 3)
- Appends results to action-item CSV (default: mobile_widget_action_items.csv)
- Keeps duplicates if they differ by created date/model/scenario (intentional)
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import re
from pathlib import Path

from openai import OpenAI
from csv_io import open_csv_for_append

SCENARIO_FIELDS = ["created_at", "model", "prompt", "category", "scenario"]
ACTION_FIELDS = [
    "created_at",
    "model",
    "scenario_created_at",
    "scenario_model",
    "category",
    "scenario",
    "prompt",
    "action_item",
]

ACTION_EXAMPLES = [
    "get_weather(params) - weather widget",
    "search_flights(params) - flight search",
    "show_stock_chart(params) - stock chart",
    "search_products(params) - product search",
    "book_restaurant(params) - restaurant booking",
    "show_map_location(params) - map location",
    "create_calendar_event(params) - calendar events",
    "play_music(params) - music player",
    "track_package(params) - package tracking",
    "show_recipe(params) - recipe card",
]


def normalize_text(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"^[\-\d\.)\s]+", "", text)
    return re.sub(r"\s+", " ", text)


def load_scenarios(csv_path: Path) -> list[dict[str, str]]:
    if not csv_path.exists():
        raise FileNotFoundError(f"Scenario CSV not found: {csv_path}")

    rows: list[dict[str, str]] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        missing = [col for col in ("category", "scenario") if col not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(
                f"Scenario CSV is missing required columns {missing}. "
                f"Found columns: {reader.fieldnames}"
            )

        for row in reader:
            scenario = (row.get("scenario") or "").strip()
            if not scenario:
                continue
            rows.append(
                {
                    "scenario_created_at": (row.get("created_at") or "").strip(),
                    "scenario_model": (row.get("model") or "").strip(),
                    "category": (row.get("category") or "").strip(),
                    "scenario": scenario,
                }
            )

    return rows


def build_prompt(category: str, scenario: str, examples: list[str], max_items: int) -> str:
    example_text = "\n".join(f"- {item}" for item in examples)
    return f"""You create function-style action items for a Generative UI widget scenario.

Category: {category}
Scenario: {scenario}
Goal: Propose practical actions a widget can execute.

Output constraints:
1) Return only action item lines.
2) Each line must use this format: function_name(params) - short description
3) Use snake_case for function_name.
4) Keep each description concise and concrete.
5) Provide at most {max_items} items.
6) Avoid duplicates in this response.

Examples:
{example_text}
"""


def sanitize_action_item(text: str) -> str:
    cleaned = re.sub(r"^[\-\d\.)\s]+", "", text.strip())
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned


def extract_action_items(text: str) -> list[str]:
    if not text:
        return []

    results: list[str] = []
    seen: set[str] = set()

    for raw in text.strip().splitlines():
        item = sanitize_action_item(raw)
        if not item:
            continue
        key = normalize_text(item)
        if key in seen:
            continue
        seen.add(key)
        results.append(item)

    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario-csv", default="mobile_widget_scenarios.csv")
    parser.add_argument("--action-csv", default="mobile_widget_action_items.csv")
    parser.add_argument("--base-url", default=os.getenv("VLLM_BASE_URL", "http://localhost:8000/v1"))
    parser.add_argument("--api-key", default=os.getenv("VLLM_API_KEY", "EMPTY"))
    parser.add_argument("--model", default=os.getenv("VLLM_MODEL", "Qwen/Qwen2.5-7B-Instruct"))
    parser.add_argument("--temperature", type=float, default=0.4)
    parser.add_argument("--max-items-per-scenario", type=int, default=3)
    parser.add_argument("--max-examples", type=int, default=10)
    parser.add_argument("--limit-scenarios", type=int, default=0)
    args = parser.parse_args()

    if args.max_items_per_scenario < 1:
        raise ValueError("--max-items-per-scenario must be >= 1")

    scenario_rows = load_scenarios(Path(args.scenario_csv))
    if args.limit_scenarios > 0:
        scenario_rows = scenario_rows[: args.limit_scenarios]

    if not scenario_rows:
        print("No scenarios found to process.")
        return

    examples = ACTION_EXAMPLES[: max(1, args.max_examples)]

    client = OpenAI(base_url=args.base_url, api_key=args.api_key)

    rows_to_append: list[dict[str, str]] = []

    for idx, row in enumerate(scenario_rows, start=1):
        prompt = build_prompt(
            category=row["category"],
            scenario=row["scenario"],
            examples=examples,
            max_items=args.max_items_per_scenario,
        )
        prompt = (
            f"{prompt}\n"
            f"Output format: return 1 to {args.max_items_per_scenario} lines, no extra text."
        )

        completion = client.chat.completions.create(
            model=args.model,
            n=1,
            temperature=args.temperature,
            messages=[
                {
                    "role": "system",
                    "content": "You output concise widget action items in function format.",
                },
                {"role": "user", "content": prompt},
            ],
        )

        output_text = completion.choices[0].message.content or ""
        items = extract_action_items(output_text)[: args.max_items_per_scenario]

        now = dt.datetime.now(dt.timezone.utc).isoformat()
        for item in items:
            rows_to_append.append(
                {
                    "created_at": now,
                    "model": args.model,
                    "scenario_created_at": row["scenario_created_at"],
                    "scenario_model": row["scenario_model"],
                    "category": row["category"],
                    "scenario": row["scenario"],
                    "prompt": prompt,
                    "action_item": item,
                }
            )

        print(
            f"[DONE] {idx}/{len(scenario_rows)} "
            f"{row['category']} | {row['scenario']}: {len(items)} items"
        )

    if not rows_to_append:
        print("No action items generated.")
        return

    action_csv_path = Path(args.action_csv)
    f, should_write_header = open_csv_for_append(action_csv_path)
    with f:
        writer = csv.DictWriter(f, fieldnames=ACTION_FIELDS)
        if should_write_header:
            writer.writeheader()
        writer.writerows(rows_to_append)

    print(f"Saved {len(rows_to_append)} rows to {action_csv_path}")


if __name__ == "__main__":
    main()
