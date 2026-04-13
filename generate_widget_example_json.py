#!/usr/bin/env python3
"""Generate stage-3 concrete widget example JSON rows from scenarios + tool calls.

Stage 3 helper script:
- Reads stage1 scenario CSV and stage2 tool-call CSV
- For each scenario, asks LLM for multiple concrete JSON examples
- Ensures each JSON object includes a `tool_calls` key
- Appends one CSV row per JSON example (for later stage-4 JSX/HTML generation)
"""

from __future__ import annotations

import argparse
import ast
import csv
import json
import random
import re
from dataclasses import dataclass
from pathlib import Path

from common.pipeline_runtime import add_openai_cli_args, create_openai_client, utc_now_iso
from common.openai_retry import create_completion_with_retry
from common.schemas import (
    STAGE1_REQUIRED_FIELDS,
    STAGE2_REQUIRED_FIELDS,
    STAGE3_FIELDS,
    ScenarioReferenceRow,
    build_scenario_fallback_key,
    build_scenario_join_key,
    ensure_required_columns,
)
from common.stage_executor import FlushWriter, run_ordered_stage

FEWSHOT_JSON_EXAMPLES: list[dict[str, object]] = [
    {
        "product_name": "ethiopian_drip_bag_coffee",
        "brand": "bean_lab",
        "price_krw": 12900,
        "discount_percent": 15,
        "delivery_eta": "2026-04-12",
        "tool_calls": [
            {"name": "search_products", "params": {"query": "drip bag coffee"}, "data": {}},
            {"name": "add_to_cart", "params": {"product_id": "bean_lab_db_01"}, "data": {}},
            {"name": "buy_now", "params": {"payment_method": "card"}, "data": {}},
        ],
    },
    {
        "product_name": "linen_oversized_shirt",
        "size": "M",
        "color": "ivory",
        "price_krw": 45900,
        "stock_status": "in_stock",
        "tool_calls": [
            {"name": "select_variant", "params": {"size": "M", "color": "ivory"}, "data": {}},
            {"name": "add_to_cart", "params": {"sku": "linen_shirt_ivory_m"}, "data": {}},
            {"name": "checkout", "params": {"coupon_code": "SPRING15"}, "data": {}},
        ],
    },
    {
        "hotel_name": "ulsan_river_hotel",
        "reservation_date": "2026-03-26T08:00:00+09:00",
        "room_number": "301",
        "reservation_status": "confirmed",
        "tool_calls": [
            {"name": "get_reservation_information", "params": {"reservation_id": "RSV-20260326-301"}, "data": {}},
            {"name": "cancel_reservation", "params": {"reason": "schedule_changed"}, "data": {}},
        ],
    },
    {
        "flight_number": "KE102",
        "departure_airport": "ICN",
        "departure_time": "2026-05-02T09:40:00+09:00",
        "gate": "A12",
        "boarding_status": "boarding_soon",
        "tool_calls": [
            {"name": "view_boarding_pass", "params": {"flight_number": "KE102"}, "data": {}},
            {"name": "check_flight_status", "params": {"flight_number": "KE102"}, "data": {}},
        ],
    },
    {
        "team": "lions_fc",
        "match_date": "2026-06-14",
        "opponent": "seoul_city_fc",
        "seat_section": "E2",
        "ticket_status": "paid",
        "tool_calls": [
            {"name": "view_ticket_qr", "params": {"ticket_id": "TKT-E2-441"}, "data": {}},
            {"name": "cancel_ticket", "params": {"ticket_id": "TKT-E2-441"}, "data": {}},
        ],
    },
    {
        "calendar_date": "2026-04-10",
        "events": [
            {"title": "design_sync", "time": "10:00"},
            {"title": "client_call", "time": "15:30"},
        ],
        "busy_slots": 2,
        "tool_calls": [
            {"name": "create_event", "params": {"title": "design_sync", "time": "10:00"}, "data": {}},
            {"name": "open_event_detail", "params": {"event_id": "evt_1530"}, "data": {}},
        ],
    },
    {
        "playlist_name": "focus_lofi_mix",
        "current_track": "night_rain_loop",
        "remaining_tracks": 12,
        "playback_mode": "shuffle",
        "tool_calls": [
            {"name": "play_music", "params": {"playlist_id": "focus_lofi_mix"}, "data": {}},
            {"name": "skip_track", "params": {}, "data": {}},
            {"name": "save_playlist", "params": {"playlist_id": "focus_lofi_mix"}, "data": {}},
        ],
    },
    {
        "recipe_name": "tofu_kimchi_stew",
        "servings": 2,
        "cook_time_min": 25,
        "missing_ingredients": ["tofu"],
        "tool_calls": [
            {"name": "show_recipe", "params": {"recipe_id": "tofu_kimchi_stew"}, "data": {}},
            {"name": "add_ingredients_to_cart", "params": {"items": ["tofu"]}, "data": {}},
        ],
    },
    {
        "workout_type": "interval_running",
        "target_duration_min": 30,
        "calorie_goal": 280,
        "progress_percent": 40,
        "tool_calls": [
            {"name": "start_workout", "params": {"workout_type": "interval_running"}, "data": {}},
            {"name": "pause_workout", "params": {"elapsed_min": 12}, "data": {}},
            {"name": "finish_workout", "params": {"elapsed_min": 30}, "data": {}},
        ],
    },
    {
        "package_id": "KR-1Z-88A2",
        "carrier": "cj_logistics",
        "status": "out_for_delivery",
        "estimated_arrival": "2026-04-09T19:00:00+09:00",
        "tool_calls": [
            {"name": "track_package", "params": {"package_id": "KR-1Z-88A2"}, "data": {}},
            {"name": "contact_courier", "params": {"carrier": "cj_logistics"}, "data": {}},
        ],
    },
]

DIFFICULTY_LEVELS = ["low", "medium", "high"]


def load_scenarios(csv_path: Path) -> list[ScenarioReferenceRow]:
    if not csv_path.exists():
        raise FileNotFoundError(f"Scenario CSV not found: {csv_path}")

    rows: list[ScenarioReferenceRow] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        ensure_required_columns(reader.fieldnames, STAGE1_REQUIRED_FIELDS, label="Scenario CSV")

        for row in reader:
            strict_key = build_scenario_join_key(row)
            scenario = strict_key[3]
            category = strict_key[2]
            if not scenario or not category:
                continue
            rows.append(
                {
                    "scenario_created_at": strict_key[0],
                    "scenario_model": strict_key[1],
                    "category": category,
                    "scenario": scenario,
                }
            )

    return rows


def load_tool_calls(csv_path: Path) -> dict[tuple[str, str, str, str], list[str]]:
    if not csv_path.exists():
        return {}

    by_strict_key: dict[tuple[str, str, str, str], list[str]] = {}
    fallback_by_scenario: dict[tuple[str, str], list[str]] = {}

    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        ensure_required_columns(reader.fieldnames, STAGE2_REQUIRED_FIELDS, label="Tool-call CSV")

        for row in reader:
            tool_call = (row.get("tool_call") or "").strip()
            if not tool_call:
                continue

            strict_key = build_scenario_join_key(row)
            by_strict_key.setdefault(strict_key, [])
            if tool_call not in by_strict_key[strict_key]:
                by_strict_key[strict_key].append(tool_call)

            fallback_key = build_scenario_fallback_key(row)
            fallback_by_scenario.setdefault(fallback_key, [])
            if tool_call not in fallback_by_scenario[fallback_key]:
                fallback_by_scenario[fallback_key].append(tool_call)

    resolved: dict[tuple[str, str, str, str], list[str]] = {}
    for key, items in by_strict_key.items():
        resolved[key] = items

    for fallback_key, items in fallback_by_scenario.items():
        synthetic_key = ("", "", *fallback_key)
        resolved.setdefault(synthetic_key, items)

    return resolved


def build_prompt(
    category: str,
    scenario: str,
    tool_calls: list[str],
    variants_per_scenario: int,
    fewshot_examples: list[dict[str, object]],
    difficulty_targets: list[str],
) -> str:
    tool_call_text = "\n".join(f"- {x}" for x in tool_calls) if tool_calls else "- (none)"
    fewshot_text = (
        "\n".join(f"- {json.dumps(item, ensure_ascii=False)}" for item in fewshot_examples)
        if fewshot_examples
        else "- (none)"
    )

    difficulty_target_text = "\n".join(
        f"- variant {i}: {level}"
        for i, level in enumerate(difficulty_targets, start=1)
    )

    return f"""You are generating concrete JSON data for a mobile widget dataset (stage 3).

Category: {category}
Scenario: {scenario}
Goal: Create realistic data objects that can be directly used to render UI in stage 4 (JSX/HTML).

Tool calls from stage 2:
{tool_call_text}

Reference JSON examples (style only, do not copy values as-is):
{fewshot_text}

Requirements:
1) Return ONLY a JSON array with exactly {variants_per_scenario} objects.
2) Every object must include "tool_calls" key with a JSON array of objects in this schema:
   [{{"name": "search_products", "params": {{}}, "data": {{}}}}]
3) If tool calls are given, map them into the tool_calls list and preserve the stage2 tool_call argument structure as much as possible.
4) If no tool call is needed, set "tool_calls": [].
5) Add concrete, user-facing fields relevant to the scenario (dates, names, numbers, status, prices, etc.).
6) Use realistic values and keep key names in snake_case.
7) Variants should describe the same core user case/entity, and differ mainly by information complexity.
   (e.g., same product/trip/order context with progressively richer fields and nesting)
8) Do not include markdown/code fences or explanation text.
9) Match the requested difficulty per variant index as closely as possible.

Target difficulty by variant index:
{difficulty_target_text}

Difficulty guide:
- low: relatively simple/flat schema, fewer fields and lighter detail.
- medium: moderate field count with limited nesting and richer detail.
- high: richer schema with deeper nesting and/or denser details.
"""


def parse_json_array(text: str) -> list[dict]:
    if not text.strip():
        return []

    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?", "", candidate).strip()
        candidate = re.sub(r"```$", "", candidate).strip()

    parsed = json.loads(candidate)
    if not isinstance(parsed, list):
        raise ValueError("Model output is not a JSON array")

    out: list[dict] = []
    for item in parsed:
        if isinstance(item, dict):
            out.append(item)
    return out


def extract_tool_call_name(tool_call: str) -> str:
    m = re.match(r"\s*([a-zA-Z0-9_]+)\s*\(", tool_call)
    if m:
        return m.group(1)
    raw = re.sub(r"\s*-.*$", "", tool_call).strip()
    raw = re.sub(r"[^a-zA-Z0-9_]+", "_", raw)
    raw = re.sub(r"_+", "_", raw).strip("_")
    return raw.lower()


def _ast_to_jsonable(node: ast.AST) -> object:
    try:
        return ast.literal_eval(node)
    except Exception:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            base = _ast_to_jsonable(node.value)
            if isinstance(base, str) and base:
                return f"{base}.{node.attr}"
            return node.attr
        if isinstance(node, ast.Call):
            func_name = extract_tool_call_name(ast.unparse(node))
            return {"call": func_name}
        return ast.unparse(node)


def parse_tool_call_object(tool_call: str) -> dict[str, object] | None:
    raw = (tool_call or "").strip()
    if not raw:
        return None

    fallback_name = extract_tool_call_name(raw)
    if not fallback_name:
        return None

    try:
        expr = ast.parse(raw, mode="eval").body
    except SyntaxError:
        return {"name": fallback_name, "params": {}, "data": {"raw": raw, "parse_error": "syntax_error"}}

    if not isinstance(expr, ast.Call):
        return {"name": fallback_name, "params": {}, "data": {"raw": raw, "parse_error": "not_call_expr"}}

    func_name = fallback_name
    if isinstance(expr.func, ast.Name):
        func_name = expr.func.id.lower()
    elif isinstance(expr.func, ast.Attribute):
        func_name = expr.func.attr.lower()

    params: dict[str, object] = {}
    if expr.args:
        params["_args"] = [_ast_to_jsonable(arg) for arg in expr.args]
    for kw in expr.keywords:
        if kw.arg:
            params[kw.arg] = _ast_to_jsonable(kw.value)

    return {"name": func_name, "params": params, "data": {"raw": raw}}


def ensure_tool_calls(obj: dict, fallback_tool_calls: list[dict[str, object]]) -> dict:
    updated = dict(obj)
    tool_calls = updated.get("tool_calls")
    if not isinstance(tool_calls, list):
        tool_calls = []

    cleaned_tool_calls: list[dict[str, object]] = []
    for item in tool_calls:
        normalized: dict[str, object] | None = None
        if isinstance(item, str):
            normalized = parse_tool_call_object(item)
        elif isinstance(item, dict):
            name = item.get("name")
            if not isinstance(name, str) or not name.strip():
                name = extract_tool_call_name(str(item.get("tool_call") or item.get("raw") or ""))
            if isinstance(name, str) and name.strip():
                params = item.get("params")
                data = item.get("data")
                normalized = {
                    "name": name.strip().lower(),
                    "params": params if isinstance(params, dict) else {},
                    "data": data if isinstance(data, dict) else {},
                }

        if normalized:
            cleaned_tool_calls.append(normalized)

    if not cleaned_tool_calls and fallback_tool_calls:
        cleaned_tool_calls = [dict(x) for x in fallback_tool_calls]

    updated["tool_calls"] = cleaned_tool_calls
    return updated


def has_tool_call_overlap(declared_tool_call_names: list[str], json_tool_calls: list[object]) -> bool:
    """Check whether stage2 declared tool calls overlap with stage3 JSON tool_calls."""
    declared = {
        x.strip().lower()
        for x in declared_tool_call_names
        if isinstance(x, str) and x.strip()
    }
    if not declared:
        return True

    generated: set[str] = set()
    for x in json_tool_calls:
        if isinstance(x, str) and x.strip():
            generated.add(x.strip().lower())
        elif isinstance(x, dict):
            name = x.get("name")
            if isinstance(name, str) and name.strip():
                generated.add(name.strip().lower())
    return not declared.isdisjoint(generated)


def _inspect_json(value: object, depth: int = 1) -> dict[str, int]:
    """Return simple structure stats used for difficulty estimation."""
    stats = {
        "max_depth": depth,
        "object_nodes": 0,
        "array_nodes": 0,
        "leaf_nodes": 0,
        "non_tool_call_keys": 0,
        "array_items": 0,
        "string_chars": 0,
    }
    if isinstance(value, dict):
        stats["object_nodes"] += 1
        for key, child in value.items():
            if key != "tool_calls":
                stats["non_tool_call_keys"] += 1
            child_stats = _inspect_json(child, depth + 1)
            for k, v in child_stats.items():
                if k == "max_depth":
                    stats[k] = max(stats[k], v)
                else:
                    stats[k] += v
        return stats

    if isinstance(value, list):
        stats["array_nodes"] += 1
        stats["array_items"] += len(value)
        for child in value:
            child_stats = _inspect_json(child, depth + 1)
            for k, v in child_stats.items():
                if k == "max_depth":
                    stats[k] = max(stats[k], v)
                else:
                    stats[k] += v
        return stats

    stats["leaf_nodes"] += 1
    if isinstance(value, str):
        stats["string_chars"] += len(value)
    return stats


def estimate_difficulty(
    scenario: str,
    tool_calls: list[str],
    tool_call_names: list[str],
    json_obj: dict,
) -> str:
    """Estimate per-variant generation difficulty as low/medium/high + score."""
    unique_tool_call_names = sorted(set(x for x in tool_call_names if x))
    tool_call_count = len(unique_tool_call_names)
    scenario_words = len(re.findall(r"[a-zA-Z0-9가-힣_]+", scenario))

    stats = _inspect_json(json_obj)
    structural_raw = (
        stats["object_nodes"]
        + stats["array_nodes"]
        + (stats["max_depth"] * 2)
        + min(stats["array_items"], 20)
    )

    tool_call_score = min(tool_call_count, 8) / 8 * 35
    field_score = min(stats["non_tool_call_keys"], 14) / 14 * 25
    structure_score = min(structural_raw, 26) / 26 * 20
    payload_score = min(stats["string_chars"], 260) / 260 * 10
    scenario_score = min(scenario_words, 18) / 18 * 10

    # Penalize when stage2 had many raw tool calls but extraction collapsed heavily.
    # (signals noisy or inconsistent tool-call specification)
    raw_tool_call_count = len([x for x in tool_calls if x.strip()])
    ambiguity_bonus = min(max(raw_tool_call_count - tool_call_count, 0), 4) * 1.25

    total_score = round(min(tool_call_score + field_score + structure_score + payload_score + scenario_score + ambiguity_bonus, 100))
    if total_score < 34:
        level = "low"
    elif total_score < 67:
        level = "medium"
    else:
        level = "high"
    return f"{level}:{total_score}"


def build_difficulty_targets(
    variants_per_scenario: int,
    strategy: str,
    rand: random.Random,
    fixed_level: str,
) -> list[str]:
    """Build per-variant target difficulty levels."""
    if strategy == "fixed":
        return [fixed_level for _ in range(variants_per_scenario)]

    if strategy == "random":
        return [rand.choice(DIFFICULTY_LEVELS) for _ in range(variants_per_scenario)]

    # default: rotate (low -> medium -> high, repeat)
    return [
        DIFFICULTY_LEVELS[i % len(DIFFICULTY_LEVELS)]
        for i in range(variants_per_scenario)
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario-csv", default="mobile_widget_scenarios.csv")
    parser.add_argument("--tool-call-csv", default="mobile_widget_tool_calls.csv")
    parser.add_argument("--json-csv", default="mobile_widget_example_json.csv")
    add_openai_cli_args(parser, default_temperature=0.5)
    parser.add_argument("--variants-per-scenario", type=int, default=3)
    parser.add_argument("--max-examples", type=int, default=3)
    parser.add_argument("--example-seed", type=int, default=42)
    parser.add_argument(
        "--difficulty-strategy",
        choices=["rotate", "random", "fixed"],
        default="rotate",
        help="How to assign per-variant difficulty targets (default: rotate).",
    )
    parser.add_argument(
        "--difficulty-fixed-level",
        choices=DIFFICULTY_LEVELS,
        default="medium",
        help="Used only when --difficulty-strategy=fixed.",
    )
    parser.add_argument(
        "--difficulty-seed",
        type=int,
        default=42,
        help="Random seed used when --difficulty-strategy=random.",
    )
    parser.add_argument("--limit-scenarios", type=int, default=0)
    parser.add_argument("--max-concurrency", type=int, default=6)
    parser.add_argument("--flush-every", type=int, default=1)
    parser.add_argument(
        "--tool-call-overlap-filter",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "When enabled, drop variants whose JSON tool_calls have zero overlap "
            "with stage2-declared tool call names."
        ),
    )
    args = parser.parse_args()

    if args.variants_per_scenario < 1:
        raise ValueError("--variants-per-scenario must be >= 1")
    if args.max_examples < 0:
        raise ValueError("--max-examples must be >= 0")
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

    tool_call_map = load_tool_calls(Path(args.tool_call_csv))
    client = create_openai_client(args)
    rand = random.Random(args.example_seed)
    difficulty_rand = random.Random(args.difficulty_seed)

    @dataclass(frozen=True)
    class ExampleJsonTask:
        row_index: int
        sample_index: int
        scenario_row: ScenarioReferenceRow

    @dataclass(frozen=True)
    class ExampleJsonResult:
        row_index: int
        sample_index: int
        scenario_row: ScenarioReferenceRow
        prompt: str
        tool_calls: list[str]
        parsed_tool_calls: list[dict[str, object]]
        tool_call_names: list[str]
        difficulty_targets: list[str]
        variants: list[dict]

    tasks: list[ExampleJsonTask] = [
        ExampleJsonTask(row_index=row_index, sample_index=1, scenario_row=row)
        for row_index, row in enumerate(scenario_rows, start=1)
    ]

    def process_row(task: ExampleJsonTask) -> ExampleJsonResult:
        row_index = task.row_index
        row = task.scenario_row
        strict_key = (
            row["scenario_created_at"],
            row["scenario_model"],
            row["category"],
            row["scenario"],
        )
        fallback_key = ("", "", *(build_scenario_fallback_key(row)))
        tool_calls = tool_call_map.get(strict_key) or tool_call_map.get(fallback_key) or []
        parsed_tool_calls = [x for x in (parse_tool_call_object(raw) for raw in tool_calls) if x]
        extracted_tool_call_names = [str(item["name"]) for item in parsed_tool_calls if item.get("name")]
        tool_call_names = [name for name in extracted_tool_call_names if name]
        difficulty_targets = build_difficulty_targets(
            variants_per_scenario=args.variants_per_scenario,
            strategy=args.difficulty_strategy,
            rand=difficulty_rand,
            fixed_level=args.difficulty_fixed_level,
        )
        prompt_examples_count = min(args.max_examples, len(FEWSHOT_JSON_EXAMPLES))
        prompt_examples = (
            rand.sample(FEWSHOT_JSON_EXAMPLES, k=prompt_examples_count)
            if prompt_examples_count > 0
            else []
        )

        prompt = build_prompt(
            category=row["category"],
            scenario=row["scenario"],
            tool_calls=tool_calls,
            variants_per_scenario=args.variants_per_scenario,
            fewshot_examples=prompt_examples,
            difficulty_targets=difficulty_targets,
        )

        completion = create_completion_with_retry(
            client,
            model=args.model,
            temperature=args.temperature,
            messages=[
                {
                    "role": "system",
                    "content": "You output strict JSON arrays for dataset generation.",
                },
                {"role": "user", "content": prompt},
            ],
        )

        output_text = completion.choices[0].message.content or ""
        variants = parse_json_array(output_text)
        return ExampleJsonResult(
            row_index=row_index,
            sample_index=task.sample_index,
            scenario_row=row,
            prompt=prompt,
            tool_calls=tool_calls,
            parsed_tool_calls=parsed_tool_calls,
            tool_call_names=tool_call_names,
            difficulty_targets=difficulty_targets,
            variants=variants[: args.variants_per_scenario],
        )

    dropped_no_overlap = 0
    out_path = Path(args.json_csv)
    file_exists = out_path.exists()
    write_mode = "a" if file_exists else "w"
    write_encoding = "utf-8" if file_exists else "utf-8-sig"

    def flush_result(result: ExampleJsonResult, flush_writer: FlushWriter) -> int:
        nonlocal dropped_no_overlap
        row_index = result.row_index
        sample_index = result.sample_index
        row = result.scenario_row
        prompt = result.prompt
        tool_calls = result.tool_calls
        parsed_tool_calls = result.parsed_tool_calls
        tool_call_names = result.tool_call_names
        difficulty_targets = result.difficulty_targets
        variants = result.variants
        local_written = 0

        now = utc_now_iso()
        for variant_index, obj in enumerate(variants, start=1):
            ensured = ensure_tool_calls(obj, parsed_tool_calls)
            if args.tool_call_overlap_filter and not has_tool_call_overlap(
                tool_call_names, ensured.get("tool_calls", [])
            ):
                dropped_no_overlap += 1
                continue
            target_level = difficulty_targets[variant_index - 1]
            difficulty = estimate_difficulty(
                scenario=row["scenario"],
                tool_calls=tool_calls,
                tool_call_names=tool_call_names,
                json_obj=ensured,
            )
            flush_writer.writerow(
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
                    "tool_calls": json.dumps(tool_calls, ensure_ascii=False),
                    "variant_index": str(variant_index),
                    "difficulty_target": target_level,
                    "difficulty": difficulty,
                    "example_json": json.dumps(ensured, ensure_ascii=False),
                }
            )
            local_written += 1
        return local_written

    def done_log(done: int, total_tasks: int, task: ExampleJsonTask, _: ExampleJsonResult) -> str:
        row_index = task.row_index
        row = task.scenario_row
        return f"[DONE] {done}/{total_tasks} row={row_index} {row['category']} | {row['scenario']}"

    def warn_log(done: int, total_tasks: int, task: ExampleJsonTask, exc: Exception) -> str:
        row_index = task.row_index
        row = task.scenario_row
        return (
            f"[WARN] {done}/{total_tasks} row={row_index} {row['category']} | {row['scenario']} "
            f"request failed after retries or parse failed: {exc}"
        )

    with out_path.open(write_mode, encoding=write_encoding, newline="") as f:
        writer = csv.DictWriter(f, fieldnames=STAGE3_FIELDS)
        if not file_exists:
            writer.writeheader()
            f.flush()

        summary = run_ordered_stage(
            tasks=tasks,
            process_task=process_row,
            task_key=lambda task: (task.row_index, task.sample_index),
            result_key=lambda result: (result.row_index, result.sample_index),
            flush_result=flush_result,
            max_concurrency=args.max_concurrency,
            writer=writer,
            output_file=f,
            flush_every=args.flush_every,
            done_log=done_log,
            warn_log=warn_log,
        )

    if not summary.written_rows:
        print("No example JSON rows generated.")
        return

    print(f"Saved {summary.written_rows} rows to {out_path}")
    if args.tool_call_overlap_filter:
        print(f"Dropped {dropped_no_overlap} rows by tool-call overlap filter")


if __name__ == "__main__":
    main()
