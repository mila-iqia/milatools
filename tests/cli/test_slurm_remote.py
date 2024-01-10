"""Tests that use an actual SLURM cluster.

The cluster to use can be specified by setting the SLURM_CLUSTER environment variable.
During the CI on GitHub, a small local slurm cluster is setup with a GitHub Action, and
SLURM_CLUSTER is set to "localhost".
"""
from __future__ import annotations

import datetime
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


@pytest.fixture(scope="session")
def login_node(cluster: str) -> Remote:
    """Fixture that gives a Remote connected to the login node of the slurm cluster."""
    return Remote(cluster)


@pytest.fixture(scope="module", autouse=True)
def cancel_all_milatools_jobs_after_tests(login_node: Remote):
    yield
    login_node.simple_run(f"scancel -u $USER --wckey={WCKEY}")
    time.sleep(1)
    # Display the output of squeue just to be sure that the jobs were cancelled.
    login_node.simple_run("squeue --me")


def get_slurm_account(cluster_login_node: Remote) -> str:
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
    accounts: list[str] = cluster_login_node.get_output(
        "sacctmgr --noheader show associations where user=$USER format=Account%50",
    ).split()
    assert accounts
    logger.info(
        f"Accounts on the slurm cluster {cluster_login_node.hostname}: {accounts}"
    )
    account = sorted(accounts)[0]
    logger.info(f"Using account {account} to launch jobs in tests.")
    return account


def get_recent_jobs_info(
    cluster_login_node: Remote,
    since=datetime.timedelta(minutes=5),
    fields=("JobID", "JobName", "Node", "State"),
) -> list[list[str]]:
    """Returns a list of fields for jobs that started recently."""
    # otherwise this would launch a job!
    assert not isinstance(cluster_login_node, SlurmRemote)
    lines = cluster_login_node.run(
        f"sacct --noheader --allocations --starttime=now-{since.seconds}seconds "
        "--format=" + ",".join(f"{field}%40" for field in fields),
        echo=True,
    ).stdout.splitlines()
    # note: using maxsplit because the State field can contain spaces: "canceled by ..."
    return [line.strip().split(maxsplit=len(fields)) for line in lines]


@pytest.fixture(scope="session")
def allocation_flags(login_node: Remote, request: pytest.FixtureRequest):
    account = get_slurm_account(login_node)
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

    # NOTE: the job should be done by now, since `.run` of the Remote is called with
    # asynchronous=False.
    sacct_output = login_node.get_output(
        "sacct --noheader --allocations --starttime=now-1minutes "
        "--format=JobID,JobName,Node%30,State"
    )
    assert compute_node in sacct_output
    assert job_id in sacct_output


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
    """Fixture that creates a `SlurmRemote` that uses `sbatch` (persist=False)."""
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

    # This stuff gets printed out when you do an `srun` on the login node.
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

    time.sleep(1)

    sacct_output = get_recent_jobs_info(login_node, fields=("JobID", "JobName", "Node"))
    assert [job_id, JOB_NAME, compute_node] in sacct_output


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
          the login node.)

    FIXME:  It should be made impossible / a critical error to make more than a single
    call to `run` or `ensure_allocation` on a SlurmRemote, because every call to `run`
    creates a new job!
    (This is because .run applies the transforms to the command, and the last
    transform adds either `salloc` or `sbatch`, hence every call to `run` launches
    either an interactive or batch job!)
    """
    # todo: test using this kind of workflow:
    # from milatools.cli.commands import _find_allocation
    # cnode = _find_allocation(
    #     remote, job_name="mila-code", job=job, node=node, alloc=alloc
    # )
    # if persist:
    #     cnode = cnode.persist()
    # data, proc = cnode.ensure_allocation()

    data, remote_runner = salloc_slurm_remote.ensure_allocation()
    assert isinstance(remote_runner, fabric.runners.Remote)
    # Mark this as the start time.
    datetime.datetime.now()

    assert "node_name" in data
    # NOTE: it very well could be if we also extracted it from the salloc output.
    assert "jobid" not in data
    compute_node_from_salloc_output = data["node_name"]

    # Check that salloc was called. This would be printed to stderr by fabric if we were
    # using `run`, but `ensure_allocation` uses `extract` which somehow doesn't print it
    stdout, stderr = capsys.readouterr()
    # assert not stdout
    # assert not stderr
    assert "salloc: Granted job allocation" in stdout
    assert (
        f"salloc: Nodes {compute_node_from_salloc_output} are ready for job" in stdout
    )
    assert not stderr

    # Check that the returned remote runner is indeed connected to a *login* node (?!)
    # NOTE: This is a fabric.runners.Remote, not a Remote or SlurmRemote of milatools.
    result = remote_runner.run("hostname", echo=True, in_stream=False)
    assert result
    hostname_from_remote_runner = result.stdout.strip()
    result2 = login_node._run("hostname", echo=True, in_stream=False)
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
    time.sleep(MAX_JOB_DURATION.total_seconds() + 1)

    sacct_output = get_recent_jobs_info(login_node, fields=("JobName", "Node", "State"))
    assert [JOB_NAME, compute_node_from_salloc_output, "COMPLETED"] in sacct_output


@requires_access_to_slurm_cluster
def test_ensure_allocation_sbatch(login_node: Remote, sbatch_slurm_remote: SlurmRemote):
    # todo: test using this kind of workflow:
    # from milatools.cli.commands import _find_allocation
    # cnode = _find_allocation(
    #     remote, job_name="mila-code", job=job, node=node, alloc=alloc
    # )
    # if persist:
    #     cnode = cnode.persist()
    # data, proc = cnode.ensure_allocation()

    job_data, login_node_remote_runner = sbatch_slurm_remote.ensure_allocation()
    print(job_data, login_node_remote_runner)
    assert isinstance(login_node_remote_runner, fabric.runners.Remote)

    node_name_from_sbatch_output = job_data["node_name"]
    assert "jobid" in job_data
    job_id_from_sbatch_extract = job_data["jobid"]

    time.sleep(1)  # Let enough time for sacct to update.

    sacct_output = get_recent_jobs_info(login_node, fields=("JobId", "JobName", "Node"))
    assert [
        job_id_from_sbatch_extract,
        JOB_NAME,
        node_name_from_sbatch_output,
    ] in sacct_output
