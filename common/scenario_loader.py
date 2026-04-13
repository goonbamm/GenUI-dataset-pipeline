"""Stage1 CSV canonical reader for scenario rows used by later stages."""

from __future__ import annotations

import csv
from pathlib import Path

from common.schemas import (
    STAGE1_REQUIRED_FIELDS,
    ScenarioReferenceRow,
    build_scenario_join_key,
    ensure_required_columns,
)


def load_stage1_scenarios(csv_path: Path, *, require_category: bool) -> list[ScenarioReferenceRow]:
    """Load Stage1 scenario CSV rows into the shared ScenarioReferenceRow contract.

    Args:
        csv_path: Path to stage1 CSV.
        require_category: If True, rows with empty category are dropped.
            If False, category may be empty but scenario is still required.
    """
    if not csv_path.exists():
        raise FileNotFoundError(f"Scenario CSV not found: {csv_path}")

    rows: list[ScenarioReferenceRow] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        ensure_required_columns(reader.fieldnames, STAGE1_REQUIRED_FIELDS, label="Scenario CSV")

        for row in reader:
            strict_key = build_scenario_join_key(row)
            category = strict_key[2]
            scenario = strict_key[3]
            if not scenario:
                continue
            if require_category and not category:
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
