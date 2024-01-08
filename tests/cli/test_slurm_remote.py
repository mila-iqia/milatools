from __future__ import annotations

import os
from logging import getLogger as get_logger

import pytest

from milatools.cli.remote import Remote, SlurmRemote

from .common import requires_s_flag

logger = get_logger(__name__)

SLURM_CLUSTER = os.environ.get("SLURM_CLUSTER")

requires_access_to_slurm_cluster = pytest.mark.skipif(
    not SLURM_CLUSTER,
    reason="Requires ssh access to a SLURM cluster.",
)


@pytest.fixture(scope="session")
def remote():
    if not SLURM_CLUSTER:
        pytest.skip("Requires ssh access to a SLURM cluster.")
    return Remote(SLURM_CLUSTER)


@pytest.fixture
def slurm_remote(remote: Remote):
    job_name = "mila-tools"

    accounts = remote.get_output(
        "sacctmgr -n show associations where user=$USER format=Account%100"
    ).split()
    logger.info(f"Accounts on the slurm cluster {remote.hostname}: {accounts}")

    assert accounts
    account = accounts[0]
    # Maybe select the account with highest priority?

    return SlurmRemote(
        connection=Remote,
        alloc=[
            "-J",
            job_name,
            f"--account={account}",
            "--nodes=1",
            "--ntasks=1",
            "--cpus-per-task=1",
            "--mem=1G",
            "--time=00:01:00",
        ],
    )


@requires_s_flag
@requires_access_to_slurm_cluster
def test_ensure_allocation(slurm_remote: SlurmRemote):
    # TODO: The SlurmRemote class is quite confusing: Is it a connection to the compute
    # node? Or is it a connection to the login node?

    # Actually runs the `salloc` command. Unclear if the connection to the compute node
    # is saved in `proc`..
    data, proc = slurm_remote.ensure_allocation()

    hostname = data["node_name"]

    # {"node_name": node_name, "jobid": results["jobid"]}, proc
    print(data, proc, hostname)


@requires_s_flag
@requires_access_to_slurm_cluster
def test_can_launch_srun(remote: Remote):
    output = remote.get_output(
        "srun --nodes=1 --ntasks=1 --cpus-per-task=1 --mem=1G --time=00:01:00 hostname"
    )
    print(output)
    # assert False, output
