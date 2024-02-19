from __future__ import annotations

import multiprocessing
import time
from concurrent.futures import Future, ThreadPoolExecutor
from multiprocessing.managers import DictProxy
from typing import Callable, TypedDict

from rich.progress import (
    Progress,
    SpinnerColumn,
    TaskID,
    TimeElapsedColumn,
)

from milatools import console


class ProgressDict(TypedDict):
    progress: int
    total: int


def example_task_fn(progress_dict: DictProxy[TaskID, ProgressDict], task_id: TaskID):
    import random
    import time

    len_of_task = random.randint(3, 20)  # take some random length of time
    for n in range(0, len_of_task):
        time.sleep(1)  # sleep for a bit to simulate work
        progress_dict[task_id] = {"progress": n + 1, "total": len_of_task}


def parallel_progress_bar(
    task_fns: list[Callable[[DictProxy[TaskID, ProgressDict], TaskID], None]],
    task_descriptions: list[str] | None = None,
    overall_progress_task_description: str = "[green]All jobs progress:",
    n_workers: int = 8,  # set this to the number of cores you have on your machine
):
    """Adapted from https://www.deanmontgomery.com/2022/03/24/rich-progress-and-
    multiprocessing/

    TODO: make sure that all subprocesses are killed if the user CTRL+C's.
    """
    task_descriptions = task_descriptions or [f"Task {i}" for i in range(len(task_fns))]
    assert len(task_fns) == len(task_descriptions)

    with Progress(
        SpinnerColumn(),
        *Progress.get_default_columns(),
        TimeElapsedColumn(),
        console=console,
        transient=False,
        refresh_per_second=10,
    ) as progress:
        futures: list[Future[None]] = []  # keep track of the jobs
        with (
            multiprocessing.Manager() as manager,
            ThreadPoolExecutor(max_workers=n_workers) as executor,
        ):
            # this is the key - we share some state between our
            # main process and our worker functions
            _progress_dict: DictProxy[TaskID, ProgressDict] = manager.dict()
            overall_progress_task = progress.add_task(
                overall_progress_task_description,
                visible=False,
            )

            # iterate over the jobs we need to run
            for task_name, task_fn in zip(task_descriptions, task_fns):
                # set visible false so we don't have a lot of bars all at once:
                task_id = progress.add_task(description=task_name, visible=False)
                futures.append(executor.submit(task_fn, _progress_dict, task_id))

            # monitor the progress:
            while not all(future.done() for future in futures):
                total_progress = 0
                total_task_lengths = 0
                for task_id, update_data in _progress_dict.items():
                    task_progress = update_data["progress"]
                    total = update_data["total"]
                    # update the progress bar for this task:
                    progress.update(
                        task_id=task_id,
                        completed=task_progress,
                        total=total,
                        # visible=task_progress < total,
                        visible=True,
                    )
                    total_progress += task_progress
                    total_task_lengths += total

                progress.update(
                    overall_progress_task,
                    completed=total_progress,
                    total=total_task_lengths,
                    visible=total_task_lengths > 0,
                )
                time.sleep(0.01)

            # raise any errors:
            for future in futures:
                future.result()
