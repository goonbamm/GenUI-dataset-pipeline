#!/usr/bin/env python3
"""Generate stage-4 GenUI TSX snippets from stage-3 JSON examples.

Stage 4 helper script:
- Reads stage-3 JSON CSV (example_json column)
- Prompts vLLM model to produce minimal TSX (no external component dependency)
- Supports multi-sample generation by repeating the same prompt per input row
- Saves rows suitable for SFT-style post-training (prompt + output + checks)
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import datetime as dt
import json
import os
import re
import threading
import time
from pathlib import Path

from openai import OpenAI

try:
    import httpx
except Exception:  # pragma: no cover - optional optimization
    httpx = None

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
    "row_index",
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
    "uses_declared_tool_calls",
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


def parse_tool_calls(example_obj: dict[str, object]) -> list[str]:
    tool_calls = example_obj.get("tool_calls", [])
    if not isinstance(tool_calls, list):
        return []
    cleaned: list[str] = []
    for call in tool_calls:
        if isinstance(call, str) and call.strip():
            cleaned.append(call.strip())
    return cleaned


def build_prompt(category: str, scenario: str, example_json: dict[str, object], tool_calls: list[str]) -> str:
    tool_call_text = ", ".join(tool_calls) if tool_calls else "(none)"
    json_text = json.dumps(example_json, ensure_ascii=False, indent=2)

    return f"""You are generating a Stage-4 GenUI TSX training target.

Category: {category}
Scenario: {scenario}
Input JSON:
{json_text}

Declared tool calls: {tool_call_text}

Requirements:
1) Return ONLY TSX code (no markdown fences, no explanation).
2) Write one default exported React function component.
3) Keep UI minimal and simple. Do not import or use external UI components.
4) Use plain semantic HTML tags (div, section, h1~h3, p, ul/li, button, etc.).
5) Render the key fields from Input JSON so the user can understand current state.
6) If tool calls exist, render tool-call buttons in the same order. Use readable labels.
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


def check_tool_calls_used(tsx: str, tool_calls: list[str]) -> bool:
    if not tool_calls:
        return True

    lower = tsx.lower()
    for tool_call in tool_calls:
        label = tool_call.replace("_", " ").lower()
        if label not in lower and tool_call.lower() not in lower:
            return False
    return True


class UnsupportedNError(RuntimeError):
    """Raised when server does not support n>1 in chat.completions.create."""


def is_n_unsupported_error(error: Exception) -> bool:
    text = str(error).lower()
    patterns = [
        "does not support n",
        "unsupported value: 'n'",
        "unsupported parameter",
        "unexpected keyword argument 'n'",
        "n must be 1",
        "n is not supported",
        "only support n=1",
    ]
    return any(p in text for p in patterns)


def create_completion_with_retry(
    client: OpenAI,
    *,
    model: str,
    n: int,
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
                n=n,
                temperature=temperature,
                messages=messages,
            )
        except Exception as e:
            if n > 1 and is_n_unsupported_error(e):
                raise UnsupportedNError(str(e)) from e
            attempt += 1
            if attempt > max_retries:
                raise
            time.sleep(initial_backoff_sec * (2 ** (attempt - 1)))


def collect_outputs_from_completion(completion, expected_count: int) -> list[str]:
    outputs: list[str] = []
    for choice in completion.choices[:expected_count]:
        outputs.append(strip_code_fences(choice.message.content or ""))
    return outputs


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
    parser.add_argument("--max-concurrency", type=int, default=4)
    parser.add_argument("--http-max-connections", type=int, default=32)
    parser.add_argument("--http-max-keepalive-connections", type=int, default=16)
    parser.add_argument("--flush-every", type=int, default=1)
    parser.add_argument(
        "--filter-invalid",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "When enabled (default), keep only rows where format_ok=1 and "
            "uses_declared_tool_calls=1. "
            "Rows with empty tool_calls are still valid by design."
        ),
    )
    args = parser.parse_args()

    if args.samples_per_input < 1:
        raise ValueError("--samples-per-input must be >= 1")
    if args.max_concurrency < 1:
        raise ValueError("--max-concurrency must be >= 1")
    if args.http_max_connections < 1:
        raise ValueError("--http-max-connections must be >= 1")
    if args.http_max_keepalive_connections < 1:
        raise ValueError("--http-max-keepalive-connections must be >= 1")
    if args.flush_every < 1:
        raise ValueError("--flush-every must be >= 1")

    json_rows = load_json_rows(Path(args.json_csv))
    if args.limit_rows > 0:
        json_rows = json_rows[: args.limit_rows]

    if not json_rows:
        print("No stage-3 JSON rows found to process.")
        return

    thread_local = threading.local()

    def get_thread_client() -> OpenAI:
        client = getattr(thread_local, "client", None)
        if client is None:
            if httpx is None:
                client = OpenAI(base_url=args.base_url, api_key=args.api_key)
            else:
                http_client = httpx.Client(
                    limits=httpx.Limits(
                        max_connections=args.http_max_connections,
                        max_keepalive_connections=args.http_max_keepalive_connections,
                    )
                )
                client = OpenAI(
                    base_url=args.base_url,
                    api_key=args.api_key,
                    http_client=http_client,
                )
            thread_local.client = client
        return client
    tasks: list[dict[str, object]] = []

    for row_index, row in enumerate(json_rows, start=1):
        try:
            json_obj = parse_json_obj(row["example_json"])
        except Exception as e:
            print(f"[WARN] row {row_index}/{len(json_rows)} parse example_json failed: {e}")
            continue

        tool_calls = parse_tool_calls(json_obj)
        prompt = build_prompt(
            category=row["category"],
            scenario=row["scenario"],
            example_json=json_obj,
            tool_calls=tool_calls,
        )

        tasks.append(
            {
                "row_index": row_index,
                "row": row,
                "tool_calls": tool_calls,
                "prompt": prompt,
                "samples_per_input": args.samples_per_input,
            }
        )

    total_calls = len(tasks)

    def process_task(task: dict[str, object]) -> list[dict[str, object]]:
        messages = [
            {
                "role": "system",
                "content": "You output raw TSX only for training datasets.",
            },
            {"role": "user", "content": str(task["prompt"])},
        ]
        row_index = int(task["row_index"])
        samples_per_input = int(task["samples_per_input"])
        outputs: list[str] = []
        client = get_thread_client()

        if samples_per_input > 1:
            try:
                outputs.extend(
                    collect_outputs_from_completion(
                        create_completion_with_retry(
                            client,
                            model=args.model,
                            n=samples_per_input,
                            temperature=args.temperature,
                            messages=messages,
                        ),
                        samples_per_input,
                    )
                )
            except UnsupportedNError as e:
                print(
                    f"[INFO] row={row_index}/{len(json_rows)} n={samples_per_input} unsupported, "
                    f"fallback to n=1 repeated calls: {e}"
                )
                for _ in range(samples_per_input):
                    outputs.extend(
                        collect_outputs_from_completion(
                            create_completion_with_retry(
                                client,
                                model=args.model,
                                n=1,
                                temperature=args.temperature,
                                messages=messages,
                            ),
                            1,
                        )
                    )
        else:
            outputs.extend(
                collect_outputs_from_completion(
                    create_completion_with_retry(
                        client,
                        model=args.model,
                        n=1,
                        temperature=args.temperature,
                        messages=messages,
                    ),
                    1,
                )
            )

        if len(outputs) < samples_per_input:
            outputs.extend([""] * (samples_per_input - len(outputs)))

        tool_calls = task["tool_calls"]
        results: list[dict[str, object]] = []
        for choice_idx, tsx_code in enumerate(outputs[:samples_per_input], start=1):
            results.append(
                {
                    "row_index": row_index,
                    "sample_index": choice_idx,
                    "row": task["row"],
                    "tool_calls": tool_calls,
                    "prompt": task["prompt"],
                    "tsx_code": tsx_code,
                    "format_ok": looks_like_tsx(tsx_code),
                    "tool_calls_ok": check_tool_calls_used(tsx_code, tool_calls),
                }
            )
        return results

    done = 0
    filtered_out = 0
    out_path = Path(args.tsx_csv)
    file_exists = out_path.exists()
    write_mode = "a" if file_exists else "w"
    write_encoding = "utf-8" if file_exists else "utf-8-sig"
    written_rows = 0
    pending_since_flush = 0
    buffered_by_row: dict[int, list[dict[str, object]]] = {}
    failed_rows: set[int] = set()
    next_row_to_flush = 1

    def flush_row_results(results: list[dict[str, object]], writer: csv.DictWriter, output_file) -> int:
        nonlocal filtered_out, pending_since_flush
        local_written = 0
        for result in results:
            if args.filter_invalid and (not result["format_ok"] or not result["tool_calls_ok"]):
                filtered_out += 1
                continue
            row = result["row"]
            now = dt.datetime.now(dt.timezone.utc).isoformat()
            writer.writerow(
                {
                    "created_at": now,
                    "model": args.model,
                    "row_index": str(result["row_index"]),
                    "json_created_at": row["created_at"],
                    "json_model": row["model"],
                    "scenario_created_at": row["scenario_created_at"],
                    "scenario_model": row["scenario_model"],
                    "category": row["category"],
                    "scenario": row["scenario"],
                    "json_variant_index": row["variant_index"],
                    "json_difficulty_target": row["difficulty_target"],
                    "json_difficulty": row["difficulty"],
                    "sample_index": str(result["sample_index"]),
                    "prompt": result["prompt"],
                    "example_json": row["example_json"],
                    "tsx_code": result["tsx_code"],
                    "format_ok": "1" if result["format_ok"] else "0",
                    "uses_declared_tool_calls": "1" if result["tool_calls_ok"] else "0",
                }
            )
            local_written += 1
            pending_since_flush += 1
            if pending_since_flush >= args.flush_every:
                output_file.flush()
                pending_since_flush = 0
        return local_written

    with out_path.open(write_mode, encoding=write_encoding, newline="") as f:
        writer = csv.DictWriter(f, fieldnames=TSX_FIELDS)
        if not file_exists:
            writer.writeheader()
            f.flush()

        with concurrent.futures.ThreadPoolExecutor(max_workers=args.max_concurrency) as executor:
            future_to_task = {executor.submit(process_task, task): task for task in tasks}
            for future in concurrent.futures.as_completed(future_to_task):
                done += 1
                task = future_to_task[future]
                row_index = int(task["row_index"])
                try:
                    results = future.result()
                    buffered_by_row[row_index] = sorted(
                        results, key=lambda x: int(x["sample_index"])
                    )
                    for result in results:
                        print(
                            f"[DONE] request={done}/{total_calls} row={result['row_index']}/{len(json_rows)} "
                            f"choice={result['sample_index']}/{args.samples_per_input}"
                        )
                except Exception as e:
                    failed_rows.add(row_index)
                    print(
                        f"[WARN] request={done}/{total_calls} row={task['row_index']}/{len(json_rows)} "
                        f"request failed after retries: {e}"
                    )

                while True:
                    if next_row_to_flush in failed_rows:
                        failed_rows.remove(next_row_to_flush)
                        next_row_to_flush += 1
                        continue
                    if next_row_to_flush in buffered_by_row:
                        results_to_flush = buffered_by_row.pop(next_row_to_flush)
                        written_rows += flush_row_results(results_to_flush, writer, f)
                        next_row_to_flush += 1
                        continue
                    break

        if pending_since_flush:
            f.flush()

    if not written_rows:
        print("No TSX rows generated.")
        return
    if args.filter_invalid:
        print(f"Filtered out {filtered_out} invalid rows.")
    print(f"Saved {written_rows} rows to {out_path}")


if __name__ == "__main__":
    main()
