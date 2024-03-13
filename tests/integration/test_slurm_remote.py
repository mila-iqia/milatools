"""Tests that use an actual SLURM cluster.

The cluster to use can be specified by setting the SLURM_CLUSTER environment variable.
During the CI on GitHub, a small local slurm cluster is setup with a GitHub Action, and
SLURM_CLUSTER is set to "localhost".
"""
from __future__ import annotations

import datetime
import time
from logging import getLogger as get_logger

import fabric.runners
import pytest

from milatools.cli.remote import Remote, SlurmRemote
from milatools.cli.utils import CLUSTERS
from milatools.utils.remote_v2 import RemoteV2

from ..cli.common import on_windows
from .conftest import JOB_NAME, MAX_JOB_DURATION, SLURM_CLUSTER, hangs_in_github_CI

logger = get_logger(__name__)

# BUG: pytest-timeout seems to cause issues with paramiko threads..
# pytestmark = pytest.mark.timeout(60)


_SACCT_UPDATE_DELAY = datetime.timedelta(seconds=10)
"""How long after salloc/sbatch before we expect to see the job show up in sacct."""

requires_access_to_slurm_cluster = pytest.mark.skipif(
    not SLURM_CLUSTER,
    reason="Requires ssh access to a SLURM cluster.",
)


def can_run_on_all_clusters():
    """Makes a given test run on all the clusters in `CLUSTERS`, *for real*!

    NOTE: (@lebrice): Unused here at the moment in the GitHub CI, but locally I'm
    enabling it sometimes to test stuff on DRAC clusters.
    """
    return pytest.mark.parametrize("cluster", CLUSTERS, indirect=True)


def get_recent_jobs_info_dicts(
    login_node: Remote | RemoteV2,
    since=datetime.timedelta(minutes=5),
    fields=("JobID", "JobName", "Node", "State"),
) -> list[dict[str, str]]:
    return [
        dict(zip(fields, line))
        for line in get_recent_jobs_info(login_node, since=since, fields=fields)
    ]


def get_recent_jobs_info(
    login_node: Remote | RemoteV2,
    since=datetime.timedelta(minutes=5),
    fields=("JobID", "JobName", "Node", "State"),
) -> list[tuple[str, ...]]:
    """Returns a list of fields for jobs that started recently."""
    # otherwise this would launch a job!
    assert not isinstance(login_node, SlurmRemote)
    lines = login_node.run(
        f"sacct --noheader --allocations "
        f"--starttime=now-{int(since.total_seconds())}seconds "
        "--format=" + ",".join(f"{field}%40" for field in fields),
        display=True,
    ).stdout.splitlines()
    # note: using maxsplit because the State field can contain spaces: "canceled by ..."
    return [tuple(line.strip().split(maxsplit=len(fields))) for line in lines]


def sleep_so_sacct_can_update():
    print("Sleeping so sacct can update...")
    time.sleep(_SACCT_UPDATE_DELAY.total_seconds())


@requires_access_to_slurm_cluster
def test_cluster_setup(login_node: Remote | RemoteV2, allocation_flags: list[str]):
    """Sanity Checks for the SLURM cluster of the CI: checks that `srun` works.

    NOTE: This is more-so a test to check that the slurm cluster used in the GitHub CI
    is setup correctly, rather than to check that the Remote/SlurmRemote work correctly.
    """

    job_id, compute_node = (
        login_node.get_output(
            f"srun {' '.join(allocation_flags)} bash -c 'echo $SLURM_JOB_ID $SLURMD_NODENAME'"
        )
        .strip()
        .split()
    )
    assert compute_node
    assert job_id.isdigit()

    sleep_so_sacct_can_update()

    # NOTE: the job should be done by now, since `.run` of the Remote is called with
    # asynchronous=False.
    sacct_output = get_recent_jobs_info(login_node, fields=("JobID", "JobName", "Node"))
    assert (job_id, JOB_NAME, compute_node) in sacct_output


@pytest.fixture
def connection_to_login_node(login_node: Remote | RemoteV2):
    if isinstance(login_node, Remote):
        return login_node.connection
    if login_node.hostname not in ["localhost", "mila"]:
        pytest.skip(
            reason=(
                f"Not making a fabric.Connection to {login_node.hostname} since it "
                f"might go through 2FA!"
            )
        )
    return Remote(login_node.hostname).connection


@pytest.fixture
def salloc_slurm_remote(
    connection_to_login_node: fabric.Connection, allocation_flags: list[str]
):
    """Fixture that creates a `SlurmRemote` that uses `salloc` (persist=False).

    The SlurmRemote is essentially just a Remote with an added `ensure_allocation`
    method as well as a transform that does `salloc` or `sbatch` with some allocation
    flags before a command is run.
    """
    return SlurmRemote(
        connection=connection_to_login_node,
        alloc=allocation_flags,
    )


@pytest.fixture
def sbatch_slurm_remote(
    connection_to_login_node: fabric.Connection, allocation_flags: list[str]
):
    """Fixture that creates a `SlurmRemote` that uses `sbatch` (persist=True)."""
    return SlurmRemote(
        connection=connection_to_login_node,
        alloc=allocation_flags,
        persist=True,
    )


## Tests for the SlurmRemote class:


@requires_access_to_slurm_cluster
def test_run(
    login_node: Remote | RemoteV2,
    salloc_slurm_remote: SlurmRemote,
):
    """Test for `SlurmRemote.run` with persist=False without an initial call to
    `ensure_allocation`.

    This should use `srun` from the login node to run the command, and return a result.
    """
    result = salloc_slurm_remote.run("echo $SLURM_JOB_ID $SLURMD_NODENAME", echo=True)
    assert isinstance(result, fabric.runners.Result)
    output_lines = result.stdout.strip().splitlines()
    assert len(output_lines) == 1
    job_id, compute_node = output_lines[0].split()

    assert compute_node
    assert job_id.isdigit()

    # This stuff gets printed out when you do an `srun` on the login node (at least it
    # does on the Mila cluster. Doesn't seem to be the case when using the slurm cluster
    # on localhost in the CI.)
    if login_node.hostname != "localhost":
        assert "srun: ----" in result.stderr

    # NOTE: the job should be done by now, since `.run` of the Remote is called with
    # asynchronous=False.

    # This check is to make sure that even though the Remote and SlurmRemote share the
    # same fabric.Connection object, this is actually running on the login node, and not
    # on the compute node.
    # TODO: Move this check to a test for salloc, since here it's working because the
    # job is completed, not because it's being executed on the login node.
    login_node_hostname = login_node.get_output("hostname")
    assert login_node_hostname != compute_node

    sleep_so_sacct_can_update()

    sacct_output = get_recent_jobs_info(login_node, fields=("JobID", "JobName", "Node"))
    assert (job_id, JOB_NAME, compute_node) in sacct_output


@hangs_in_github_CI
@requires_access_to_slurm_cluster
def test_ensure_allocation(
    login_node: Remote | RemoteV2,
    salloc_slurm_remote: SlurmRemote,
    capsys: pytest.CaptureFixture[str],
):
    """Test that `ensure_allocation` calls salloc for a SlurmRemote with persist=False.

    Calling `ensure_allocation` on a SlurmRemote with persist=False should:
    1. Call `salloc` on the login node and retrieve the allocated node name,
    2. return a dict with the node name and a remote runner that is:
        TODO: What should it be?
        - connected to the login node (what's currently happening)
        - connected to the compute node through the interactive terminal of salloc on
          the login node.

    FIXME:  It should be made impossible / a critical error to make more than a single
    call to `run` or `ensure_allocation` on a SlurmRemote, because every call to `run`
    creates a new job!
    (This is because .run applies the transforms to the command, and the last
    transform adds either `salloc` or `sbatch`, hence every call to `run` launches
    either an interactive or batch job!)
    """
    data, remote_runner = salloc_slurm_remote.ensure_allocation()
    assert isinstance(remote_runner, fabric.runners.Remote)

    assert "node_name" in data
    # NOTE: it very well could be if we also extracted it from the salloc output.
    assert "jobid" not in data
    compute_node_from_salloc_output = data["node_name"]

    # Check that salloc was called. This would be printed to stderr by fabric if we were
    # using `run`, but `ensure_allocation` uses `extract` which somehow doesn't print it
    salloc_stdout, salloc_stderr = capsys.readouterr()
    # assert not stdout
    # assert not stderr
    assert "salloc: Granted job allocation" in salloc_stdout
    assert (
        f"salloc: Nodes {compute_node_from_salloc_output} are ready for job"
        in salloc_stdout
    )
    assert not salloc_stderr

    # Check that the returned remote runner is indeed connected to a *login* node (?!)
    # NOTE: This is a fabric.runners.Remote, not a Remote or SlurmRemote of milatools,
    # so calling `.run` doesn't launch a job.
    result = remote_runner.run("hostname", echo=True, in_stream=False)
    assert result
    hostname_from_remote_runner = result.stdout.strip()
    result2 = login_node.run("hostname", display=True, hide=False)
    assert result2
    hostname_from_login_node_runner = result2.stdout.strip()
    assert hostname_from_remote_runner == hostname_from_login_node_runner

    # TODO: IF the remote runner was to be connected to the compute node through the
    # same interactive terminal, then we'd use this:
    # result = remote_runner.run(
    #     "echo $SLURM_JOB_ID $SLURMD_NODENAME",
    #     echo=True,
    #     echo_format=T.bold_cyan(
    #         f"({compute_node_from_salloc_output})" + " $ {command}"
    #     ),
    #     in_stream=False,
    # )
    # assert result
    # assert not result.stderr
    # assert result.stdout.strip()
    # job_id, compute_node = result.stdout.strip().split()
    # # cn-a001 vs cn-a001.server.mila.quebec for example.
    # assert compute_node.startswith(compute_node_from_salloc_output)
    # assert compute_node != login_node.hostname  # hopefully also works in CI...

    # NOTE: too brittle.
    # if datetime.datetime.now() - start_time < MAX_JOB_DURATION:
    #     # Check that the job shows up as still running in the output of `sacct`, since
    #     # we should not have reached the end time yet.
    #     sacct_output = get_recent_jobs_info(
    #         login_node, fields=("JobName", "Node", "State")
    #     )
    #     assert [JOB_NAME, compute_node_from_salloc_output, "RUNNING"] in sacct_output

    print(f"Sleeping for {MAX_JOB_DURATION.total_seconds()}s until job finishes...")
    time.sleep(MAX_JOB_DURATION.total_seconds())

    sacct_output = get_recent_jobs_info(login_node, fields=("JobName", "Node", "State"))
    assert (JOB_NAME, compute_node_from_salloc_output, "COMPLETED") in sacct_output


@pytest.mark.xfail(
    on_windows,
    raises=PermissionError,
    reason="BUG: Getting permission denied while reading a NamedTemporaryFile?",
)
@hangs_in_github_CI
@requires_access_to_slurm_cluster
def test_ensure_allocation_sbatch(
    login_node: Remote | RemoteV2, sbatch_slurm_remote: SlurmRemote
):
    job_data, login_node_remote_runner = sbatch_slurm_remote.ensure_allocation()
    print(job_data, login_node_remote_runner)
    assert isinstance(login_node_remote_runner, fabric.runners.Remote)

    node_hostname = job_data["node_name"]
    assert "jobid" in job_data
    job_id_from_sbatch_extract = job_data["jobid"]

    sleep_so_sacct_can_update()

    job_infos = get_recent_jobs_info(login_node, fields=("JobId", "JobName", "Node"))
    # NOTE: `.extract`, used by `ensure_allocation`, actually returns the full node
    # hostname as an output (e.g. cn-a001.server.mila.quebec), but sacct only shows the
    # node name.
    assert any(
        (
            job_id_from_sbatch_extract == job_id
            and JOB_NAME == job_name
            and node_hostname.startswith(node_name)
            for job_id, job_name, node_name in job_infos
        )
    )
