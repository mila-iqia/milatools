from __future__ import annotations

import multiprocessing
import time
from concurrent.futures import Future, ThreadPoolExecutor
from logging import getLogger as get_logger
from multiprocessing.managers import DictProxy
from typing import Iterable, Protocol, TypedDict, TypeVar

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


class TaskFn(Protocol[OutT_co]):
    """Protocol for a function that can be run in parallel and reports its progress.

    The function should periodically set a dict containing info about it's progress in
    the `progress_dict` at key `task_id`. For example:

    ```python
    def _example_task_fn(progress_dict: DictProxy[TaskID, ProgressDict], task_id: TaskID):
        import random
        import time
        progress_dict[task_id] = {"progress": 0, "total": len_of_task, "info": "Starting."}

        len_of_task = random.randint(3, 20)  # take some random length of time
        for n in range(len_of_task):
            time.sleep(1)  # sleep for a bit to simulate work
            progress_dict[task_id] = {"progress": n + 1, "total": len_of_task}

        progress_dict[task_id] = {"progress": len_of_task, "total": len_of_task, "info": "Done."}
        return f"Some result for task {task_id}."

    for result in parallel_progress_bar([_example_task_fn, _example_task_fn]):
        print(result)
    """

    def __call__(
        self, task_progress_dict: DictProxy[TaskID, ProgressDict], task_id: TaskID
    ) -> OutT_co:
        ...


def parallel_progress_bar(
    task_fns: list[TaskFn[OutT_co]],
    task_descriptions: list[str] | None = None,
    overall_progress_task_description: str = "[green]All jobs progress:",
    n_workers: int = 8,
) -> Iterable[OutT_co]:
    """Adapted from https://www.deanmontgomery.com/2022/03/24/rich-progress-and-
    multiprocessing/

    TODO: Double-check that using a ThreadPoolExecutor here actually makes sense and
    that the calls over SSH can be done in parallel.
    """
    if task_descriptions is None:
        task_descriptions = [f"Task {i}" for i in range(len(task_fns))]

    assert task_fns
    assert len(task_fns) == len(task_descriptions)

    futures: dict[TaskID, Future[OutT_co]] = {}
    num_yielded_results: int = 0

    # NOTE: Could also use a ProcessPoolExecutor here:
    # executor = ProcessPoolExecutor(max_workers=n_workers)
    executor = ThreadPoolExecutor(
        max_workers=n_workers, thread_name_prefix="mila_sync_worker"
    )
    manager = multiprocessing.Manager()
    progress = Progress(
        SpinnerColumn(finished_text="[green]✓"),
        TextColumn("[progress.description]{task.description}"),
        MofNCompleteColumn(),
        BarColumn(bar_width=None),
        TaskProgressColumn(),
        TimeRemainingColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=False,
        refresh_per_second=10,
        expand=False,
    )
    with executor, manager, progress:
        # We share some state between our main process and our worker
        # functions
        _progress_dict: DictProxy[TaskID, ProgressDict] = manager.dict()

        overall_progress_task = progress.add_task(
            overall_progress_task_description,
            visible=True,
            start=True,
        )

        # iterate over the jobs we need to run
        for task_name, task_fn in zip(task_descriptions, task_fns):
            # NOTE: Could set visible=false so we don't have a lot of bars all at once.
            task_id = progress.add_task(
                description=task_name, visible=True, start=False
            )
            futures[task_id] = executor.submit(task_fn, _progress_dict, task_id)

        _started_task_ids: list[TaskID] = []

        # monitor the progress:
        while num_yielded_results < len(futures):
            total_progress = 0
            total_task_lengths = 0

            for (task_id, future), task_description in zip(
                futures.items(), task_descriptions
            ):
                if task_id not in _progress_dict:
                    # No progress reported yet by the task function.
                    continue

                update_data = _progress_dict[task_id]
                task_progress = update_data["progress"]
                total = update_data["total"]

                # Start the task in the progress bar when the first update is received.
                # This allows us to have a nice per-task elapsed time instead of the
                # same elapsed time in all tasks.
                if task_id not in _started_task_ids and task_progress > 0:
                    # Note: calling `start_task` multiple times doesn't cause issues,
                    # but we're still doing this just to be explicit.
                    progress.start_task(task_id)
                    _started_task_ids.append(task_id)

                # Update the progress bar for this task:
                progress.update(
                    task_id=task_id,
                    completed=task_progress,
                    total=total,
                    description=task_description
                    + (f" - {info}" if (info := update_data.get("info")) else ""),
                    visible=True,
                )
                total_progress += task_progress
                total_task_lengths += total

            if total_progress or total_task_lengths:
                progress.update(
                    task_id=overall_progress_task,
                    completed=total_progress,
                    total=total_task_lengths,
                    visible=True,
                )

            next_task_id_to_yield, next_future_to_resolve = list(futures.items())[
                num_yielded_results
            ]
            if next_future_to_resolve.done():
                logger.debug(f"Task {next_task_id_to_yield} is done, yielding result.")
                yield next_future_to_resolve.result()
                num_yielded_results += 1

            try:
                time.sleep(0.01)
            except KeyboardInterrupt:
                logger.info(
                    "Received keyboard interrupt, cancelling tasks that haven't started yet."
                )
                for future in futures.values():
                    future.cancel()
                break
