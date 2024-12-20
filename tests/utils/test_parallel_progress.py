from __future__ import annotations

import asyncio
import functools
import time
from logging import getLogger as get_logger
from typing import TypeVar

import pytest
from pytest_regressions.file_regression import FileRegressionFixture

from milatools.cli import console
from milatools.utils.parallel_progress import (
    AsyncTaskFn,
    ReportProgressFn,
    run_async_tasks_with_progress_bar,
)

from ..cli.common import xfails_on_windows

logger = get_logger(__name__)

OutT = TypeVar("OutT")


async def _async_task_fn(
    report_progress: ReportProgressFn,
    task_id: int,
    task_length: int,
    result: OutT,
) -> OutT:
    report_progress(0, task_length, "Starting task.")

    for n in range(task_length):
        await asyncio.sleep(1.0)  # sleep for a bit to simulate work
        logger.debug(f"Task {task_id} is {n+1}/{task_length} done.")
        report_progress(n + 1, task_length)

    report_progress(task_length, task_length, "Done.")
    return result


@xfails_on_windows(
    raises=AssertionError,
    reason="Output is weird on windows? something to do with linebreaks perhaps.",
    strict=True,
)
@pytest.mark.asyncio
async def test_async_progress_bar(file_regression: FileRegressionFixture):
    num_tasks = 4
    task_length = 5
    task_lengths = [task_length for _ in range(num_tasks)]
    task_results = [i for i in range(num_tasks)]

    task_fns: list[AsyncTaskFn[int]] = [
        functools.partial(
            _async_task_fn, task_id=i, task_length=task_length, result=result
        )
        for i, (task_length, result) in enumerate(zip(task_lengths, task_results))
    ]

    start_time = time.time()
    with console.capture() as capture:
        # NOTE: the results are returned as a list (all at the same time).
        results = await run_async_tasks_with_progress_bar(
            task_fns, _show_elapsed_time=False
        )
    assert results == task_results

    all_output = capture.get()
    # Remove the elapsed column since its values can vary a little bit between runs.
    all_output_without_elapsed = "\n".join(
        line.removesuffix(last_part).rstrip()
        if (parts := line.split()) and (last_part := parts[-1]).count(":") == 2
        else line
        for line in all_output.splitlines()
    )

    file_regression.check(all_output_without_elapsed, encoding="utf-8")

    total_time_seconds = time.time() - start_time

    # All tasks sleep for `task_length` seconds, so the total time should still be
    # roughly `task_length` seconds.
    assert total_time_seconds < 2 * task_length


@pytest.mark.asyncio
async def test_interrupt_progress_bar():
    """Test the case where one of the tasks raises an exception."""
    num_tasks = 4
    task_length = 5
    task_lengths = [task_length for _ in range(num_tasks)]
    task_results = [i for i in range(num_tasks)]

    task_fns: list[AsyncTaskFn] = [
        functools.partial(
            _async_task_fn, task_id=i, task_length=task_length, result=result
        )
        for i, (task_length, result) in enumerate(zip(task_lengths, task_results))
    ]

    # todo: seems not possible to raise KeyboardInterrupt, it seems to mess with
    # pytest-asyncio. Would be good to test it though.
    exception_type = asyncio.CancelledError

    async def _task_that_raises_an_exception(
        report_progress: ReportProgressFn,
        task_length: int,
    ):
        report_progress(0, task_length, "Starting task.")
        # Raise an exception midway through the task.
        await asyncio.sleep(task_length / 2)
        report_progress(
            task_length // 2,
            task_length,
            f"Done sleeping, about to raise a {exception_type.__name__}.",
        )
        raise exception_type()

    task_that_raises_an_exception = functools.partial(
        _task_that_raises_an_exception, task_length=task_length
    )

    results = None
    with pytest.raises(exception_type):
        results = await run_async_tasks_with_progress_bar(
            task_fns + [task_that_raises_an_exception]
        )
    # Results was not set.
    assert results is None

    # Other test case: the interrupt is raised from "outside the progress bar".
    # Check that the "outside" task raising an exception doesn't cancel the tasks in
    # the "progress bar group".

    async def _raise_after(delay: int):
        await asyncio.sleep(delay)
        raise exception_type()

    results, exception = await asyncio.gather(
        run_async_tasks_with_progress_bar(task_fns),
        _raise_after(1),
        return_exceptions=True,
    )
    # The result from the progress bar should be there, and the exception from the other
    # task is also there.
    assert results == task_results
    assert isinstance(exception, exception_type)
