#!/usr/bin/env python3
"""Run GenUI dataset stages in one orchestrated command."""

from __future__ import annotations

import argparse
import math
import shlex
import subprocess
import sys
from dataclasses import dataclass

DEFAULT_CATEGORIES_COUNT = 11
DEFAULT_MAX_ITEMS_PER_SCENARIO = 3
DEFAULT_VARIANTS_PER_SCENARIO = 3
DEFAULT_SAMPLES_PER_INPUT = 3


@dataclass(frozen=True)
class StageCommand:
    index: int
    name: str
    cmd: list[str]


def _parse_stage_arg_strings(arg_strings: list[str] | None) -> list[str]:
    if not arg_strings:
        return []

    parsed: list[str] = []
    for arg_string in arg_strings:
        parsed.extend(shlex.split(arg_string))
    return parsed


def _read_int_flag(args: list[str], flag: str) -> int | None:
    for i, token in enumerate(args):
        if token == flag and i + 1 < len(args):
            return int(args[i + 1])
        if token.startswith(f"{flag}="):
            return int(token.split("=", 1)[1])
    return None


def _has_flag(args: list[str], flag: str) -> bool:
    return any(token == flag or token.startswith(f"{flag}=") for token in args)


def _read_categories_count(stage1_args: list[str]) -> int:
    for i, token in enumerate(stage1_args):
        if token == "--categories":
            count = 0
            for value in stage1_args[i + 1 :]:
                if value.startswith("--"):
                    break
                count += 1
            if count > 0:
                return count
        if token.startswith("--categories="):
            values = token.split("=", 1)[1].split(",")
            parsed = [v.strip() for v in values if v.strip()]
            if parsed:
                return len(parsed)
    return DEFAULT_CATEGORIES_COUNT


def _derive_stage_args_from_target_total(
    *,
    target_total: int,
    allocation_mode: str,
    stage_args_by_index: dict[int, list[str]],
) -> tuple[dict[int, list[str]], dict[str, int]]:
    if target_total <= 0:
        raise ValueError("--target-total must be a positive integer")
    if allocation_mode != "balanced":
        raise ValueError(f"Unsupported --allocation-mode: {allocation_mode} (supported: balanced)")

    stage1_args = stage_args_by_index[1]
    stage2_args = stage_args_by_index[2]
    stage3_args = stage_args_by_index[3]
    stage4_args = stage_args_by_index[4]

    categories = _read_categories_count(stage1_args)
    max_items_per_scenario = _read_int_flag(stage2_args, "--max-items-per-scenario") or DEFAULT_MAX_ITEMS_PER_SCENARIO
    variants_per_scenario = _read_int_flag(stage3_args, "--variants-per-scenario") or DEFAULT_VARIANTS_PER_SCENARIO
    samples_per_input = _read_int_flag(stage4_args, "--samples-per-input") or DEFAULT_SAMPLES_PER_INPUT

    s3_target = math.ceil(target_total / samples_per_input)
    s1_target = math.ceil(s3_target / variants_per_scenario)
    target_per_category = math.ceil(s1_target / categories)

    s1_total = categories * target_per_category
    s2_total = s1_total * max_items_per_scenario
    s3_total = s1_total * variants_per_scenario
    s4_total = s3_total * samples_per_input

    auto_args_by_stage = {
        1: ["--target-per-category", str(target_per_category)],
        2: ["--limit-scenarios", str(s1_total)],
        3: ["--variants-per-scenario", str(variants_per_scenario), "--limit-scenarios", str(s1_total)],
        4: ["--samples-per-input", str(samples_per_input)],
    }
    derived_summary = {
        "categories": categories,
        "max_items_per_scenario": max_items_per_scenario,
        "variants_per_scenario": variants_per_scenario,
        "samples_per_input": samples_per_input,
        "target_per_category": target_per_category,
        "s1_total": s1_total,
        "s2_total": s2_total,
        "s3_total": s3_total,
        "s4_total": s4_total,
    }
    return auto_args_by_stage, derived_summary


def _merge_stage_args_with_warnings(
    stage_args_by_index: dict[int, list[str]],
    auto_args_by_stage: dict[int, list[str]],
) -> tuple[dict[int, list[str]], dict[int, bool]]:
    merged: dict[int, list[str]] = {}
    had_auto_by_stage: dict[int, bool] = {}
    for stage_idx, explicit_args in stage_args_by_index.items():
        merged_args = list(explicit_args)
        auto_args = auto_args_by_stage.get(stage_idx, [])
        appended_auto = False
        i = 0
        while i < len(auto_args):
            flag = auto_args[i]
            value = auto_args[i + 1] if i + 1 < len(auto_args) else None
            if _has_flag(explicit_args, flag):
                print(
                    f"[PIPELINE][WARN] stage {stage_idx} explicit arg '{flag}' takes precedence "
                    f"over auto-allocation value '{value}'."
                )
            else:
                merged_args.extend([flag, value] if value is not None else [flag])
                appended_auto = True
            i += 2
        merged[stage_idx] = merged_args
        had_auto_by_stage[stage_idx] = appended_auto
    return merged, had_auto_by_stage


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run stage1~stage4 with shared pipeline options and stage-scoped argument channels."
        )
    )
    parser.add_argument("--from-stage", type=int, default=1)
    parser.add_argument("--to-stage", type=int, default=4)
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue running later stages even if a prior stage fails.",
    )
    parser.add_argument(
        "--target-total",
        type=int,
        default=None,
        help=(
            "Desired stage4 total sample count. When set, stage args are auto-derived "
            "with balanced allocation unless explicitly overridden in --stageN-args."
        ),
    )
    parser.add_argument(
        "--allocation-mode",
        default="balanced",
        help="Allocation mode for --target-total (currently only: balanced).",
    )
    parser.add_argument(
        "--stage1-args",
        action="append",
        metavar='"ARGS"',
        help='Stage 1 args as a quoted string. Example: --stage1-args "--limit 5 --seed 42"',
    )
    parser.add_argument(
        "--stage2-args",
        action="append",
        metavar='"ARGS"',
        help='Stage 2 args as a quoted string. Example: --stage2-args "--input data.jsonl"',
    )
    parser.add_argument(
        "--stage3-args",
        action="append",
        metavar='"ARGS"',
        help='Stage 3 args as a quoted string.',
    )
    parser.add_argument(
        "--stage4-args",
        action="append",
        metavar='"ARGS"',
        help='Stage 4 args as a quoted string.',
    )
    args, unknown = parser.parse_known_args()

    if unknown:
        parser.error(
            "Unrecognized pipeline argument(s): "
            f"{' '.join(unknown)}. "
            "Use --stageN-args for stage-specific flags."
        )

    if args.from_stage < 1 or args.to_stage > 4 or args.from_stage > args.to_stage:
        raise ValueError("Require 1 <= --from-stage <= --to-stage <= 4")

    stage_args_by_index = {
        1: _parse_stage_arg_strings(args.stage1_args),
        2: _parse_stage_arg_strings(args.stage2_args),
        3: _parse_stage_arg_strings(args.stage3_args),
        4: _parse_stage_arg_strings(args.stage4_args),
    }
    auto_used_by_stage = {1: False, 2: False, 3: False, 4: False}

    if args.target_total is not None:
        auto_args_by_stage, summary = _derive_stage_args_from_target_total(
            target_total=args.target_total,
            allocation_mode=args.allocation_mode,
            stage_args_by_index=stage_args_by_index,
        )
        stage_args_by_index, auto_used_by_stage = _merge_stage_args_with_warnings(
            stage_args_by_index, auto_args_by_stage
        )
        print(
            "[PIPELINE] auto allocation "
            f"(mode={args.allocation_mode}, target_total={args.target_total}) -> "
            f"S1={summary['s1_total']} S2={summary['s2_total']} "
            f"S3={summary['s3_total']} S4={summary['s4_total']} "
            f"(target_per_category={summary['target_per_category']}, "
            f"coeff: max_items={summary['max_items_per_scenario']}, "
            f"variants={summary['variants_per_scenario']}, samples={summary['samples_per_input']})"
        )

    stages = [
        StageCommand(1, "stage1_scenarios", [sys.executable, "generate_mobile_widget_scenarios.py"]),
        StageCommand(2, "stage2_tool_calls", [sys.executable, "generate_widget_tool_calls.py"]),
        StageCommand(3, "stage3_example_json", [sys.executable, "generate_widget_example_json.py"]),
        StageCommand(4, "stage4_genui_tsx", [sys.executable, "generate_genui_tsx.py"]),
    ]

    selected = [s for s in stages if args.from_stage <= s.index <= args.to_stage]
    for stage in selected:
        stage_args = stage_args_by_index[stage.index]
        cmd = stage.cmd + stage_args

        print(f"[PIPELINE] running stage {stage.index}: {stage.name}")
        if stage_args:
            source = "explicit+auto(target-total)" if auto_used_by_stage[stage.index] else "explicit"
            print(f"[PIPELINE] stage {stage.index} args [{source}]: {stage_args}")

        result = subprocess.run(cmd)
        if result.returncode == 0:
            print(f"[PIPELINE] stage {stage.index} completed")
            continue

        print(
            f"[PIPELINE] stage {stage.index} failed with exit code {result.returncode}. "
            f"problematic args: {stage_args if stage_args else 'none'}"
        )
        if not args.continue_on_error:
            raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
