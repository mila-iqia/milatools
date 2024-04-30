from __future__ import annotations

import asyncio
import inspect
import re
import subprocess
from logging import getLogger as get_logger
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio

from milatools.cli.utils import removesuffix
from milatools.utils.compute_node import (
    ComputeNode,
    JobNotRunningError,
    get_queued_milatools_job_ids,
    salloc,
    sbatch,
)
from milatools.utils.remote_v2 import RemoteV2

from ..conftest import launches_jobs
from .runner_tests import RunnerTests
from .test_remote_v2 import uses_remote_v2

logger = get_logger(__name__)
pytestmark = [uses_remote_v2]


@launches_jobs
@pytest.mark.slow
@pytest.mark.asyncio
async def test_salloc(
    login_node_v2: RemoteV2,
    allocation_flags: list[str],
    job_name: str,
):
    if login_node_v2.hostname == "localhost":
        # todo: Check why this (and other tests in this file) don't work on the mock
        # slurm cluster during the CI.
        # - perhaps there is only one 'node' and so only one 'job' can run, and tests
        #   are actually running more than one job, so blocking each other?
        pytest.skip(reason="Test doesn't currently work on the mock slurm cluster.")

    compute_node = await salloc(login_node_v2, allocation_flags, job_name=job_name)

    assert isinstance(compute_node, ComputeNode)
    assert compute_node.hostname != login_node_v2.hostname

    # note: needs to be properly quoted so as not to evaluate the variable here!
    job_id = compute_node.get_output("echo $SLURM_JOB_ID")
    assert job_id.isdigit()
    assert compute_node.job_id == int(job_id)

    all_slurm_env_vars = {
        (split := line.split("="))[0]: split[1]
        for line in compute_node.get_output("env | grep SLURM").splitlines()
    }
    # NOTE: We actually do have all the other SLURM env variables here because we're
    # using `srun` with the job id on the login node to run our jobs.
    assert all_slurm_env_vars["SLURM_JOB_ID"] == str(compute_node.job_id)
    assert len(all_slurm_env_vars) > 1
    await compute_node.close_async()


@launches_jobs
@pytest.mark.slow
@pytest.mark.asyncio
async def test_sbatch(
    login_node_v2: RemoteV2,
    allocation_flags: list[str],
    job_name: str,
):
    if login_node_v2.hostname == "localhost":
        pytest.skip(reason="Test doesn't currently work on the mock slurm cluster.")

    compute_node = await sbatch(login_node_v2, allocation_flags, job_name=job_name)
    assert isinstance(compute_node, ComputeNode)

    assert compute_node.hostname != login_node_v2.hostname
    job_id = compute_node.get_output("echo $SLURM_JOB_ID")
    assert job_id.isdigit()
    assert compute_node.job_id == int(job_id)
    all_slurm_env_vars = {
        (split := line.split("="))[0]: split[1]
        for line in compute_node.get_output("env | grep SLURM").splitlines()
    }
    assert all_slurm_env_vars["SLURM_JOB_ID"] == str(compute_node.job_id)
    assert len(all_slurm_env_vars) > 1
    await compute_node.close_async()


@pytest.fixture(scope="session", params=[True, False], ids=["sbatch", "salloc"])
def persist(request: pytest.FixtureRequest):
    return request.param


@launches_jobs
@pytest.mark.slow
@pytest.mark.asyncio
async def test_interrupt_allocation(
    login_node_v2: RemoteV2,
    allocation_flags: list[str],
    job_name: str,
    persist: bool,
):
    """Test that checks that interrupting `salloc` or `sbatch` cancels the job
    allocation.

    TODO: Try to get better control over when the interrupt happens, for example:
    - while connecting via ssh;
    - while waiting for the job to show up in `sacct`;
    - while waiting for the job to start running.
    """
    if login_node_v2.hostname == "localhost":
        pytest.skip(reason="Test doesn't currently work on the mock slurm cluster.")

    async def get_jobs_in_squeue() -> set[int]:
        return await get_queued_milatools_job_ids(login_node_v2, job_name=job_name)

    _jobs_before = await get_jobs_in_squeue()

    async def get_new_job_ids() -> set[int]:
        """Retrieves the ID of the new jobs since we called `salloc` or `sbatch`."""
        new_job_ids: set[int] = set()
        queued_jobs = await get_jobs_in_squeue()
        new_job_ids = queued_jobs - _jobs_before

        while not new_job_ids:
            queued_jobs = await get_jobs_in_squeue()
            logger.info(f"{_jobs_before=}, {queued_jobs=}")
            new_job_ids = queued_jobs - _jobs_before
            if new_job_ids:
                break
            logger.info("Waiting for the job to show up in the output of `squeue`.")
            await asyncio.sleep(0.1)
        return new_job_ids

    # Check that a job allocation was indeed created.
    # NOTE: Assuming that it takes more time for the job to be allocated than it takes for
    # the job to show up in `squeue`.
    salloc_task = asyncio.create_task(
        sbatch(login_node_v2, sbatch_flags=allocation_flags, job_name=job_name)
        if persist
        else salloc(login_node_v2, salloc_flags=allocation_flags, job_name=job_name),
        name="sbatch" if persist else "salloc",
    )
    get_new_job_ids_task = asyncio.create_task(
        get_new_job_ids(), name="get_new_job_ids"
    )

    new_job_ids = await asyncio.wait_for(get_new_job_ids_task, timeout=None)
    assert not salloc_task.done()  # hopefully we get the job ID from SQUEUE before the
    # job is actually running...
    salloc_task.cancel(
        msg="Interrupting the job allocation as soon as the job ID shows up in squeue."
    )
    assert new_job_ids and len(new_job_ids) == 1
    new_job_id = new_job_ids.pop()
    # wait long enough for `squeue` to update and not show the job anymore.
    await asyncio.sleep(10)
    jobs_after = await get_jobs_in_squeue()
    assert new_job_id not in jobs_after
    assert jobs_after <= _jobs_before


@launches_jobs
@pytest.mark.slow
class TestComputeNode(RunnerTests):
    @pytest_asyncio.fixture(scope="class")
    async def runner(
        self, login_node_v2: RemoteV2, persist: bool, allocation_flags: list[str]
    ):
        if login_node_v2.hostname == "localhost":
            pytest.skip(reason="Test doesn't currently work on the mock slurm cluster.")

        if persist:
            runner = await sbatch(
                login_node_v2, sbatch_flags=allocation_flags, job_name="mila-code"
            )
        else:
            runner = await salloc(
                login_node_v2, salloc_flags=allocation_flags, job_name="mila-code"
            )
        yield runner
        await runner.close_async()

    @pytest.fixture(
        scope="class",
        params=[
            ("echo OK", "OK", ""),
            ("echo $SLURM_JOB_ID", re.compile(r"^[0-9]+"), ""),
            ("echo $SLURM_PROCID", "0", ""),
        ],
    )
    def command_and_expected_result(self, request: pytest.FixtureRequest):
        return request.param

    @pytest.mark.parametrize("use_async", [False, True], ids=["sync", "async"])
    @pytest.mark.asyncio
    async def test_run_gets_executed_in_job_step(
        self, runner: ComputeNode, use_async: bool
    ):
        command = "echo $SLURM_STEP_ID"
        output_a = (
            await runner.get_output_async(command)
            if use_async
            else runner.get_output(command)
        )
        output_b = (
            await runner.get_output_async(command)
            if use_async
            else runner.get_output(command)
        )
        job_step_a = int(output_a)
        job_step_b = int(output_b)
        assert job_step_a >= 0
        assert job_step_b == job_step_a + 1

    @pytest.mark.asyncio
    async def test_connect(self, runner: ComputeNode):
        login_node = runner.login_node
        job_id = runner.job_id
        node_hostname = runner.hostname
        compute_node_with_jobid = await ComputeNode.connect(
            login_node, job_id_or_node_name=job_id
        )
        assert compute_node_with_jobid.salloc_subprocess is None
        assert compute_node_with_jobid == runner

        # Need to connect with the node name, not the full node hostname.
        # For the `mila` cluster, we don't currently have a `cn-?????` entry in the ssh
        # config (although we could!)
        # Therefore, we need to connect to the node with the full hostname. However
        # squeue expects the node name, so we have to truncate it manually for now.
        if node_hostname.endswith(".server.mila.quebec"):
            node_name = removesuffix(node_hostname, ".server.mila.quebec")
        else:
            node_name = node_hostname

        compute_node_with_node_name = await ComputeNode.connect(
            login_node, job_id_or_node_name=node_name
        )
        assert compute_node_with_jobid.salloc_subprocess is None
        assert compute_node_with_node_name == runner

    @pytest.mark.parametrize("use_async", [False, True], ids=["sync", "async"])
    @pytest.mark.asyncio
    async def test_close(
        self,
        login_node_v2: RemoteV2,
        persist: bool,
        allocation_flags: list[str],
        job_name: str,
        use_async: bool,
    ):
        if login_node_v2.hostname == "localhost":
            pytest.skip(reason="Test doesn't currently work on the mock slurm cluster.")
        # Here we create a new job allocation just to cancel it. We could reuse the
        # `runner` fixture, but that would require us to run this test as the very last one.
        if persist:
            compute_node = await sbatch(
                login_node_v2, sbatch_flags=allocation_flags, job_name=job_name
            )
        else:
            compute_node = await salloc(
                login_node_v2, salloc_flags=allocation_flags, job_name=job_name
            )

        if use_async:
            await compute_node.close_async()
        else:
            compute_node.close()

        job_state = await login_node_v2.get_output_async(
            f"sacct --noheader --allocations --jobs {compute_node.job_id} --format=State%100",
            display=True,
            hide=False,
        )
        if persist:
            # batch jobs are scancelled.
            assert job_state.startswith("CANCELLED")
        else:
            # interactive jobs are exited cleanly.
            assert job_state == "COMPLETED"


@launches_jobs
@pytest.mark.slow
@pytest.mark.asyncio
async def test_del_computenode(
    login_node_v2: RemoteV2, persist: bool, allocation_flags: list[str], job_name: str
):
    """Test what happens when we delete a ComputeNode instance (persistent vs non-
    persistent).

    TODO: Perhaps we could use mocks here instead of allocating a job just to end it after.
    """
    if persist:
        compute_node = await sbatch(
            login_node_v2, sbatch_flags=allocation_flags, job_name=job_name
        )
    else:
        compute_node = await salloc(
            login_node_v2, salloc_flags=allocation_flags, job_name=job_name
        )

    job_id = compute_node.job_id
    del compute_node
    # if deleting does anything, wait for its effect to propagate to sacct
    await asyncio.sleep(5)
    state_after = await login_node_v2.get_output_async(
        f"sacct --jobs {job_id} --allocations --noheader --format=State",
    )
    try:
        if persist:
            assert state_after == "RUNNING"
        else:
            assert state_after == "COMPLETED"
    finally:
        await login_node_v2.run_async(f"scancel {job_id}")


@pytest_asyncio.fixture(params=[False, True], ids=["sync", "async"])
async def mock_closed_compute_node(
    request: pytest.FixtureRequest,
    ssh_config_file: Path,
):
    """Cheaply constructs a *closed* ComputeNode, without launching any jobs."""
    close_async: bool = request.param

    fake_job_id = 1234

    def _mock_run(command: str, *args, input: str | None = None, **kwargs):
        if input == "echo $SLURMD_NODENAME\n":
            return subprocess.CompletedProcess(command, 0, "cn-a001", "")
        if command == f"scancel {fake_job_id}":
            return subprocess.CompletedProcess(command, 0, "", "")
        # Unexpected command.
        assert False, (command, input)

    async def _mock_run_async(command: str, *args, input: str | None = None, **kwargs):
        if command == f"scancel {fake_job_id}":
            return subprocess.CompletedProcess(command, 0, "", "")
        # Unexpected command.
        assert False, (command, input)

    mock_run = Mock(spec=RemoteV2.run, side_effect=_mock_run)
    mock_run_async = AsyncMock(spec=RemoteV2.run_async, side_effect=_mock_run_async)
    mock_login_node = Mock(
        spec=RemoteV2,
        hostname="mila",
        ssh_config_path=ssh_config_file,
    )
    mock_login_node.configure_mock(
        run=mock_run,
        run_async=mock_run_async,
    )
    compute_node = ComputeNode(mock_login_node, job_id=1234)
    mock_run.assert_called()
    mock_run_async.assert_not_called()
    mock_run.reset_mock()

    if close_async:
        await compute_node.close_async()
        mock_run_async.assert_called_once()
        assert mock_run_async.mock_calls[0].args[0] == f"scancel {fake_job_id}"
    else:
        compute_node.close()
        # bug? this doesn't work but the output is identical?
        # mock_run.assert_called_once_with(f"scancel {fake_job_id}")
        mock_run.assert_called_once()
        assert mock_run.mock_calls[0].args[0] == f"scancel {fake_job_id}"
    mock_run.reset_mock()
    mock_run_async.reset_mock()
    return compute_node


@pytest.mark.asyncio
async def test_using_closed_compute_node_raises_error(
    mock_closed_compute_node: ComputeNode,
):
    compute_node = mock_closed_compute_node
    assert isinstance(compute_node.login_node, Mock)
    mock_run: Mock = mock_closed_compute_node.login_node.run  # type: ignore
    mock_run_async: Mock = compute_node.login_node.run_async  # type: ignore
    for method in ["run", "run_async", "get_output", "get_output_async"]:
        with pytest.raises(JobNotRunningError):
            output_or_coroutine = getattr(compute_node, method)("echo OK")
            # if we get here it means it's a coroutine, since we'd otherwise have raised
            # the error.
            assert inspect.iscoroutine(output_or_coroutine)
            await output_or_coroutine

        mock_run.assert_not_called()
        mock_run_async.assert_not_called()
