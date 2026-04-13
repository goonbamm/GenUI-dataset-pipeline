"""Shared stage contract and runner for dataset pipeline scripts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Generic, Sequence, TypeVar

from common.stage_executor import StageRunSummary, run_ordered_stage

TTask = TypeVar("TTask")
TResult = TypeVar("TResult")
TKey = TypeVar("TKey", bound=object)


@dataclass(frozen=True)
class StageSpec(Generic[TTask, TResult, TKey]):
    """Configuration bundle for executing one stage with shared ordered runner."""

    tasks: Sequence[TTask]
    process_task: Callable[[TTask], TResult]
    task_key: Callable[[TTask], TKey]
    result_key: Callable[[TResult], TKey]
    flush_result: Callable[[TResult, object], int]
    max_concurrency: int
    writer: object
    output_file: object
    flush_every: int
    done_log: Callable[[int, int, TTask, TResult], str] | None = None
    warn_log: Callable[[int, int, TTask, Exception], str] | None = None


def run_stage(spec: StageSpec[TTask, TResult, TKey]) -> StageRunSummary:
    """Run one stage using the common ordered executor."""

    return run_ordered_stage(
        tasks=spec.tasks,
        process_task=spec.process_task,
        task_key=spec.task_key,
        result_key=spec.result_key,
        flush_result=spec.flush_result,
        max_concurrency=spec.max_concurrency,
        writer=spec.writer,
        output_file=spec.output_file,
        flush_every=spec.flush_every,
        done_log=spec.done_log,
        warn_log=spec.warn_log,
    )
