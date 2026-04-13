#!/usr/bin/env python3
"""Run GenUI dataset stages in one orchestrated command."""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class StageCommand:
    index: int
    name: str
    cmd: list[str]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-stage", type=int, default=1)
    parser.add_argument("--to-stage", type=int, default=4)
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue running later stages even if a prior stage fails.",
    )
    args, passthrough = parser.parse_known_args()

    if args.from_stage < 1 or args.to_stage > 4 or args.from_stage > args.to_stage:
        raise ValueError("Require 1 <= --from-stage <= --to-stage <= 4")

    stages = [
        StageCommand(1, "stage1_scenarios", [sys.executable, "generate_mobile_widget_scenarios.py"]),
        StageCommand(2, "stage2_tool_calls", [sys.executable, "generate_widget_tool_calls.py"]),
        StageCommand(3, "stage3_example_json", [sys.executable, "generate_widget_example_json.py"]),
        StageCommand(4, "stage4_genui_tsx", [sys.executable, "generate_genui_tsx.py"]),
    ]

    selected = [s for s in stages if args.from_stage <= s.index <= args.to_stage]
    for stage in selected:
        print(f"[PIPELINE] running stage {stage.index}: {stage.name}")
        result = subprocess.run(stage.cmd + passthrough)
        if result.returncode == 0:
            print(f"[PIPELINE] stage {stage.index} completed")
            continue

        print(f"[PIPELINE] stage {stage.index} failed with exit code {result.returncode}")
        if not args.continue_on_error:
            raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
