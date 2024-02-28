from __future__ import annotations

import functools
import time
from logging import getLogger as get_logger
from typing import Any

from pytest_regressions.file_regression import FileRegressionFixture

from milatools.cli import console
from milatools.cli.utils import removesuffix
from milatools.parallel_progress import (
    DictProxy,
    ProgressDict,
    TaskFn,
    TaskID,
    parallel_progress_bar,
)

logger = get_logger(__name__)


def _task_fn(
    progress_dict: DictProxy[TaskID, ProgressDict],
    task_id: TaskID,
    task_length: int,
    result: Any,
) -> Any:
    progress_dict[task_id] = {
        "progress": 0,
        "total": task_length,
        "info": "Starting task.",
    }

    for n in range(task_length):
        time.sleep(1.0)  # sleep for a bit to simulate work
        logger.debug(f"Task {task_id} is {n+1}/{task_length} done.")
        progress_dict[task_id] = {"progress": n + 1, "total": task_length}

    progress_dict[task_id] = {
        "progress": task_length,
        "total": task_length,
        "info": "Done.",
    }
    return result


def test_parallel_progress_bar(file_regression: FileRegressionFixture):
    num_tasks = 3
    task_lengths = [(i + 1) * 2 for i in range(num_tasks)]
    task_results = [i for i in range(num_tasks)]

    task_fns: list[TaskFn] = [
        functools.partial(_task_fn, task_length=task_length, result=result)
        for task_length, result in zip(task_lengths, task_results)
    ]

    start_time = time.time()

    console.begin_capture()
    all_output = ""

    print("------ Before starting")
    num_results = 0
    for i, result in enumerate(parallel_progress_bar(task_fns, n_workers=num_tasks)):
        time_to_result_i = time.time() - start_time

        # It should take ~`task_lengths[i]` seconds to get result #i
        assert task_lengths[i] <= time_to_result_i <= task_lengths[i] + 0.5
        assert result is task_results[i]

        print(f"------- After receiving output #{i}", flush=True)
        # all_output += capsys.readouterr().out
        all_output += console.end_capture()
        console.begin_capture()
        num_results += 1
    assert num_results == num_tasks

    print("------ After the progress bar is done.", flush=True)

    # all_output += capsys.readouterr().out
    all_output += console.end_capture()
    # Remove the elapsed column since its values can vary a little bit, and we're
    # already checking the elapsed time for each result in the for-loop above.
    all_output_without_elapsed = "\n".join(
        removesuffix(line, last_part).rstrip()
        if (last_part := line.split()[-1]).count(":") == 2
        else line
        for line in all_output.splitlines()
    )

    file_regression.check(all_output_without_elapsed)

    total_time_seconds = time.time() - start_time
    # output = console.end_capture()
    # longest task was programmed to take a known amount of time to run, so the
    # overall progress bar should have taken a max of ~ `max(task_lengths)` seconds.
    longtest_task_length = max(task_lengths)
    assert longtest_task_length <= total_time_seconds <= longtest_task_length + 1
