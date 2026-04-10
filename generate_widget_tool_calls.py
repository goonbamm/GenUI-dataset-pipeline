#!/usr/bin/env python3
"""Generate tool calls for widget scenarios via vLLM (OpenAI-compatible API).

Stage 2 helper script:
- Reads scenario rows from stage1 CSV (default: mobile_widget_scenarios.csv)
- For each scenario, requests up to N tool calls (default: 3)
- Appends results to tool-call CSV (default: mobile_widget_tool_calls.csv)
- Keeps duplicates if they differ by created date/model/scenario (intentional)
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import datetime as dt
import os
import time
from pathlib import Path

from openai import OpenAI

from common.text import normalize_spaces, normalize_text as common_normalize_text, strip_list_prefix

TOOL_CALL_FIELDS = [
    "created_at",
    "model",
    "row_index",
    "sample_index",
    "scenario_created_at",
    "scenario_model",
    "category",
    "scenario",
    "prompt",
    "tool_call",
]

TOOL_CALL_EXAMPLES = [
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
    return common_normalize_text(text, strip_prefix=True)


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
    return f"""You create function-style tool calls for a Generative UI widget scenario.

Category: {category}
Scenario: {scenario}
Goal: Propose practical tool calls a widget can execute.

Output constraints:
1) Return only tool call lines.
2) Each line must use this format: function_name(params) - short description
3) Use snake_case for function_name.
4) Keep each description concise and concrete.
5) Provide at most {max_items} items.
6) Avoid duplicates in this response.

Examples:
{example_text}
"""


def sanitize_tool_call(text: str) -> str:
    return normalize_spaces(strip_list_prefix(text.strip()))


def extract_tool_calls(text: str) -> list[str]:
    if not text:
        return []

    results: list[str] = []
    seen: set[str] = set()

    for raw in text.strip().splitlines():
        item = sanitize_tool_call(raw)
        if not item:
            continue
        key = normalize_text(item)
        if key in seen:
            continue
        seen.add(key)
        results.append(item)

    return results


def create_completion_with_retry(
    client: OpenAI,
    *,
    model: str,
    temperature: float,
    messages: list[dict[str, str]],
    max_retries: int = 3,
    initial_backoff_sec: float = 1.0,
):
    attempt = 0
    while True:
        try:
            return client.chat.completions.create(
                model=model,
                n=1,
                temperature=temperature,
                messages=messages,
            )
        except Exception:
            attempt += 1
            if attempt > max_retries:
                raise
            time.sleep(initial_backoff_sec * (2 ** (attempt - 1)))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario-csv", default="mobile_widget_scenarios.csv")
    parser.add_argument("--tool-call-csv", default="mobile_widget_tool_calls.csv")
    parser.add_argument("--base-url", default=os.getenv("VLLM_BASE_URL", "http://localhost:8000/v1"))
    parser.add_argument("--api-key", default=os.getenv("VLLM_API_KEY", "EMPTY"))
    parser.add_argument("--model", default=os.getenv("VLLM_MODEL", "Qwen/Qwen2.5-7B-Instruct"))
    parser.add_argument("--temperature", type=float, default=0.4)
    parser.add_argument("--max-items-per-scenario", type=int, default=3)
    parser.add_argument("--max-examples", type=int, default=10)
    parser.add_argument("--limit-scenarios", type=int, default=0)
    parser.add_argument("--max-concurrency", type=int, default=6)
    args = parser.parse_args()

    if args.max_items_per_scenario < 1:
        raise ValueError("--max-items-per-scenario must be >= 1")
    if args.max_concurrency < 1:
        raise ValueError("--max-concurrency must be >= 1")

    scenario_rows = load_scenarios(Path(args.scenario_csv))
    if args.limit_scenarios > 0:
        scenario_rows = scenario_rows[: args.limit_scenarios]

    if not scenario_rows:
        print("No scenarios found to process.")
        return

    examples = TOOL_CALL_EXAMPLES[: max(1, args.max_examples)]

    client = OpenAI(base_url=args.base_url, api_key=args.api_key)

    def process_row(row_index: int, row: dict[str, str]) -> dict[str, object]:
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

        completion = create_completion_with_retry(
            client,
            model=args.model,
            temperature=args.temperature,
            messages=[
                {
                    "role": "system",
                    "content": "You output concise widget tool calls in function format.",
                },
                {"role": "user", "content": prompt},
            ],
        )

        output_text = completion.choices[0].message.content or ""
        items = extract_tool_calls(output_text)[: args.max_items_per_scenario]
        return {
            "row_index": row_index,
            "sample_index": 1,
            "row": row,
            "prompt": prompt,
            "items": items,
        }

    rows_to_append: list[dict[str, str]] = []
    total = len(scenario_rows)
    done = 0
    ordered_results: list[dict[str, object]] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.max_concurrency) as executor:
        future_to_row = {
            executor.submit(process_row, row_index, row): (row_index, row)
            for row_index, row in enumerate(scenario_rows, start=1)
        }

        for future in concurrent.futures.as_completed(future_to_row):
            done += 1
            row_index, row = future_to_row[future]
            try:
                ordered_results.append(future.result())
                print(f"[DONE] {done}/{total} row={row_index} {row['category']} | {row['scenario']}")
            except Exception as e:
                print(
                    f"[WARN] {done}/{total} row={row_index} {row['category']} | {row['scenario']} "
                    f"request failed after retries: {e}"
                )

    ordered_results.sort(key=lambda x: (int(x["row_index"]), int(x["sample_index"])))

    for result in ordered_results:
        row_index = int(result["row_index"])
        sample_index = int(result["sample_index"])
        row = result["row"]
        prompt = str(result["prompt"])
        items = result["items"]

        now = dt.datetime.now(dt.timezone.utc).isoformat()
        for item in items:
            rows_to_append.append(
                {
                    "created_at": now,
                    "model": args.model,
                    "row_index": str(row_index),
                    "sample_index": str(sample_index),
                    "scenario_created_at": row["scenario_created_at"],
                    "scenario_model": row["scenario_model"],
                    "category": row["category"],
                    "scenario": row["scenario"],
                    "prompt": prompt,
                    "tool_call": item,
                }
            )

    if not rows_to_append:
        print("No tool calls generated.")
        return

    tool_call_csv_path = Path(args.tool_call_csv)
    file_exists = tool_call_csv_path.exists()
    write_mode = "a" if file_exists else "w"
    write_encoding = "utf-8" if file_exists else "utf-8-sig"
    with tool_call_csv_path.open(write_mode, encoding=write_encoding, newline="") as f:
        writer = csv.DictWriter(f, fieldnames=TOOL_CALL_FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows_to_append)

    print(f"Saved {len(rows_to_append)} rows to {tool_call_csv_path}")


if __name__ == "__main__":
    main()
