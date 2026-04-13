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
import re
from pathlib import Path

from common.pipeline_runtime import add_openai_cli_args, create_openai_client, utc_now_iso
from common.openai_retry import create_completion_with_retry
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
    'get_weather(city="Seoul", date="2026-04-12", unit="celsius") - 3-day weather widget',
    'search_flights(origin="ICN", destination="NRT", depart_date="2026-05-02", passengers=1) - flight search results',
    'show_stock_chart(ticker="AAPL", period="1M", interval="1D") - monthly stock trend chart',
    'search_products(query="wireless earbuds", sort="rating", price_max=150000) - product list for shopping widget',
    'book_restaurant(name="Mingles", date="2026-04-18", time="19:00", party_size=2) - reservation request',
    'show_map_location(place="Gangnam Station", zoom=15, transport="transit") - location map card',
    'create_calendar_event(title="design review", start_at="2026-04-15T14:00:00+09:00", duration_min=60) - event creation',
    'play_music(playlist="focus_lofi", device="phone_speaker", shuffle=True) - start playlist playback',
    'track_package(carrier="CJ", tracking_number="1234567890", locale="ko-KR") - package status timeline',
    'show_recipe(dish="tofu_kimchi_stew", servings=2, difficulty="easy") - recipe detail card',
]

PLACEHOLDER_PARAM_NAMES = {
    "param",
    "params",
    "parameter",
    "parameters",
    "arg",
    "args",
    "input",
    "inputs",
    "data",
    "payload",
    "value",
    "values",
}

GENERIC_FUNCTION_NAMES = {
    "do_action",
    "run_task",
    "execute_task",
    "process_data",
    "handle_input",
    "perform_action",
}

MAX_DESCRIPTION_LENGTH = 80


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
2) Each line must use this format: function_name(param1=value1, param2=value2, ...) - short description
3) Use snake_case for function_name.
4) Fill parameters with realistic, scenario-specific values (avoid placeholder names like params, data, input).
5) Keep each description concise and concrete.
6) Provide at most {max_items} items.
7) Avoid duplicates in this response.

Examples:
{example_text}
"""


def sanitize_tool_call(text: str) -> str:
    return normalize_spaces(strip_list_prefix(text.strip()))


def normalize_tool_call_format(text: str) -> str:
    normalized = sanitize_tool_call(text)
    if not normalized:
        return normalized

    # minor auto-fixes: excessive spaces and duplicated separator hyphens
    normalized = re.sub(r"\s*\(\s*", "(", normalized, count=1)
    normalized = re.sub(r"\s*\)\s*", ")", normalized, count=1)
    normalized = re.sub(r"\)\s*--+\s*", ") - ", normalized, count=1)
    normalized = re.sub(r"\)\s*-\s*-\s*", ") - ", normalized, count=1)
    normalized = re.sub(r"\s*-\s*", " - ", normalized, count=1)
    return normalize_spaces(normalized)


def validate_tool_call_format(text: str) -> bool:
    if not text:
        return False

    # function_name(params) - description
    match = re.fullmatch(r"([a-z]+(?:_[a-z0-9]+)*)\((.*)\)\s*-\s*(.+)", text)
    if not match:
        return False

    params = (match.group(2) or "").strip()
    description = (match.group(3) or "").strip()
    if not params or not description:
        return False

    # reject unbalanced parentheses in parameter section
    depth = 0
    for ch in params:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return False
    if depth != 0:
        return False

    return True


def parse_tool_call_parts(text: str) -> tuple[str, str, str] | None:
    match = re.fullmatch(r"([a-z]+(?:_[a-z0-9]+)*)\((.*)\)\s*-\s*(.+)", text)
    if not match:
        return None
    return match.group(1), match.group(2).strip(), match.group(3).strip()


def extract_param_names(params: str) -> list[str]:
    return [m.group(1).lower() for m in re.finditer(r"(?:^|,)\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*=", params)]


def validate_tool_call_content(text: str) -> bool:
    parts = parse_tool_call_parts(text)
    if not parts:
        return False

    function_name, params, description = parts
    if function_name in GENERIC_FUNCTION_NAMES:
        return False

    if len(description) > MAX_DESCRIPTION_LENGTH:
        return False

    param_names = extract_param_names(params)
    if not param_names:
        return False
    if any(name in PLACEHOLDER_PARAM_NAMES for name in param_names):
        return False

    return True


def extract_tool_calls(text: str) -> list[str]:
    if not text:
        return []

    results: list[str] = []
    seen: set[str] = set()
    dropped_by_format = 0
    dropped_by_content = 0

    for raw in text.strip().splitlines():
        item = normalize_tool_call_format(raw)
        if not item:
            continue
        if not validate_tool_call_format(item):
            dropped_by_format += 1
            continue
        if not validate_tool_call_content(item):
            dropped_by_content += 1
            continue
        key = normalize_text(item)
        if key in seen:
            continue
        seen.add(key)
        results.append(item)

    if dropped_by_format:
        print(f"[WARN] Dropped {dropped_by_format} invalid tool call(s) due to format validation.")
    if dropped_by_content:
        print(f"[WARN] Dropped {dropped_by_content} invalid tool call(s) due to content validation.")

    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario-csv", default="mobile_widget_scenarios.csv")
    parser.add_argument("--tool-call-csv", default="mobile_widget_tool_calls.csv")
    add_openai_cli_args(parser, default_temperature=0.4)
    parser.add_argument("--max-items-per-scenario", type=int, default=3)
    parser.add_argument("--max-examples", type=int, default=10)
    parser.add_argument("--limit-scenarios", type=int, default=0)
    parser.add_argument("--max-concurrency", type=int, default=6)
    parser.add_argument("--flush-every", type=int, default=1)
    args = parser.parse_args()

    if args.max_items_per_scenario < 1:
        raise ValueError("--max-items-per-scenario must be >= 1")
    if args.max_concurrency < 1:
        raise ValueError("--max-concurrency must be >= 1")
    if args.flush_every < 1:
        raise ValueError("--flush-every must be >= 1")

    scenario_rows = load_scenarios(Path(args.scenario_csv))
    if args.limit_scenarios > 0:
        scenario_rows = scenario_rows[: args.limit_scenarios]

    if not scenario_rows:
        print("No scenarios found to process.")
        return

    examples = TOOL_CALL_EXAMPLES[: max(1, args.max_examples)]

    client = create_openai_client(args)

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

    total = len(scenario_rows)
    done = 0
    tool_call_csv_path = Path(args.tool_call_csv)
    file_exists = tool_call_csv_path.exists()
    write_mode = "a" if file_exists else "w"
    write_encoding = "utf-8" if file_exists else "utf-8-sig"

    written_rows = 0
    buffered_results: dict[tuple[int, int], dict[str, object]] = {}
    failed_results: set[tuple[int, int]] = set()
    next_expected = (1, 1)

    pending_since_flush = 0

    def flush_result(result: dict[str, object], writer: csv.DictWriter, output_file) -> int:
        nonlocal pending_since_flush
        row_index = int(result["row_index"])
        sample_index = int(result["sample_index"])
        row = result["row"]
        prompt = str(result["prompt"])
        items = result["items"]
        local_written = 0

        now = utc_now_iso()
        for item in items:
            writer.writerow(
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
            local_written += 1
            pending_since_flush += 1
            if pending_since_flush >= args.flush_every:
                output_file.flush()
                pending_since_flush = 0
        return local_written

    with tool_call_csv_path.open(write_mode, encoding=write_encoding, newline="") as f:
        writer = csv.DictWriter(f, fieldnames=TOOL_CALL_FIELDS)
        if not file_exists:
            writer.writeheader()
            f.flush()

        with concurrent.futures.ThreadPoolExecutor(max_workers=args.max_concurrency) as executor:
            future_to_row = {
                executor.submit(process_row, row_index, row): (row_index, row)
                for row_index, row in enumerate(scenario_rows, start=1)
            }

            for future in concurrent.futures.as_completed(future_to_row):
                done += 1
                row_index, row = future_to_row[future]
                try:
                    result = future.result()
                    buffered_results[(int(result["row_index"]), int(result["sample_index"]))] = result
                    print(f"[DONE] {done}/{total} row={row_index} {row['category']} | {row['scenario']}")
                except Exception as e:
                    failed_results.add((int(row_index), 1))
                    print(
                        f"[WARN] {done}/{total} row={row_index} {row['category']} | {row['scenario']} "
                        f"request failed after retries: {e}"
                    )

                while True:
                    if next_expected in failed_results:
                        failed_results.remove(next_expected)
                        next_expected = (next_expected[0] + 1, 1)
                        continue
                    if next_expected in buffered_results:
                        ordered_result = buffered_results.pop(next_expected)
                        written_rows += flush_result(ordered_result, writer, f)
                        next_expected = (next_expected[0] + 1, 1)
                        continue
                    break

        if pending_since_flush:
            f.flush()

    if not written_rows:
        print("No tool calls generated.")
        return
    print(f"Saved {written_rows} rows to {tool_call_csv_path}")


if __name__ == "__main__":
    main()
