"""Shared ordered stage execution helpers."""

from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass
from typing import Callable, Sequence, TypeVar

TTask = TypeVar("TTask")
TResult = TypeVar("TResult")
TKey = TypeVar("TKey", bound=object)


class FlushWriter:
    """CSV writer wrapper that handles periodic flush policy."""

    def __init__(self, writer, output_file, flush_every: int):
        self._writer = writer
        self._output_file = output_file
        self._flush_every = flush_every
        self._pending_since_flush = 0

    def writerow(self, row: dict[str, str]) -> None:
        self._writer.writerow(row)
        self._pending_since_flush += 1
        if self._pending_since_flush >= self._flush_every:
            self._output_file.flush()
            self._pending_since_flush = 0

    def finalize(self) -> None:
        if self._pending_since_flush:
            self._output_file.flush()
            self._pending_since_flush = 0


@dataclass
class StageRunSummary:
    total: int
    completed: int
    failed: int
    written_rows: int


def run_ordered_stage(
    *,
    tasks: Sequence[TTask],
    process_task: Callable[[TTask], TResult],
    task_key: Callable[[TTask], TKey],
    result_key: Callable[[TResult], TKey],
    flush_result: Callable[[TResult, FlushWriter], int],
    max_concurrency: int,
    writer,
    output_file,
    flush_every: int,
    done_log: Callable[[int, int, TTask, TResult], str] | None = None,
    warn_log: Callable[[int, int, TTask, Exception], str] | None = None,
) -> StageRunSummary:
    """Execute tasks concurrently and flush successful results in input-key order."""

    ordered_keys = [task_key(task) for task in tasks]
    key_to_position = {key: idx for idx, key in enumerate(ordered_keys)}
    if len(key_to_position) != len(ordered_keys):
        raise ValueError("task_key must be unique per task")

    buffered_results: dict[TKey, TResult] = {}
    failed_keys: set[TKey] = set()
    cursor = 0
    done = 0
    failed = 0
    written_rows = 0

    flush_writer = FlushWriter(writer=writer, output_file=output_file, flush_every=flush_every)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_concurrency) as executor:
        future_to_task = {executor.submit(process_task, task): task for task in tasks}

        for future in concurrent.futures.as_completed(future_to_task):
            done += 1
            task = future_to_task[future]
            key = task_key(task)
            try:
                result = future.result()
                result_k = result_key(result)
                if result_k not in key_to_position:
                    raise KeyError(f"Unknown result key: {result_k}")
                buffered_results[result_k] = result
                if done_log is not None:
                    print(done_log(done, len(tasks), task, result))
            except Exception as exc:
                failed += 1
                failed_keys.add(key)
                if warn_log is not None:
                    print(warn_log(done, len(tasks), task, exc))

            while cursor < len(ordered_keys):
                expected_key = ordered_keys[cursor]
                if expected_key in failed_keys:
                    failed_keys.remove(expected_key)
                    cursor += 1
                    continue
                if expected_key in buffered_results:
                    ordered_result = buffered_results.pop(expected_key)
                    written_rows += flush_result(ordered_result, flush_writer)
                    cursor += 1
                    continue
                break

    flush_writer.finalize()
    return StageRunSummary(total=len(tasks), completed=done, failed=failed, written_rows=written_rows)
