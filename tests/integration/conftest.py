from __future__ import annotations

import datetime
import functools
import os
import sys
from logging import getLogger as get_logger

import pytest

from milatools.cli.remote_v1 import Remote
from milatools.utils.remote_v2 import (
    SSH_CONFIG_FILE,
    RemoteV2,
    is_already_logged_in,
)
from tests.cli.common import in_github_CI, in_self_hosted_github_CI

logger = get_logger(__name__)
JOB_NAME = "milatools_test"
WCKEY = "milatools_test"

SLURM_CLUSTER = os.environ.get(
    "SLURM_CLUSTER", "mila" if in_self_hosted_github_CI or not in_github_CI else None
)
"""The name of the slurm cluster to use for tests.

When running the tests on a dev machine, this defaults to the Mila cluster. Set to
`None` when running on the github CI.
"""

MAX_JOB_DURATION = datetime.timedelta(seconds=10)

hangs_in_github_CI = pytest.mark.skipif(
    SLURM_CLUSTER == "localhost",
    reason=(
        "TODO: Hangs in the GitHub CI (probably because it runs salloc or sbatch on a "
        "cluster with only `localhost` as a 'compute' node?)"
    ),
)


def skip_if_not_already_logged_in(cluster: str) -> pytest.MarkDecorator:
    """Skip a test if not already logged in to the cluster.

    This is useful for example if we're connecting to the DRAC cluster in unit tests and
    we only want to go through 2FA once.
    """
    return pytest.mark.skipif(
        sys.platform == "win32"
        or not SSH_CONFIG_FILE.exists()
        or not is_already_logged_in(cluster),
        reason=(
            f"Logging into {cluster} might go through 2FA. It should be done "
            "in advance."
        ),
    )


def skip_param_if_not_already_logged_in(cluster: str):
    """Skip a test if not already logged in to the cluster.

    This is useful for example if we're connecting to the DRAC cluster in unit tests and
    we only want to go through 2FA once.
    """
    return pytest.param(
        cluster,
        marks=[
            skip_if_not_already_logged_in(cluster),
        ],
    )


@functools.lru_cache
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
    logger.info(
        f"Fetching the list of SLURM accounts available on the {cluster} cluster."
    )
    if sys.platform == "win32":
        result = Remote(cluster).run(
            "sacctmgr --noheader show associations where user=$USER format=Account%50"
        )
    else:
        result = RemoteV2(cluster).run(
            "sacctmgr --noheader show associations where user=$USER format=Account%50"
        )
    accounts = [line.strip() for line in result.stdout.splitlines()]
    assert accounts
    logger.info(f"Accounts on the slurm cluster {cluster}: {accounts}")
    account = sorted(accounts)[0]
    logger.info(f"Using account {account} to launch jobs in tests.")
    return account


@pytest.fixture(scope="session")
def slurm_account(cluster: str):
    return get_slurm_account(cluster)


@pytest.fixture()
def allocation_flags(
    cluster: str, slurm_account: str, request: pytest.FixtureRequest
) -> list[str]:
    # note: thanks to lru_cache, this is only making one ssh connection per cluster.
    allocation_options = {
        "job-name": JOB_NAME,
        "wckey": WCKEY,
        "account": slurm_account,
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
    return [
        f"--{key}={value}" if value is not None else f"--{key}"
        for key, value in allocation_options.items()
    ]
