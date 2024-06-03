from __future__ import annotations

import asyncio
import contextlib
import datetime
import re
import shutil
import sys
from datetime import timedelta
from logging import getLogger as get_logger
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio
from pytest_regressions.file_regression import FileRegressionFixture

from milatools.cli.code import code
from milatools.cli.commands import code_v1
from milatools.cli.utils import (
    CommandNotFoundError,
    MilatoolsUserError,
    get_hostname_to_use_for_compute_node,
    removesuffix,
)
from milatools.utils import disk_quota
from milatools.utils.compute_node import (
    ComputeNode,
    get_queued_milatools_job_ids,
    salloc,
)
from milatools.utils.disk_quota import check_disk_quota, check_disk_quota_v1
from milatools.utils.remote_v1 import RemoteV1
from milatools.utils.remote_v2 import RemoteV2

from ..conftest import job_name, launches_jobs
from .test_slurm_remote import get_recent_jobs_info_dicts

logger = get_logger(__name__)


async def _get_job_info(
    job_id: int,
    login_node: RemoteV2,
    fields: tuple[str, ...] = ("JobID", "JobName", "Node", "WorkDir", "State"),
) -> dict:
    return dict(
        zip(
            fields,
            (
                await login_node.get_output_async(
                    f"sacct --noheader --allocations --user=$USER --jobs {job_id} "
                    "--format=" + ",".join(f"{field}%40" for field in fields),
                    display=False,
                    hide=True,
                )
            )
            .strip()
            .split(),
        )
    )


@launches_jobs
@pytest.mark.slow
@pytest.mark.asyncio
@pytest.mark.parametrize("persist", [True, False], ids=["sbatch", "salloc"])
@pytest.mark.parametrize(
    job_name.__name__,
    # Don't set the `--job-name` in the `allocation_flags` fixture
    # (this is necessary for `mila code` to work properly).
    [None],
    ids=[""],
    indirect=True,
)
async def test_code(
    login_node_v2: RemoteV2,
    persist: bool,
    capsys: pytest.CaptureFixture,
    allocation_flags: list[str],
    file_regression: FileRegressionFixture,
    slurm_account_on_cluster: str,
):
    if login_node_v2.hostname == "localhost":
        pytest.skip(
            "TODO: This test doesn't yet work with the slurm cluster spun up in the GitHub CI."
        )

    home = await login_node_v2.get_output_async("echo $HOME")
    scratch = await login_node_v2.get_output_async("echo $SCRATCH")

    start = datetime.datetime.now() - timedelta(minutes=5)
    jobs_before = get_recent_jobs_info_dicts(
        login_node_v2, since=datetime.datetime.now() - start
    )
    jobs_before = {
        int(job_info["JobID"]): job_info
        for job_info in jobs_before
        if job_info["JobName"] == "mila-code"
    }

    relative_path = "bob"

    with contextlib.redirect_stderr(sys.stdout):
        logger.info(f"{'sbatch' if persist else 'salloc'} flags: {allocation_flags}")
        compute_node_or_job_id = await code(
            path=relative_path,
            command="echo",  # replace the usual `code` with `echo` for testing.
            # idea: Could probably also return the process ID of the `code` editor?
            persist=persist,
            job=None,
            node=None,
            alloc=allocation_flags,
            cluster=login_node_v2.hostname,  # type: ignore
        )

    # Get the output that was printed while running that command.
    captured_output = capsys.readouterr().out

    node_hostname: str | None = None
    if persist:
        assert isinstance(compute_node_or_job_id, ComputeNode)
        compute_node = compute_node_or_job_id
        assert compute_node is not None
        job_id = compute_node.job_id
        node_hostname = compute_node.hostname

    else:
        assert isinstance(compute_node_or_job_id, int)
        job_id = compute_node_or_job_id

    await asyncio.sleep(5)  # give a chance to sacct to update.

    job_info = await _get_job_info(
        job_id=job_id,
        login_node=login_node_v2,
        fields=("JobID", "JobName", "Node", "WorkDir", "State"),
    )
    if node_hostname is None:
        node_hostname = get_hostname_to_use_for_compute_node(
            job_info["Node"], cluster=login_node_v2.hostname
        )
    assert node_hostname and node_hostname != "None"

    # Check that the workdir is the scratch directory (because we cd'ed to $SCRATCH
    # before submitting the job)
    workdir = job_info["WorkDir"]
    assert workdir == scratch
    try:
        if persist:
            # Job should still be running since we're using `persist` (that's the whole
            # point.)
            # NOTE: There's a fixture that scancel's all our jobs spawned during unit tests
            # so there's no issue of lingering jobs on the cluster after the tests run/fail.
            assert job_info["State"] == "RUNNING"
            await compute_node.close_async()
        else:
            # NOTE: Job is actually in the `COMPLETED` state because we exited cleanly (by
            # passing `exit\n` to the salloc subprocess.)
            assert job_info["State"] == "COMPLETED"
    finally:
        login_node_v2.run(f"scancel {job_id}", display=True)

    def filter_captured_output(captured_output: str) -> str:
        # Remove information that may vary between runs from the regression test files.
        def filter_line(line: str) -> str:
            if (
                regex := re.compile(
                    r"Disk usage: \d+\.\d+ / \d+\.\d+ GiB and \d+ / \d+ files"
                )
            ).match(line):
                # IDEA: Use regex to go from this:
                # Disk usage: 66.56 / 100.00 GiB and 789192 / 1048576 files
                # to this:
                # Disk usage: X / LIMIT GiB and X / LIMIT files
                line = regex.sub("Disk usage: X / LIMIT GiB and X / LIMIT files", line)

            # If the line ends with an elapsed time, replace it with something constant.
            line = re.sub(r"\d+:\d+:\d+$", "H:MM:SS", line)
            # In the progress bar for syncing vscode extensions, there might be one with
            # N/N (which depends on how many extensions were missing). Replace it with a
            # constant.
            line = re.sub(r" \d+/\d+ ", " N/N ", line)

            return (
                line.rstrip()
                .replace(str(job_id), "JOB_ID")
                .replace(node_hostname, "COMPUTE_NODE")
                .replace(home, "$HOME")
                .replace(
                    f"--account={slurm_account_on_cluster}", "--account=SLURM_ACCOUNT"
                )
            )

        return "\n".join(filter_line(line) for line in captured_output.splitlines())

    file_regression.check(filter_captured_output(captured_output))


@pytest.mark.parametrize("use_v1", [False, True], ids=["code", "code_v1"])
@pytest.mark.asyncio
async def test_code_without_code_command_in_path(
    monkeypatch: pytest.MonkeyPatch, use_v1: bool
):
    """Test the case where `mila code` is run without having vscode installed."""

    def mock_which(command: str) -> str | None:
        assert command == "code"  # pretend like vscode isn't installed.
        return None

    monkeypatch.setattr(shutil, shutil.which.__name__, Mock(side_effect=mock_which))
    if use_v1:
        with pytest.raises(CommandNotFoundError):
            code_v1(
                path="bob",
                command="code",
                persist=False,
                job=None,
                node=None,
                alloc=[],
                cluster="bob",
            )
    else:
        with pytest.raises(CommandNotFoundError):
            await code(
                path="bob",
                command="code",
                persist=False,
                job=None,
                node=None,
                alloc=[],
                cluster="bob",
            )


@pytest_asyncio.fixture(scope="session")
async def existing_job(
    cluster: str,
    login_node_v2: RemoteV2,
    allocation_flags: list[str],
    job_name: str,
) -> ComputeNode:
    """Gets a compute node connecting to a running job on the cluster.

    This avoids making an allocation if possible, by reusing an already-running job with
    the name `job_name` if it exists.
    """
    if cluster == "localhost":
        pytest.skip(
            "This test doesn't yet work with the slurm cluster spun up in the GitHub CI."
        )

    existing_test_jobs_on_cluster = await get_queued_milatools_job_ids(
        login_node_v2, job_name=job_name
    )
    # todo: filter to use only the ones that are expected to be up for a little while
    # longer (e.g. 2-3 minutes)
    for job_id in existing_test_jobs_on_cluster:
        try:
            # Note: Connecting to a compute node runs a command with `srun`, so it will
            # raise an error if the job is no longer running.
            compute_node = await ComputeNode.connect(login_node_v2, job_id)
        except Exception as exc:
            logger.debug(f"Unable to reuse job {job_id}: {exc}")
        else:
            logger.info(
                f"Reusing existing test job with name {job_name} on the cluster: {job_id}"
            )
            return compute_node
    logger.info(
        "Unable to find existing test jobs on the cluster. Allocating a new one."
    )
    compute_node = await salloc(
        login_node_v2, salloc_flags=allocation_flags, job_name=job_name
    )
    return compute_node


@pytest.fixture
def doesnt_create_new_jobs_fixture(capsys: pytest.CaptureFixture):
    yield
    out, err = capsys.readouterr()
    assert "Submitted batch job" not in out
    assert "Submitted batch job" not in err
    assert "salloc: Granted job allocation" not in out
    assert "salloc: Granted job allocation" not in err


doesnt_create_new_jobs = pytest.mark.usefixtures(
    doesnt_create_new_jobs_fixture.__name__
)


@doesnt_create_new_jobs
@pytest.mark.parametrize("use_v1", [False, True], ids=["code", "code_v1"])
@pytest.mark.parametrize(
    ("use_node_name", "use_job_id"),
    [(True, False), (False, True), (True, True)],
    ids=["node", "job", "both"],
)
@pytest.mark.asyncio
async def test_code_with_existing_job(
    cluster: str,
    existing_job: ComputeNode,
    use_job_id: bool,
    use_node_name: bool,
    use_v1: bool,
    capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
):
    """Test using `mila code <path> --job <job_id>`"""

    path = "bob"
    assert use_job_id or use_node_name

    job: int | None = existing_job.job_id if use_job_id else None
    node: str | None = None
    if use_node_name:
        hostname = existing_job.hostname
        # We actually need to pass `cn-a001` (node name) as --node, not the entire
        # hostname!
        node = removesuffix(hostname, ".server.mila.quebec")

    if not use_v1:

        async def _mock_close_async():
            return

        monkeypatch.setattr(
            ComputeNode,
            ComputeNode.close_async.__name__,
            mock_close_async := AsyncMock(
                spec=ComputeNode.close_async, side_effect=_mock_close_async
            ),
        )

        def _mock_close():
            return

        monkeypatch.setattr(
            ComputeNode,
            ComputeNode.close.__name__,
            mock_close := Mock(spec=ComputeNode.close, side_effect=_mock_close),
        )

        compute_node_or_job_id = await code(
            path=path,
            command="echo",  # replace the usual `code` with `echo` for testing.
            persist=True,  # todo: Doesn't really make sense to pass --persist when using --job or --node!
            job=job,
            node=node,
            alloc=[],
            cluster=cluster,
        )
        assert isinstance(compute_node_or_job_id, ComputeNode)
        assert compute_node_or_job_id.job_id == existing_job.job_id
        assert compute_node_or_job_id.hostname == existing_job.hostname
        mock_close_async.assert_not_called()
        mock_close.assert_not_called()
    else:
        node: str | None = None
        if use_node_name:
            hostname = existing_job.hostname
            # We actually need to pass `cn-a001` (node name) as --node, not the entire
            # hostname!
            node = removesuffix(hostname, ".server.mila.quebec")

        code_v1(
            path=path,
            command="echo",  # replace the usual `code` with `echo` for testing.
            persist=True,  # so this doesn't try to cancel the running job on exit.
            job=job,
            node=node,
            alloc=[],
            cluster=cluster,
        )
        # BUG: here it prints that it ends the session, but it *doesn't* end the job.
        # This is correct, but misleading. We should probably print something else.
        ended_session_string = f"Ended session on {existing_job.hostname!r}"
        output = capsys.readouterr().out
        assert ended_session_string not in output


@doesnt_create_new_jobs
@pytest.mark.asyncio
@pytest.mark.parametrize("use_v1", [False, True], ids=["v2", "v1"])
async def test_code_with_disk_quota_reached(
    monkeypatch: pytest.MonkeyPatch, use_v1: bool
):
    if use_v1:
        from milatools.cli import commands

        # Makes the test slightly quicker to run.
        monkeypatch.setattr(commands, RemoteV1.__name__, Mock(spec=RemoteV1))

        def _mock_check_disk_quota_v1(remote: RemoteV1 | RemoteV2):
            raise MilatoolsUserError(
                "ERROR: Your disk quota on the $HOME filesystem is exceeded! "
            )

        mock_check_disk_quota = Mock(
            spec=check_disk_quota_v1, side_effect=_mock_check_disk_quota_v1
        )
        monkeypatch.setattr(
            disk_quota, check_disk_quota_v1.__name__, mock_check_disk_quota
        )
        monkeypatch.setattr(
            commands, check_disk_quota_v1.__name__, mock_check_disk_quota
        )
        with pytest.raises(MilatoolsUserError):
            code_v1(
                path="bob",
                command="echo",  # replace the usual `code` with `echo` for testing.
                persist=False,
                job=None,
                node="bobobo",  # to avoid accidental sallocs.
                alloc=[],
                cluster="bob",
            )
        mock_check_disk_quota.assert_called_once()
    else:
        from milatools.cli import code

        # Makes the test quicker to run by avoiding connecting to the cluster.
        monkeypatch.setattr(code, RemoteV2.__name__, Mock(spec=RemoteV2))

        async def _mock_check_disk_quota(remote: RemoteV1 | RemoteV2):
            raise MilatoolsUserError(
                "ERROR: Your disk quota on the $HOME filesystem is exceeded! "
            )

        mock_check_disk_quota = AsyncMock(
            spec=check_disk_quota, side_effect=_mock_check_disk_quota
        )
        monkeypatch.setattr(
            disk_quota, check_disk_quota.__name__, mock_check_disk_quota
        )

        monkeypatch.setattr(code, check_disk_quota.__name__, mock_check_disk_quota)
        with pytest.raises(MilatoolsUserError):
            await code.code(
                path="bob",
                command="echo",  # replace the usual `code` with `echo` for testing.
                persist=False,
                job=None,
                node="bobobo",  # to avoid accidental sallocs.
                alloc=[],
                cluster="bob",
            )
        mock_check_disk_quota.assert_called_once()
