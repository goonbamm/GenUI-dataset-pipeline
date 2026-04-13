#!/usr/bin/env python3
"""Run GenUI dataset stages in one orchestrated command."""

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from dataclasses import dataclass


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
            print(f"[PIPELINE] stage {stage.index} args: {stage_args}")

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
