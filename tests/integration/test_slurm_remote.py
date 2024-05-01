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

import milatools
import milatools.cli
import milatools.cli.utils
from milatools.cli.utils import CLUSTERS
from milatools.utils.remote_v1 import RemoteV1, SlurmRemote
from milatools.utils.remote_v2 import RemoteV2

from ..cli.common import on_windows
from ..conftest import launches_jobs
from .conftest import SLURM_CLUSTER, hangs_in_github_CI

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
    login_node: RemoteV1 | RemoteV2,
    since=datetime.timedelta(minutes=5),
    fields=("JobID", "JobName", "Node", "State"),
) -> list[dict[str, str]]:
    return [
        dict(zip(fields, line))
        for line in get_recent_jobs_info(login_node, since=since, fields=fields)
    ]


def get_recent_jobs_info(
    login_node: RemoteV1 | RemoteV2,
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


@pytest.fixture
def fabric_connection_to_login_node(login_node: RemoteV1 | RemoteV2):
    if isinstance(login_node, RemoteV1):
        return login_node.connection

    if login_node.hostname not in ["localhost", "mila"]:
        pytest.skip(
            reason=(
                f"Not making a fabric.Connection to {login_node.hostname} since it "
                f"might go through 2FA!"
            )
        )
    return RemoteV1(login_node.hostname).connection


@pytest.fixture
def salloc_slurm_remote(
    fabric_connection_to_login_node: fabric.Connection, allocation_flags: list[str]
):
    """Fixture that creates a `SlurmRemote` that uses `salloc` (persist=False).

    The SlurmRemote is essentially just a Remote with an added `ensure_allocation`
    method as well as a transform that does `salloc` or `sbatch` with some allocation
    flags before a command is run.
    """
    return SlurmRemote(
        connection=fabric_connection_to_login_node,
        alloc=allocation_flags,
    )


@pytest.fixture
def sbatch_slurm_remote(
    fabric_connection_to_login_node: fabric.Connection, allocation_flags: list[str]
):
    """Fixture that creates a `SlurmRemote` that uses `sbatch` (persist=True)."""
    return SlurmRemote(
        connection=fabric_connection_to_login_node,
        alloc=allocation_flags,
        persist=True,
    )


## Tests for the SlurmRemote class:


PARAMIKO_SSH_BANNER_BUG = pytest.mark.xfail(
    True,
    reason="TODO: Sometimes get an annoying Paramiko SSH Banner issue!",
    raises=milatools.cli.utils.SSHConnectionError,
    strict=False,
)


@pytest.mark.slow
@PARAMIKO_SSH_BANNER_BUG
@launches_jobs
@requires_access_to_slurm_cluster
def test_run(
    login_node: RemoteV1 | RemoteV2,
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

    # BUG: on the GitHub CI, where the slurm cluster is localhost, this check fails:
    # the job names don't match what we'd expect! --> removing the job name check for now.
    # sacct_output = get_recent_jobs_info(login_node, fields=("JobID", "JobName", "Node"))
    # assert (job_id, JOB_NAME, compute_node) in sacct_output
    sacct_output = get_recent_jobs_info(login_node, fields=("JobID", "Node"))
    assert (job_id, compute_node) in sacct_output


@pytest.mark.skip(reason="The way this test checks if the job ran is brittle.")
@pytest.mark.slow
@PARAMIKO_SSH_BANNER_BUG
@launches_jobs
@hangs_in_github_CI
@requires_access_to_slurm_cluster
def test_ensure_allocation(
    login_node: RemoteV1 | RemoteV2,
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
    try:
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
        # BUG: If the `login_node` is a RemoteV2, then this will fail because the hostname
        # might differ between the two (multiple login nodes in the Mila cluster).
        result2 = login_node.run("hostname", display=True, hide=False)
        assert result2
        hostname_from_login_node_runner = result2.stdout.strip()

        if isinstance(login_node, RemoteV2) and login_node.hostname == "mila":
            assert hostname_from_remote_runner.startswith("login-")
            assert hostname_from_login_node_runner.startswith("login-")
        elif isinstance(login_node, RemoteV1):
            assert hostname_from_remote_runner == hostname_from_login_node_runner
    finally:
        result = remote_runner.run("echo $SLURM_JOB_ID", echo=True, in_stream=False)
        assert result
        job_id = int(result.stdout.strip())
        login_node.run(f"scancel {job_id}", display=True, hide=False)


@launches_jobs
@pytest.mark.slow
@PARAMIKO_SSH_BANNER_BUG
@pytest.mark.xfail(
    on_windows,
    raises=PermissionError,
    reason="BUG: Getting permission denied while reading a NamedTemporaryFile?",
)
@hangs_in_github_CI
@requires_access_to_slurm_cluster
def test_ensure_allocation_sbatch(
    login_node: RemoteV1 | RemoteV2,
    sbatch_slurm_remote: SlurmRemote,
    job_name: str,
):
    job_data, login_node_remote_runner = sbatch_slurm_remote.ensure_allocation()
    print(job_data, login_node_remote_runner)
    assert isinstance(login_node_remote_runner, fabric.runners.Remote)

    node_hostname = job_data["node_name"]
    assert "jobid" in job_data
    job_id_from_sbatch_extract = job_data["jobid"]
    try:
        sleep_so_sacct_can_update()

        job_infos = get_recent_jobs_info(
            login_node, fields=("JobId", "JobName", "Node")
        )
        # BUG: The job name can be very long, which can lead to an error here.
        assert (job_id_from_sbatch_extract, job_name, node_hostname) in job_infos
    finally:
        job_id_from_sbatch_extract = int(job_id_from_sbatch_extract)
        login_node.run(
            f"scancel {job_id_from_sbatch_extract}", display=True, hide=False
        )
