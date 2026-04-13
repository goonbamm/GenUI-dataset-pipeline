"""Shared CSV schemas and key contracts for GenUI dataset pipeline stages."""

from __future__ import annotations

from typing import Mapping, TypedDict

SCENARIO_JOIN_KEY_FIELDS = (
    "scenario_created_at",
    "scenario_model",
    "category",
    "scenario",
)
SCENARIO_FALLBACK_KEY_FIELDS = ("category", "scenario")


class Stage1ScenarioCsvRow(TypedDict):
    created_at: str
    model: str
    prompt: str
    category: str
    scenario: str


class ScenarioReferenceRow(TypedDict):
    scenario_created_at: str
    scenario_model: str
    category: str
    scenario: str


class Stage2ToolCallCsvRow(TypedDict):
    created_at: str
    model: str
    row_index: str
    sample_index: str
    scenario_created_at: str
    scenario_model: str
    category: str
    scenario: str
    prompt: str
    tool_call: str


class Stage3ExampleJsonCsvRow(TypedDict):
    created_at: str
    model: str
    row_index: str
    sample_index: str
    scenario_created_at: str
    scenario_model: str
    category: str
    scenario: str
    prompt: str
    # JSON-serialized array of tool objects.
    # Contract example:
    # [
    #   {"name": "search_products", "params": {"query": "wireless earbuds"}},
    #   {"name": "add_to_cart", "data": {"product_id": "sku_123", "quantity": 1}}
    # ]
    tool_calls: str
    variant_index: str
    difficulty_target: str
    difficulty: str
    example_json: str


class Stage4TsxCsvRow(TypedDict):
    created_at: str
    model: str
    row_index: str
    json_created_at: str
    json_model: str
    scenario_created_at: str
    scenario_model: str
    category: str
    scenario: str
    json_variant_index: str
    json_difficulty_target: str
    json_difficulty: str
    sample_index: str
    prompt: str
    example_json: str
    # Carries forward Stage3 contract where `example_json.tool_calls` is a
    # JSON array of tool objects (including `params`/`data` payloads).
    tsx_code: str
    format_ok: str
    uses_declared_tool_calls: str


STAGE1_FIELDS = ["created_at", "model", "prompt", "category", "scenario"]
STAGE1_REQUIRED_FIELDS = ["created_at", "model", "category", "scenario"]

STAGE2_FIELDS = [
    "created_at",
    "model",
    "row_index",
    "sample_index",
    *SCENARIO_JOIN_KEY_FIELDS,
    "prompt",
    "tool_call",
]
STAGE2_REQUIRED_FIELDS = [*SCENARIO_JOIN_KEY_FIELDS, "tool_call"]

STAGE3_FIELDS = [
    "created_at",
    "model",
    "row_index",
    "sample_index",
    *SCENARIO_JOIN_KEY_FIELDS,
    "prompt",
    "tool_calls",
    "variant_index",
    "difficulty_target",
    "difficulty",
    "example_json",
]
STAGE3_REQUIRED_FIELDS = [
    "created_at",
    "model",
    *SCENARIO_JOIN_KEY_FIELDS,
    "variant_index",
    "difficulty_target",
    "difficulty",
    "example_json",
]

STAGE4_FIELDS = [
    "created_at",
    "model",
    "row_index",
    "json_created_at",
    "json_model",
    *SCENARIO_JOIN_KEY_FIELDS,
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


def ensure_required_columns(headers: list[str] | None, required: list[str], *, label: str) -> None:
    found = headers or []
    missing = [col for col in required if col not in found]
    if missing:
        raise ValueError(f"{label} is missing required columns {missing}. Found columns: {found}")


def build_scenario_join_key(row: Mapping[str, str]) -> tuple[str, str, str, str]:
    return tuple((row.get(field) or "").strip() for field in SCENARIO_JOIN_KEY_FIELDS)  # type: ignore[return-value]


def build_scenario_fallback_key(row: Mapping[str, str]) -> tuple[str, str]:
    return tuple((row.get(field) or "").strip() for field in SCENARIO_FALLBACK_KEY_FIELDS)  # type: ignore[return-value]
