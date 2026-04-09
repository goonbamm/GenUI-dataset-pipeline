#!/usr/bin/env python3
"""Generate stage-4 GenUI TSX snippets from stage-3 JSON examples.

Stage 4 helper script:
- Reads stage-3 JSON CSV (example_json column)
- Prompts vLLM model to produce minimal TSX (no external component dependency)
- Supports multi-sample generation by repeating the same prompt per input row
- Saves rows suitable for SFT and RLVR-style post-training (prompt + output + checks)
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
from pathlib import Path

from openai import OpenAI

JSON_REQUIRED_FIELDS = [
    "created_at",
    "model",
    "scenario_created_at",
    "scenario_model",
    "category",
    "scenario",
    "variant_index",
    "difficulty_target",
    "difficulty",
    "example_json",
]

TSX_FIELDS = [
    "created_at",
    "model",
    "json_created_at",
    "json_model",
    "scenario_created_at",
    "scenario_model",
    "category",
    "scenario",
    "json_variant_index",
    "json_difficulty_target",
    "json_difficulty",
    "sample_index",
    "prompt",
    "example_json",
    "tsx_code",
    "format_ok",
    "uses_declared_actions",
    "rlvr_reward_spec",
]


def load_json_rows(csv_path: Path) -> list[dict[str, str]]:
    if not csv_path.exists():
        raise FileNotFoundError(f"JSON CSV not found: {csv_path}")

    rows: list[dict[str, str]] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        missing = [col for col in JSON_REQUIRED_FIELDS if col not in headers]
        if missing:
            raise ValueError(
                f"JSON CSV is missing required columns {missing}. "
                f"Found columns: {headers}"
            )

        for row in reader:
            example_json = (row.get("example_json") or "").strip()
            if not example_json:
                continue
            rows.append({k: (row.get(k) or "").strip() for k in JSON_REQUIRED_FIELDS})

    return rows


def parse_json_obj(raw: str) -> dict[str, object]:
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("example_json must be a JSON object")
    return data


def parse_actions(example_obj: dict[str, object]) -> list[str]:
    actions = example_obj.get("actions", [])
    if not isinstance(actions, list):
        return []
    cleaned: list[str] = []
    for a in actions:
        if isinstance(a, str) and a.strip():
            cleaned.append(a.strip())
    return cleaned


def build_prompt(category: str, scenario: str, example_json: dict[str, object], actions: list[str]) -> str:
    action_text = ", ".join(actions) if actions else "(none)"
    json_text = json.dumps(example_json, ensure_ascii=False, indent=2)

    return f"""You are generating a Stage-4 GenUI TSX training target.

Category: {category}
Scenario: {scenario}
Input JSON:
{json_text}

Allowed actions: {action_text}

Requirements:
1) Return ONLY TSX code (no markdown fences, no explanation).
2) Write one default exported React function component.
3) Keep UI minimal and simple. Do not import or use external UI components.
4) Use plain semantic HTML tags (div, section, h1~h3, p, ul/li, button, etc.).
5) Render the key fields from Input JSON so the user can understand current state.
6) If actions exist, render action buttons in the same order. Use readable labels.
7) Avoid network calls and side effects; this is a static training target.
8) Keep code concise and deterministic.
"""


def strip_code_fences(text: str) -> str:
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:tsx|jsx|typescript|javascript)?", "", candidate).strip()
        candidate = re.sub(r"```$", "", candidate).strip()
    return candidate


def looks_like_tsx(text: str) -> bool:
    return "export default" in text and "return (" in text and "<" in text and ">" in text


def check_actions_used(tsx: str, actions: list[str]) -> bool:
    if not actions:
        return True

    lower = tsx.lower()
    for action in actions:
        label = action.replace("_", " ").lower()
        if label not in lower and action.lower() not in lower:
            return False
    return True


def build_rlvr_reward_spec() -> str:
    spec = {
        "checks": [
            {"name": "valid_tsx_shape", "rule": "contains export default + JSX return"},
            {"name": "no_markdown_fence", "rule": "must not contain triple backticks"},
            {"name": "minimal_html_only", "rule": "no external UI component imports"},
            {"name": "actions_covered", "rule": "declared actions should appear as labels/text"},
        ],
        "weights": {
            "valid_tsx_shape": 0.35,
            "no_markdown_fence": 0.15,
            "minimal_html_only": 0.2,
            "actions_covered": 0.3,
        },
    }
    return json.dumps(spec, ensure_ascii=False)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json-csv", default="mobile_widget_example_json.csv")
    parser.add_argument("--tsx-csv", default="mobile_widget_genui_tsx.csv")
    parser.add_argument("--base-url", default=os.getenv("VLLM_BASE_URL", "http://localhost:8000/v1"))
    parser.add_argument("--api-key", default=os.getenv("VLLM_API_KEY", "EMPTY"))
    parser.add_argument("--model", default=os.getenv("VLLM_MODEL", "Qwen/Qwen2.5-7B-Instruct"))
    parser.add_argument("--temperature", type=float, default=0.3)
    parser.add_argument("--samples-per-input", type=int, default=3)
    parser.add_argument("--limit-rows", type=int, default=0)
    args = parser.parse_args()

    if args.samples_per_input < 1:
        raise ValueError("--samples-per-input must be >= 1")

    json_rows = load_json_rows(Path(args.json_csv))
    if args.limit_rows > 0:
        json_rows = json_rows[: args.limit_rows]

    if not json_rows:
        print("No stage-3 JSON rows found to process.")
        return

    client = OpenAI(base_url=args.base_url, api_key=args.api_key)
    rlvr_reward_spec = build_rlvr_reward_spec()
    rows_to_append: list[dict[str, str]] = []

    total_calls = len(json_rows) * args.samples_per_input
    call_idx = 0

    for idx, row in enumerate(json_rows, start=1):
        try:
            json_obj = parse_json_obj(row["example_json"])
        except Exception as e:
            print(f"[WARN] {idx}/{len(json_rows)} parse example_json failed: {e}")
            continue

        actions = parse_actions(json_obj)
        prompt = build_prompt(
            category=row["category"],
            scenario=row["scenario"],
            example_json=json_obj,
            actions=actions,
        )

        for sample_index in range(1, args.samples_per_input + 1):
            call_idx += 1
            completion = client.chat.completions.create(
                model=args.model,
                n=1,
                temperature=args.temperature,
                messages=[
                    {
                        "role": "system",
                        "content": "You output raw TSX only for training datasets.",
                    },
                    {"role": "user", "content": prompt},
                ],
            )

            output_text = completion.choices[0].message.content or ""
            tsx_code = strip_code_fences(output_text)
            format_ok = looks_like_tsx(tsx_code)
            actions_ok = check_actions_used(tsx_code, actions)
            now = dt.datetime.now(dt.timezone.utc).isoformat()

            rows_to_append.append(
                {
                    "created_at": now,
                    "model": args.model,
                    "json_created_at": row["created_at"],
                    "json_model": row["model"],
                    "scenario_created_at": row["scenario_created_at"],
                    "scenario_model": row["scenario_model"],
                    "category": row["category"],
                    "scenario": row["scenario"],
                    "json_variant_index": row["variant_index"],
                    "json_difficulty_target": row["difficulty_target"],
                    "json_difficulty": row["difficulty"],
                    "sample_index": str(sample_index),
                    "prompt": prompt,
                    "example_json": row["example_json"],
                    "tsx_code": tsx_code,
                    "format_ok": "1" if format_ok else "0",
                    "uses_declared_actions": "1" if actions_ok else "0",
                    "rlvr_reward_spec": rlvr_reward_spec,
                }
            )

            print(
                f"[DONE] call {call_idx}/{total_calls} | row {idx}/{len(json_rows)} "
                f"sample {sample_index}/{args.samples_per_input}"
            )

    if not rows_to_append:
        print("No TSX rows generated.")
        return

    out_path = Path(args.tsx_csv)
    file_exists = out_path.exists()
    write_mode = "a" if file_exists else "w"
    write_encoding = "utf-8" if file_exists else "utf-8-sig"
    with out_path.open(write_mode, encoding=write_encoding, newline="") as f:
        writer = csv.DictWriter(f, fieldnames=TSX_FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows_to_append)

    print(f"Saved {len(rows_to_append)} rows to {out_path}")


if __name__ == "__main__":
    main()
