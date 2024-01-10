"""Tests that use an actual SLURM cluster.

The cluster to use can be specified by setting the SLURM_CLUSTER environment variable.
During the CI on GitHub, a small local slurm cluster is setup with a GitHub Action, and
SLURM_CLUSTER is set to "localhost".
"""
from __future__ import annotations

import datetime
import functools
import io
import os
import time
from logging import getLogger as get_logger

import fabric.runners
import pytest

from milatools.cli.remote import Remote, SlurmRemote
from milatools.cli.utils import T

logger = get_logger(__name__)

SLURM_CLUSTER = os.environ.get("SLURM_CLUSTER")
JOB_NAME = "milatools_test"
WCKEY = "milatools_test"

# BUG: pytest-timeout seems to cause issues with paramiko threads..
# pytestmark = pytest.mark.timeout(60)

requires_access_to_slurm_cluster = pytest.mark.skipif(
    not SLURM_CLUSTER,
    reason="Requires ssh access to a SLURM cluster.",
)

# TODO: Import the value from `milatools.cli.utils` once the other PR adds it.
CLUSTERS = ["mila", "narval", "cedar", "beluga", "graham"]


def can_run_on_all_clusters():
    return pytest.mark.parametrize("cluster", CLUSTERS, indirect=True)


@pytest.fixture(scope="session", params=[SLURM_CLUSTER])
def cluster(request: pytest.FixtureRequest) -> str:
    # NOTE: Adding this 'param' here allows tests to indirectly parametrize the cluster
    # to use with
    slurm_cluster_hostname = request.param
    if not slurm_cluster_hostname:
        pytest.skip("Requires ssh access to a SLURM cluster.")
    return slurm_cluster_hostname


@pytest.fixture(scope="session")
def cluster_login_node(cluster: str) -> Remote:
    return Remote(cluster)


@pytest.fixture(scope="module", autouse=True)
def cancel_all_milatools_jobs_after_tests(cluster_login_node: Remote):
    yield
    cluster_login_node.run(f"scancel -u $USER --wckey={WCKEY}")
    time.sleep(1)
    # Display the output of squeue just to be sure that the jobs were cancelled.
    cluster_login_node.run("squeue --me")


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
    logger.info(f"Using account {account} for jobs launched in tests.")
    return account


@pytest.fixture(scope="session")
def allocation_flags(cluster_login_node: Remote, request: pytest.FixtureRequest):
    account = get_slurm_account(cluster_login_node)
    allocation_options = {
        "job-name": JOB_NAME,
        "wckey": WCKEY,
        "account": account,
        "nodes": 1,
        "ntasks": 1,
        "cpus-per-task": 1,
        "mem": "1G",
        "time": datetime.timedelta(seconds=10),
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
def test_srun(cluster_login_node: Remote, allocation_flags: str):
    """Checks that `srun` works on the slurm cluster with the parametrized flags.

    NOTE: This is more so a test to check that the slurm cluster used in the GitHub CI
    is setup correctly, rather than to check that the Remote/SlurmRemote work correctly.
    """
    output = cluster_login_node.get_output(f"srun {allocation_flags} hostname")
    assert output
    assert "not found" not in output


@requires_access_to_slurm_cluster
def test_salloc(cluster_login_node: Remote, allocation_flags: str):
    """Checks that `salloc` works on the slurm cluster with the parametrized flags."""
    pytest.skip(
        "TODO: Implement this to show that we can use a regular Remote with salloc and "
        "piping of the input stream."
    )
    in_stream = io.StringIO()
    cluster_login_node.run(
        f"salloc {allocation_flags}", asynchronous=True, in_stream=in_stream
    )


@pytest.fixture
def slurm_remote(cluster_login_node: Remote, allocation_flags: str):
    """Fixture that creates a `SlurmRemote` that uses `salloc` (persist=False).

    The SlurmRemote is essentially just a Remote with an added `ensure_allocation` and a
    transform that does `salloc` or `sbatch` with some allocation flags before a command
    is run.
    """
    return SlurmRemote(
        connection=cluster_login_node.connection,
        alloc=allocation_flags.split(),
    )


# @pytest.mark.skip(reason="seems to hang on the CI.")
@requires_access_to_slurm_cluster
def test_ensure_allocation(slurm_remote: SlurmRemote):
    """Test that `ensure_allocation` calls salloc for a SlurmRemote with persist=False.

    TODO: I believe it would be simpler and cleaner to do ensure_allocation using a
    function that takes in a Remote as argument, and possibly returning a Remote
    connected to the compute node (or not, idk).
    """
    data, login_node_remote_runner = slurm_remote.ensure_allocation()
    assert isinstance(login_node_remote_runner, fabric.runners.Remote)
    hostname_from_salloc_output = data["node_name"]

    time.sleep(5)  # seems like squeue doesn't update quite fast enough sometimes.
    print("Running squeue --me")
    squeue_output = slurm_remote.simple_run("squeue --me")
    assert hostname_from_salloc_output in squeue_output.stdout
    print("End of test")


@pytest.fixture
def persistent_slurm_remote(cluster_login_node: Remote, allocation_flags: str):
    return SlurmRemote(
        connection=cluster_login_node.connection,
        alloc=allocation_flags.split(),
        persist=True,
    )


@pytest.mark.skip(
    reason="TODO: Fix this test, seems to hang at the end even though the job ran."
)
@requires_access_to_slurm_cluster
def test_ensure_allocation_sbatch(persistent_slurm_remote: SlurmRemote):
    # TODO: The SlurmRemote class is quite confusing: Is it a connection to the compute
    # node? Or is it a connection to the login node?

    job_data, login_node_remote_runner = persistent_slurm_remote.ensure_allocation()
    print(job_data, login_node_remote_runner)

    assert isinstance(login_node_remote_runner, fabric.runners.Remote)

    node_name_from_sbatch_extract = job_data["node_name"]
    assert "jobid" in job_data
    job_id_from_sbatch_extract = job_data["jobid"]

    time.sleep(5)  # Let enough time for squeue to update.
    squeue_output = persistent_slurm_remote.run("squeue --me")
    assert squeue_output
    squeue_output = squeue_output.stdout.strip()
    print(squeue_output)

    assert node_name_from_sbatch_extract in squeue_output
    assert job_id_from_sbatch_extract in squeue_output


# WIP stuff:


def _get_output(
    login_node_remote_runner: fabric.runners.Remote, hostname: str, cmd: str, **kwargs
):
    """TODO: WIP: Trying to get rid of the need for the `SlurmRemote` class."""
    result = login_node_remote_runner.run(
        cmd,
        # These two (echo and echo_format) do the same thing as the 'self.display'
        # method of our `Remote` class.
        echo=True,
        echo_format=T.bold_cyan(f"({hostname})" + " $ {command}"),
        in_stream=False,  # disable in_stream so tests work correctly.
        hide="stdout",  # hide stdout because we'll be printing it in colour below.
        **kwargs,
    )
    # TODO: Take a look at "stream watchers" in the fabric docs.
    assert result
    output = result.stdout.strip()
    print(T.cyan(output))
    return output


def make_get_output_fn(login_node_remote_runner: fabric.runners.Remote, hostname: str):
    return functools.partial(_get_output, login_node_remote_runner, hostname)
