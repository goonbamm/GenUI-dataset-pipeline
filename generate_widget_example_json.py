#!/usr/bin/env python3
"""Generate stage-3 concrete widget example JSON rows from scenarios + action items.

Stage 3 helper script:
- Reads stage1 scenario CSV and stage2 action-item CSV
- For each scenario, asks LLM for multiple concrete JSON examples
- Ensures each JSON object includes an `actions` key
- Appends one CSV row per JSON example (for later stage-4 JSX/HTML generation)
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import random
import re
from pathlib import Path

from openai import OpenAI

SCENARIO_REQUIRED_FIELDS = ["created_at", "model", "category", "scenario"]
ACTION_REQUIRED_FIELDS = ["scenario_created_at", "scenario_model", "category", "scenario", "action_item"]

EXAMPLE_JSON_FIELDS = [
    "created_at",
    "model",
    "scenario_created_at",
    "scenario_model",
    "category",
    "scenario",
    "prompt",
    "action_items",
    "variant_index",
    "difficulty_target",
    "difficulty",
    "example_json",
]

FEWSHOT_JSON_EXAMPLES: list[dict[str, object]] = [
    {
        "product_name": "ethiopian_drip_bag_coffee",
        "brand": "bean_lab",
        "price_krw": 12900,
        "discount_percent": 15,
        "delivery_eta": "2026-04-12",
        "actions": ["search_products", "add_to_cart", "buy_now"],
    },
    {
        "product_name": "linen_oversized_shirt",
        "size": "M",
        "color": "ivory",
        "price_krw": 45900,
        "stock_status": "in_stock",
        "actions": ["select_variant", "add_to_cart", "checkout"],
    },
    {
        "hotel_name": "ulsan_river_hotel",
        "reservation_date": "2026-03-26T08:00:00+09:00",
        "room_number": "301",
        "reservation_status": "confirmed",
        "actions": ["get_reservation_information", "cancel_reservation"],
    },
    {
        "flight_number": "KE102",
        "departure_airport": "ICN",
        "departure_time": "2026-05-02T09:40:00+09:00",
        "gate": "A12",
        "boarding_status": "boarding_soon",
        "actions": ["view_boarding_pass", "check_flight_status"],
    },
    {
        "team": "lions_fc",
        "match_date": "2026-06-14",
        "opponent": "seoul_city_fc",
        "seat_section": "E2",
        "ticket_status": "paid",
        "actions": ["view_ticket_qr", "cancel_ticket"],
    },
    {
        "calendar_date": "2026-04-10",
        "events": [
            {"title": "design_sync", "time": "10:00"},
            {"title": "client_call", "time": "15:30"},
        ],
        "busy_slots": 2,
        "actions": ["create_event", "open_event_detail"],
    },
    {
        "playlist_name": "focus_lofi_mix",
        "current_track": "night_rain_loop",
        "remaining_tracks": 12,
        "playback_mode": "shuffle",
        "actions": ["play_music", "skip_track", "save_playlist"],
    },
    {
        "recipe_name": "tofu_kimchi_stew",
        "servings": 2,
        "cook_time_min": 25,
        "missing_ingredients": ["tofu"],
        "actions": ["show_recipe", "add_ingredients_to_cart"],
    },
    {
        "workout_type": "interval_running",
        "target_duration_min": 30,
        "calorie_goal": 280,
        "progress_percent": 40,
        "actions": ["start_workout", "pause_workout", "finish_workout"],
    },
    {
        "package_id": "KR-1Z-88A2",
        "carrier": "cj_logistics",
        "status": "out_for_delivery",
        "estimated_arrival": "2026-04-09T19:00:00+09:00",
        "actions": ["track_package", "contact_courier"],
    },
]

DIFFICULTY_LEVELS = ["low", "medium", "high"]


def normalize_text(text: str) -> str:
    text = text.strip().lower()
    return re.sub(r"\s+", " ", text)


def load_scenarios(csv_path: Path) -> list[dict[str, str]]:
    if not csv_path.exists():
        raise FileNotFoundError(f"Scenario CSV not found: {csv_path}")

    rows: list[dict[str, str]] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        missing = [col for col in SCENARIO_REQUIRED_FIELDS if col not in headers]
        if missing:
            raise ValueError(
                f"Scenario CSV is missing required columns {missing}. "
                f"Found columns: {headers}"
            )

        for row in reader:
            scenario = (row.get("scenario") or "").strip()
            category = (row.get("category") or "").strip()
            if not scenario or not category:
                continue
            rows.append(
                {
                    "scenario_created_at": (row.get("created_at") or "").strip(),
                    "scenario_model": (row.get("model") or "").strip(),
                    "category": category,
                    "scenario": scenario,
                }
            )

    return rows


def load_action_items(csv_path: Path) -> dict[tuple[str, str, str, str], list[str]]:
    if not csv_path.exists():
        return {}

    by_strict_key: dict[tuple[str, str, str, str], list[str]] = {}
    fallback_by_scenario: dict[tuple[str, str], list[str]] = {}

    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        missing = [col for col in ACTION_REQUIRED_FIELDS if col not in headers]
        if missing:
            raise ValueError(
                f"Action CSV is missing required columns {missing}. "
                f"Found columns: {headers}"
            )

        for row in reader:
            action_item = (row.get("action_item") or "").strip()
            if not action_item:
                continue

            strict_key = (
                (row.get("scenario_created_at") or "").strip(),
                (row.get("scenario_model") or "").strip(),
                (row.get("category") or "").strip(),
                (row.get("scenario") or "").strip(),
            )
            by_strict_key.setdefault(strict_key, [])
            if action_item not in by_strict_key[strict_key]:
                by_strict_key[strict_key].append(action_item)

            fallback_key = (strict_key[2], strict_key[3])
            fallback_by_scenario.setdefault(fallback_key, [])
            if action_item not in fallback_by_scenario[fallback_key]:
                fallback_by_scenario[fallback_key].append(action_item)

    resolved: dict[tuple[str, str, str, str], list[str]] = {}
    for key, items in by_strict_key.items():
        resolved[key] = items

    for (category, scenario), items in fallback_by_scenario.items():
        synthetic_key = ("", "", category, scenario)
        resolved.setdefault(synthetic_key, items)

    return resolved


def build_prompt(
    category: str,
    scenario: str,
    action_items: list[str],
    variants_per_scenario: int,
    fewshot_examples: list[dict[str, object]],
    difficulty_targets: list[str],
) -> str:
    action_list_text = "\n".join(f"- {x}" for x in action_items) if action_items else "- (none)"
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

Action items from stage 2:
{action_list_text}

Reference JSON examples (style only, do not copy values as-is):
{fewshot_text}

Requirements:
1) Return ONLY a JSON array with exactly {variants_per_scenario} objects.
2) Every object must include "actions" key with a JSON array of snake_case action names.
3) If action items are given, map them into the actions list (function name only, no params/description).
4) If no action item is needed, set "actions": [].
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


def extract_action_name(action_item: str) -> str:
    m = re.match(r"\s*([a-zA-Z0-9_]+)\s*\(", action_item)
    if m:
        return m.group(1)
    raw = re.sub(r"\s*-.*$", "", action_item).strip()
    raw = re.sub(r"[^a-zA-Z0-9_]+", "_", raw)
    raw = re.sub(r"_+", "_", raw).strip("_")
    return raw.lower()


def ensure_actions(obj: dict, fallback_action_names: list[str]) -> dict:
    updated = dict(obj)
    actions = updated.get("actions")
    if not isinstance(actions, list):
        actions = []

    cleaned_actions: list[str] = []
    for item in actions:
        if isinstance(item, str) and item.strip():
            cleaned_actions.append(item.strip())

    if not cleaned_actions and fallback_action_names:
        cleaned_actions = fallback_action_names

    updated["actions"] = cleaned_actions
    return updated


def _inspect_json(value: object, depth: int = 1) -> dict[str, int]:
    """Return simple structure stats used for difficulty estimation."""
    stats = {
        "max_depth": depth,
        "object_nodes": 0,
        "array_nodes": 0,
        "leaf_nodes": 0,
        "non_action_keys": 0,
        "array_items": 0,
        "string_chars": 0,
    }
    if isinstance(value, dict):
        stats["object_nodes"] += 1
        for key, child in value.items():
            if key != "actions":
                stats["non_action_keys"] += 1
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
    action_items: list[str],
    action_names: list[str],
    json_obj: dict,
) -> str:
    """Estimate per-variant generation difficulty as low/medium/high + score."""
    unique_action_names = sorted(set(x for x in action_names if x))
    action_count = len(unique_action_names)
    scenario_words = len(re.findall(r"[a-zA-Z0-9가-힣_]+", scenario))

    stats = _inspect_json(json_obj)
    structural_raw = (
        stats["object_nodes"]
        + stats["array_nodes"]
        + (stats["max_depth"] * 2)
        + min(stats["array_items"], 20)
    )

    action_score = min(action_count, 8) / 8 * 35
    field_score = min(stats["non_action_keys"], 14) / 14 * 25
    structure_score = min(structural_raw, 26) / 26 * 20
    payload_score = min(stats["string_chars"], 260) / 260 * 10
    scenario_score = min(scenario_words, 18) / 18 * 10

    # Penalize when stage2 had many raw action items but extraction collapsed heavily.
    # (signals noisy or inconsistent action specification)
    raw_action_count = len([x for x in action_items if x.strip()])
    ambiguity_bonus = min(max(raw_action_count - action_count, 0), 4) * 1.25

    total_score = round(min(action_score + field_score + structure_score + payload_score + scenario_score + ambiguity_bonus, 100))
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
    parser.add_argument("--action-csv", default="mobile_widget_action_items.csv")
    parser.add_argument("--json-csv", default="mobile_widget_example_json.csv")
    parser.add_argument("--base-url", default=os.getenv("VLLM_BASE_URL", "http://localhost:8000/v1"))
    parser.add_argument("--api-key", default=os.getenv("VLLM_API_KEY", "EMPTY"))
    parser.add_argument("--model", default=os.getenv("VLLM_MODEL", "Qwen/Qwen2.5-7B-Instruct"))
    parser.add_argument("--temperature", type=float, default=0.5)
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
    args = parser.parse_args()

    if args.variants_per_scenario < 1:
        raise ValueError("--variants-per-scenario must be >= 1")
    if args.max_examples < 0:
        raise ValueError("--max-examples must be >= 0")

    scenario_rows = load_scenarios(Path(args.scenario_csv))
    if args.limit_scenarios > 0:
        scenario_rows = scenario_rows[: args.limit_scenarios]

    if not scenario_rows:
        print("No scenarios found to process.")
        return

    action_map = load_action_items(Path(args.action_csv))
    client = OpenAI(base_url=args.base_url, api_key=args.api_key)
    rand = random.Random(args.example_seed)
    difficulty_rand = random.Random(args.difficulty_seed)

    rows_to_append: list[dict[str, str]] = []

    for idx, row in enumerate(scenario_rows, start=1):
        strict_key = (
            row["scenario_created_at"],
            row["scenario_model"],
            row["category"],
            row["scenario"],
        )
        fallback_key = ("", "", row["category"], row["scenario"])
        action_items = action_map.get(strict_key) or action_map.get(fallback_key) or []
        action_names = [extract_action_name(x) for x in action_items if extract_action_name(x)]
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
            action_items=action_items,
            variants_per_scenario=args.variants_per_scenario,
            fewshot_examples=prompt_examples,
            difficulty_targets=difficulty_targets,
        )

        completion = client.chat.completions.create(
            model=args.model,
            n=1,
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

        try:
            variants = parse_json_array(output_text)
        except Exception as e:
            print(
                f"[WARN] {idx}/{len(scenario_rows)} {row['category']} | {row['scenario']} "
                f"-> parse failed: {e}"
            )
            continue

        now = dt.datetime.now(dt.timezone.utc).isoformat()
        for variant_index, obj in enumerate(variants[: args.variants_per_scenario], start=1):
            ensured = ensure_actions(obj, action_names)
            target_level = difficulty_targets[variant_index - 1]
            difficulty = estimate_difficulty(
                scenario=row["scenario"],
                action_items=action_items,
                action_names=action_names,
                json_obj=ensured,
            )
            rows_to_append.append(
                {
                    "created_at": now,
                    "model": args.model,
                    "scenario_created_at": row["scenario_created_at"],
                    "scenario_model": row["scenario_model"],
                    "category": row["category"],
                    "scenario": row["scenario"],
                    "prompt": prompt,
                    "action_items": json.dumps(action_items, ensure_ascii=False),
                    "variant_index": str(variant_index),
                    "difficulty_target": target_level,
                    "difficulty": difficulty,
                    "example_json": json.dumps(ensured, ensure_ascii=False),
                }
            )

        print(
            f"[DONE] {idx}/{len(scenario_rows)} {row['category']} | {row['scenario']}: "
            f"{min(len(variants), args.variants_per_scenario)} variants"
        )

    if not rows_to_append:
        print("No example JSON rows generated.")
        return

    out_path = Path(args.json_csv)
    file_exists = out_path.exists()
    write_mode = "a" if file_exists else "w"
    write_encoding = "utf-8" if file_exists else "utf-8-sig"
    with out_path.open(write_mode, encoding=write_encoding, newline="") as f:
        writer = csv.DictWriter(f, fieldnames=EXAMPLE_JSON_FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows_to_append)

    print(f"Saved {len(rows_to_append)} rows to {out_path}")


if __name__ == "__main__":
    main()
