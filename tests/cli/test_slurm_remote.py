from __future__ import annotations

import os

import pytest
from fabric.connection import Connection

from milatools.cli.remote import Remote, SlurmRemote

SERVER_NAME = os.environ.get("SERVER_NAME", "mila")

CONN_TO_SLURM_CLUSTER_AVAILABLE = False
try:
    connection = Connection(SERVER_NAME)
    CONN_TO_SLURM_CLUSTER_AVAILABLE = connection.run("which srun").stdout != ""
except Exception:
    CONN_TO_SLURM_CLUSTER_AVAILABLE = False

requires_access_to_slurm_cluster = pytest.mark.skipif(
    not CONN_TO_SLURM_CLUSTER_AVAILABLE,
    reason="Requires ssh access to a SLURM cluster.",
)


@pytest.fixture
def remote():
    return Remote(hostname=SERVER_NAME)


@pytest.fixture
def slurm_remote(remote: Remote):
    job_name = "mila-tools"
    return SlurmRemote(
        connection=remote.connection,
        alloc=[
            "-J",
            job_name,
            "--nodes=1",
            "--ntasks=1",
            "--cpus-per-task=1",
            "--mem=1G",
            "--time=00:01:00",
        ],
    )


@requires_access_to_slurm_cluster
def test_ensure_allocation(slurm_remote: SlurmRemote):
    # TODO: Really confusing: What exactly is SlurmRemote? Is it a connection to the
    # compute node? Or is it a connection to the login node?
    data, proc = slurm_remote.ensure_allocation()
    # {"node_name": node_name, "jobid": results["jobid"]}, proc
    print(data, proc)


@requires_access_to_slurm_cluster
def test_can_launch_srun(remote: Remote):
    output = remote.get_output(
        "srun --nodes=1 --ntasks=1 --cpus-per-task=1 --mem=1G --time=00:01:00 hostname"
    )
    print(output)
    # assert False, output
