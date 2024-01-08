"""Tests that use an actual SLURM cluster.

The cluster to use can be specified by setting the SLURM_CLUSTER environment variable.
During the CI on GitHub, a small local slurm cluster is setup with a GitHub Action, and
SLURM_CLUSTER is set to "localhost".
"""
from __future__ import annotations

import functools
import os
import time
from logging import getLogger as get_logger

import fabric.runners
import pytest

from milatools.cli.remote import Remote, SlurmRemote
from milatools.cli.utils import T

logger = get_logger(__name__)

SLURM_CLUSTER = os.environ.get("SLURM_CLUSTER")
JOB_NAME = "mila-tools"
WCKEY = "milatools_test"

pytestmark = pytest.mark.timeout(60)

requires_access_to_slurm_cluster = pytest.mark.skipif(
    not SLURM_CLUSTER,
    reason="Requires ssh access to a SLURM cluster.",
)


@pytest.fixture(scope="module", autouse=True)
def cancel_all_milatools_jobs_after_tests(remote: Remote):
    yield
    remote.run(f"scancel -u $USER --wckey={WCKEY}")


@pytest.fixture(scope="session")
def remote():
    if not SLURM_CLUSTER:
        pytest.skip("Requires ssh access to a SLURM cluster.")
    return Remote(SLURM_CLUSTER)


@pytest.fixture(scope="session")
def account(remote: Remote):
    """Fixture that gets an account to be used for test jobs on the slurm cluster."""
    accounts = remote.get_output(
        "sacctmgr --noheader show associations where user=$USER format=Account%100",
    ).split()
    logger.info(f"Accounts on the slurm cluster {remote.hostname}: {accounts}")
    assert accounts
    account = accounts[0]
    logger.info(f"Using account {account} for jobs launched in tests.")
    return account


@pytest.fixture(scope="session")
def allocation_flags(account: str):
    return (
        f"--job-name={JOB_NAME} --wckey={WCKEY} --account={account} --nodes=1 "
        f"--ntasks=1 --cpus-per-task=1 --mem=1G --time=00:01:00"
    )


@requires_access_to_slurm_cluster
def test_srun(remote: Remote, allocation_flags: str):
    """Checks that `srun` works on the slurm cluster with the parametrized flags."""
    output = remote.get_output(f"srun {allocation_flags} hostname")
    assert output
    assert "not found" not in output
    print(output)


@pytest.fixture
def interactive_slurm_remote(remote: Remote, allocation_flags: str):
    return SlurmRemote(
        connection=remote.connection,
        alloc=allocation_flags.split(),
    )


@requires_access_to_slurm_cluster
def test_ensure_allocation_salloc(interactive_slurm_remote: SlurmRemote):
    # TODO: The SlurmRemote class is quite confusing: Is it a connection to the compute
    # node? Or is it a connection to the login node?

    # Actually runs the `salloc` command. Unclear if the connection to the compute node
    # is saved in `proc`..
    data, login_node_remote_runner = interactive_slurm_remote.ensure_allocation()
    assert isinstance(login_node_remote_runner, fabric.runners.Remote)
    print(data)
    # WIP: Uncomment to use the WIP `get_output` equivalent without any additional
    # classes. `Remote`
    # run = make_get_output_fn(
    #     login_node_remote_runner, interactive_slurm_remote.hostname
    # )

    run = interactive_slurm_remote.get_output

    hostname_from_salloc_output_on_login_node = data["node_name"]

    time.sleep(5)  # seems like squeue doesn't update quite fast enough sometimes.
    squeue_output = run("squeue --me")
    print("Got this output from squeue:", squeue_output)

    # user = _run("whoami")
    # hostname = _run("hostname")
    assert hostname_from_salloc_output_on_login_node in squeue_output


@pytest.fixture
def batch_slurm_remote(remote: Remote, allocation_flags: str):
    return SlurmRemote(
        connection=remote.connection,
        alloc=allocation_flags.split(),
        persist=True,
    )


@pytest.mark.timeout(60)
@requires_access_to_slurm_cluster
def test_ensure_allocation_sbatch(batch_slurm_remote: SlurmRemote):
    # TODO: The SlurmRemote class is quite confusing: Is it a connection to the compute
    # node? Or is it a connection to the login node?

    job_data, login_node_remote_runner = batch_slurm_remote.ensure_allocation()
    print(job_data, login_node_remote_runner)

    assert isinstance(login_node_remote_runner, fabric.runners.Remote)

    node_name_from_sbatch_extract = job_data["node_name"]
    assert "jobid" in job_data
    job_id_from_sbatch_extract = job_data["jobid"]

    time.sleep(5)  # Let enough time for squeue to update.
    squeue_output = batch_slurm_remote.run("squeue --me")
    assert squeue_output
    squeue_output = squeue_output.stdout.strip()
    print(squeue_output)

    assert node_name_from_sbatch_extract in squeue_output
    assert job_id_from_sbatch_extract in squeue_output


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
