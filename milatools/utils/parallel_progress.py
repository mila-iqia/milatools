from __future__ import annotations

import asyncio
import functools
from logging import getLogger as get_logger
from typing import (
    Coroutine,
    Protocol,
    TypedDict,
    TypeVar,
)

from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from typing_extensions import NotRequired

from milatools.cli import console

logger = get_logger(__name__)
OutT_co = TypeVar("OutT_co", covariant=True)


class ProgressDict(TypedDict):
    progress: int
    total: int
    info: NotRequired[str]


class ReportProgressFn(Protocol):
    """A function to be called inside a task to show information in the progress bar."""

    def __call__(self, progress: int, total: int, info: str | None = None) -> None:
        ...  # pragma: no cover


def report_progress(
    progress: int,
    total: int,
    info: str | None = None,
    *,
    task_id: TaskID,
    progress_dict: dict[TaskID, ProgressDict],
):
    if info is not None:
        progress_dict[task_id] = {"progress": progress, "total": total, "info": info}
    else:
        progress_dict[task_id] = {"progress": progress, "total": total}


class AsyncTaskFn(Protocol[OutT_co]):
    """Protocol for a function that can be run in parallel and reports its progress.

    The function can (should) periodically report info about it's progress by calling
    the `report_progress` function. For example:
    """

    def __call__(
        self, report_progress: ReportProgressFn
    ) -> Coroutine[None, None, OutT_co]:
        ...  # pragma: no cover


async def run_async_tasks_with_progress_bar(
    async_task_fns: list[AsyncTaskFn[OutT_co]],
    task_descriptions: list[str] | None = None,
    overall_progress_task_description: str = "[green]All jobs progress:",
    _show_elapsed_time: bool = True,
) -> list[OutT_co]:
    """Run a sequence of async tasks in "parallel" and display a progress bar.

    Adapted from the example at:

    https://www.deanmontgomery.com/2022/03/24/rich-progress-and-multiprocessing/

    NOTE: This differs from the usual progress bar: the results are returned as a list
    (all at the same time) instead of one at a time.

    >>> import pytest, sys
    >>> if sys.platform.startswith('win'):
    ...     pytest.skip("This doctest doesn't work properly on Windows.")
    >>> async def example_task_fn(report_progress: ReportProgressFn, len_of_task: int):
    ...     import random
    ...     report_progress(progress=0, total=len_of_task, info="Starting.")
    ...     for n in range(len_of_task):
    ...         await asyncio.sleep(1)  # sleep for a bit to simulate work
    ...         report_progress(progress=n + 1, total=len_of_task, info="working...")
    ...     report_progress(progress=len_of_task, total=len_of_task, info="Done.")
    ...     return f"Done after {len_of_task} seconds."
    >>> import functools
    >>> tasks = [functools.partial(example_task_fn, len_of_task=i) for i in range(1, 4)]
    >>> import time
    >>> start_time = time.time()
    >>> results = asyncio.run(run_async_tasks_with_progress_bar(tasks, _show_elapsed_time=False))
    ✓ All jobs progress: 6/6 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 100% 0:00:00
    ✓ Task 0 - Done.     1/1 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 100% 0:00:00
    ✓ Task 1 - Done.     2/2 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 100% 0:00:00
    ✓ Task 2 - Done.     3/3 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 100% 0:00:00
    >>> results
    ['Done after 1 seconds.', 'Done after 2 seconds.', 'Done after 3 seconds.']
    >>> f"Finished all tasks in {round(time.time() - start_time)} seconds."
    'Finished all tasks in 3 seconds.'
    """
    if task_descriptions is None:
        task_descriptions = [f"Task {i}" for i in range(len(async_task_fns))]
    columns = [
        SpinnerColumn(finished_text="[green]✓"),
        TextColumn("[progress.description]{task.description}"),
        MofNCompleteColumn(),
        BarColumn(bar_width=None),
        TaskProgressColumn(),
        *([TimeElapsedColumn()] if _show_elapsed_time else []),
        TimeRemainingColumn(),
    ]
    progress = Progress(
        *columns,
        console=console,
        transient=False,
        refresh_per_second=10,
        expand=False,
    )

    _progress_dict: dict[TaskID, ProgressDict] = {}
    tasks: dict[TaskID, asyncio.Task[OutT_co]] = {}

    overall_progress_task = progress.add_task(
        overall_progress_task_description,
        visible=True,
        start=True,
    )
    # iterate over the jobs we need to run
    for task_description, async_task_fn in zip(task_descriptions, async_task_fns):
        # NOTE: Could set visible=false so we don't have a lot of bars all at once.
        task_id = progress.add_task(
            description=task_description,
            visible=False,
            start=False,
        )
        report_progress_fn = functools.partial(
            report_progress, task_id=task_id, progress_dict=_progress_dict
        )
        coroutine = async_task_fn(report_progress=report_progress_fn)

        tasks[task_id] = asyncio.create_task(coroutine, name=task_description)

    update_pbar_task = asyncio.create_task(
        update_progress_bar(
            progress,
            tasks=tasks,
            task_descriptions=task_descriptions,
            progress_dict=_progress_dict,
            overall_progress_task=overall_progress_task,
        ),
        name=update_progress_bar.__name__,
    )
    try:
        with progress:
            await asyncio.gather(
                *[*tasks.values(), update_pbar_task], return_exceptions=True
            )
    except (KeyboardInterrupt, asyncio.CancelledError) as err:
        logger.warning(f"Received {type(err).__name__}, cancelling tasks.")
        for task in tasks.values():
            task.cancel()
        update_pbar_task.cancel()
        raise

    return [task.result() for task in tasks.values()]


async def update_progress_bar(
    progress: Progress,
    tasks: dict[TaskID, asyncio.Task[OutT_co]],
    progress_dict: dict[TaskID, ProgressDict],
    task_descriptions: list[str],
    overall_progress_task: TaskID,
):
    assert len(task_descriptions) == len(tasks)
    _started_task_ids: list[TaskID] = []
    while True:
        total_progress = 0
        total_task_lengths = 0

        for (task_id, task), task_description in zip(tasks.items(), task_descriptions):
            if task_id not in progress_dict:
                # No progress reported yet by the task function.
                continue

            update_data = progress_dict[task_id]
            task_progress = update_data["progress"]
            task_total = update_data["total"]

            # Start the task in the progress bar when the first update is received.
            # This allows us to have a nice per-task elapsed time instead of the
            # same elapsed time in all tasks.
            if task_id not in _started_task_ids and task_progress > 0:
                # Note: calling `start_task` multiple times doesn't cause issues,
                # but we're still doing this just to be explicit.
                progress.start_task(task_id)
                _started_task_ids.append(task_id)

            progress.update(
                task_id=task_id,
                completed=task_progress,
                total=task_total,
                description=task_description
                + (
                    " - Done."
                    if task.done()
                    else (f" - {info}" if (info := update_data.get("info")) else "")
                ),
                visible=True,
            )

            total_progress += task_progress
            total_task_lengths += task_total

        if total_progress or total_task_lengths:
            progress.update(
                task_id=overall_progress_task,
                completed=total_progress,
                total=total_task_lengths,
                visible=True,
            )

        if all(task.done() for task in tasks.values()):
            break

        await asyncio.sleep(0.10)
