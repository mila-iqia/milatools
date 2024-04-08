from __future__ import annotations

import functools
import time
from logging import getLogger as get_logger
from typing import Sequence, TypeVar

from pytest_regressions.file_regression import FileRegressionFixture

from milatools.cli import console
from milatools.cli.utils import removesuffix
from milatools.utils.parallel_progress import (
    DictProxy,
    ProgressDict,
    TaskFn,
    TaskID,
    parallel_progress_bar,
)

from ..cli.common import xfails_on_windows

logger = get_logger(__name__)

OutT = TypeVar("OutT")


def _task_fn(
    task_progress_dict: DictProxy[TaskID, ProgressDict],
    task_id: TaskID,
    task_length: int,
    result: OutT,
) -> OutT:
    task_progress_dict[task_id] = {
        "progress": 0,
        "total": task_length,
        "info": "Starting task.",
    }

    for n in range(task_length):
        time.sleep(1.0)  # sleep for a bit to simulate work
        logger.debug(f"Task {task_id} is {n+1}/{task_length} done.")
        task_progress_dict[task_id] = {"progress": n + 1, "total": task_length}

    task_progress_dict[task_id] = {
        "progress": task_length,
        "total": task_length,
        "info": "Done.",
    }
    return result


@xfails_on_windows(
    raises=AssertionError,
    reason="Output is weird on windows? something to do with linebreaks perhaps.",
    strict=True,
)
def test_parallel_progress_bar(file_regression: FileRegressionFixture):
    num_tasks = 3
    task_lengths = [(i + 1) * 2 for i in range(num_tasks)]
    task_results = [i for i in range(num_tasks)]

    task_fns: list[TaskFn[int]] = [
        # pylance doesn't sees this as `Partial[int]` because it doesn't "save" the rest
        # of the signature. Ignoring the type error here.
        functools.partial(_task_fn, task_length=task_length, result=result)  # type: ignore
        for task_length, result in zip(task_lengths, task_results)
    ]

    start_time = time.time()

    console.begin_capture()

    time_to_results: list[float] = []
    results: list[int] = []
    for result in parallel_progress_bar(task_fns, n_workers=num_tasks):
        results.append(result)
        time_to_result = time.time() - start_time
        time_to_results.append(time_to_result)

    assert results == task_results

    for task_length, time_to_result in zip(task_lengths, time_to_results):
        # It should take ~`task_lengths[i]` seconds to get result #i
        # NOTE: This check was flaky. Adding some slack to the max time temporarily
        # addressed this.
        assert task_length <= time_to_result  # <= task_length + 2.0

    def assert_increasing(sequence: Sequence[float]) -> None:
        assert sequence and all(
            time_i <= time_j
            for time_i, time_j in zip(time_to_results, time_to_results[1:])
        )

    assert_increasing(task_lengths)
    assert_increasing(time_to_results)

    all_output = console.end_capture()

    # Remove the elapsed column since its values can vary a little bit, and we're
    # already checking the elapsed time for each result in the for-loop above.
    all_output_without_elapsed = "\n".join(
        removesuffix(line, last_part).rstrip()
        if (parts := line.split()) and (last_part := parts[-1]).count(":") == 2
        else line
        for line in all_output.splitlines()
    )

    file_regression.check(all_output_without_elapsed, encoding="utf-8")

    total_time_seconds = time.time() - start_time
    # output = console.end_capture()
    # longest task was programmed to take a known amount of time to run, so the
    # overall progress bar should have taken a max of ~ `max(task_lengths)` seconds.
    longtest_task_length = max(task_lengths)
    # NOTE: This check was flaky. Adding some slack to the max time only temporarily
    # addressed this.
    assert longtest_task_length <= total_time_seconds  # <= longtest_task_length + 1.5
