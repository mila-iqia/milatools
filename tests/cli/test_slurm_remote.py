"""Tests that use an actual SLURM cluster.

The cluster to use can be specified by setting the SLURM_CLUSTER environment variable.
During the CI on GitHub, a small local slurm cluster is setup with a GitHub Action, and
SLURM_CLUSTER is set to "localhost".
"""
from __future__ import annotations

import datetime
import functools
import os
import time
from logging import getLogger as get_logger

import fabric.runners
import pytest

from milatools.cli.remote import Remote, SlurmRemote

logger = get_logger(__name__)

SLURM_CLUSTER = os.environ.get("SLURM_CLUSTER")
JOB_NAME = "milatools_test"
WCKEY = "milatools_test"
MAX_JOB_DURATION = datetime.timedelta(seconds=10)
# BUG: pytest-timeout seems to cause issues with paramiko threads..
# pytestmark = pytest.mark.timeout(60)


_SACCT_UPDATE_DELAY = datetime.timedelta(seconds=10)
"""How long after salloc/sbatch before we expect to see the job show up in sacct."""

requires_access_to_slurm_cluster = pytest.mark.skipif(
    not SLURM_CLUSTER,
    reason="Requires ssh access to a SLURM cluster.",
)

# TODO: Import the value from `milatools.cli.utils` once the other PR adds it.
CLUSTERS = ["mila", "narval", "cedar", "beluga", "graham"]


@pytest.fixture(scope="session", params=[SLURM_CLUSTER])
def cluster(request: pytest.FixtureRequest) -> str:
    """Fixture that gives the hostname of the slurm cluster to use for tests.

    NOTE: The `cluster` can also be parametrized indirectly by tests, for example:

    ```python
    @pytest.mark.parametrize("cluster", ["mila", "some_cluster"], indirect=True)
    def test_something(remote: Remote):
        ...  # here the remote is connected to the cluster specified above!
    ```
    """
    slurm_cluster_hostname = request.param

    if not slurm_cluster_hostname:
        pytest.skip("Requires ssh access to a SLURM cluster.")
    return slurm_cluster_hostname


def can_run_on_all_clusters():
    """Makes a given test run on all the clusters in `CLUSTERS`, *for real*!

    NOTE: (@lebrice): Unused here at the moment in the GitHub CI, but locally I'm
    enabling it sometimes to test stuff on DRAC clusters.
    """
    return pytest.mark.parametrize("cluster", CLUSTERS, indirect=True)


@pytest.fixture()
def login_node(cluster: str) -> Remote:
    """Fixture that gives a Remote connected to the login node of the slurm cluster.

    NOTE: Making this a function-scoped fixture because the Connection object is of the
    Remote is used when creating the SlurmRemotes.
    """
    return Remote(cluster)


@pytest.fixture(scope="module", autouse=True)
def cancel_all_milatools_jobs_before_and_after_tests(cluster: str):
    # Note: need to recreate this because login_node is a function-scoped fixture.
    login_node = Remote(cluster)
    login_node.run(f"scancel -u $USER --wckey={WCKEY}")
    time.sleep(1)
    yield
    login_node.run(f"scancel -u $USER --wckey={WCKEY}")
    time.sleep(1)
    # Display the output of squeue just to be sure that the jobs were cancelled.
    login_node._run("squeue --me", echo=True, in_stream=False)


@functools.lru_cache()
def get_slurm_account(cluster: str) -> str:
    """Gets the SLURM account of the user using sacctmgr on the slurm cluster.

    When there are multiple accounts, this selects the first account, alphabetically.

    On DRAC cluster, this uses the `def` allocations instead of `rrg`, and when
    the rest of the accounts are the same up to a '_cpu' or '_gpu' suffix, it uses
    '_cpu'.

    For example:

    ```text
    def-someprofessor_cpu  <-- this one is used.
    def-someprofessor_gpu
    rrg-someprofessor_cpu
    rrg-someprofessor_gpu
    ```
    """
    # note: recreating the Connection here because this will be called for every test
    # and we use functools.cache to cache the result, so the input has to be a simpler
    # value like a string.
    result = fabric.Connection(cluster).run(
        "sacctmgr --noheader show associations where user=$USER format=Account%50",
        echo=True,
        in_stream=False,
    )
    assert isinstance(result, fabric.runners.Result)
    accounts: list[str] = [line.strip() for line in result.stdout.splitlines()]
    assert accounts
    logger.info(f"Accounts on the slurm cluster {cluster}: {accounts}")
    account = sorted(accounts)[0]
    logger.info(f"Using account {account} to launch jobs in tests.")
    return account


def get_recent_jobs_info(
    login_node: Remote,
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
        echo=True,
        in_stream=False,
    ).stdout.splitlines()
    # note: using maxsplit because the State field can contain spaces: "canceled by ..."
    return [tuple(line.strip().split(maxsplit=len(fields))) for line in lines]


def sleep_so_sacct_can_update():
    print("Sleeping so sacct can update...")
    time.sleep(_SACCT_UPDATE_DELAY.total_seconds())


@pytest.fixture()
def allocation_flags(cluster: str, request: pytest.FixtureRequest):
    # note: thanks to lru_cache, this is only making one ssh connection per cluster.
    account = get_slurm_account(cluster)
    allocation_options = {
        "job-name": JOB_NAME,
        "wckey": WCKEY,
        "account": account,
        "nodes": 1,
        "ntasks": 1,
        "cpus-per-task": 1,
        "mem": "1G",
        "time": MAX_JOB_DURATION,
        "oversubscribe": None,  # allow multiple such jobs to share resources.
    }
    overrides = getattr(request, "param", {})
    assert isinstance(overrides, dict)
    if overrides:
        print(f"Overriding allocation options with {overrides}")
        allocation_options.update(overrides)
    return " ".join(
        [
            f"--{key}={value}" if value is not None else f"--{key}"
            for key, value in allocation_options.items()
        ]
    )


@requires_access_to_slurm_cluster
def test_cluster_setup(login_node: Remote, allocation_flags: str):
    """Sanity Checks for the SLURM cluster of the CI: checks that `srun` works.

    NOTE: This is more-so a test to check that the slurm cluster used in the GitHub CI
    is setup correctly, rather than to check that the Remote/SlurmRemote work correctly.
    """

    job_id, compute_node = (
        login_node.get_output(
            f"srun {allocation_flags} bash -c 'echo $SLURM_JOB_ID $SLURMD_NODENAME'"
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
def salloc_slurm_remote(login_node: Remote, allocation_flags: str):
    """Fixture that creates a `SlurmRemote` that uses `salloc` (persist=False).

    The SlurmRemote is essentially just a Remote with an added `ensure_allocation`
    method as well as a transform that does `salloc` or `sbatch` with some allocation
    flags before a command is run.
    """
    return SlurmRemote(
        connection=login_node.connection,
        alloc=allocation_flags.split(),
    )


@pytest.fixture
def sbatch_slurm_remote(login_node: Remote, allocation_flags: str):
    """Fixture that creates a `SlurmRemote` that uses `sbatch` (persist=True)."""
    return SlurmRemote(
        connection=login_node.connection,
        alloc=allocation_flags.split(),
        persist=True,
    )


## Tests for the SlurmRemote class:


@requires_access_to_slurm_cluster
def test_run(
    login_node: Remote,
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


hangs_in_github_CI = pytest.mark.skipif(
    SLURM_CLUSTER == "localhost", reason="BUG: Hangs in the GitHub CI.."
)


@hangs_in_github_CI
@requires_access_to_slurm_cluster
def test_ensure_allocation(
    login_node: Remote,
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
    result2 = login_node.run("hostname", echo=True, in_stream=False)
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


@hangs_in_github_CI
@requires_access_to_slurm_cluster
def test_ensure_allocation_sbatch(login_node: Remote, sbatch_slurm_remote: SlurmRemote):
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
