from __future__ import annotations

import datetime
import os
import sys
from logging import getLogger as get_logger

import pytest

from milatools.cli.utils import SSH_CONFIG_FILE
from milatools.utils.remote import Remote, is_already_logged_in

logger = get_logger(__name__)
JOB_NAME = "milatools_test"
WCKEY = "milatools_test"

SLURM_CLUSTER = os.environ.get("SLURM_CLUSTER")
"""The name of the slurm cluster(s) to use for tests.

When running the tests on a dev machine, this defaults to the Mila cluster. Set to
`None` when running on the github CI.
"""

MAX_JOB_DURATION = datetime.timedelta(minutes=5)

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
        or not is_already_logged_in(cluster, ssh_config_path=SSH_CONFIG_FILE),
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


def get_recent_jobs_info(
    login_node: Remote,
    since: datetime.timedelta = datetime.timedelta(minutes=5),
    fields: tuple[str, ...] = ("JobID", "JobName", "Node", "State"),
) -> list[tuple[str, ...]]:
    """Returns a list of fields for jobs that started recently."""
    lines = login_node.run(
        f"sacct --noheader --allocations --user=$USER "
        f"--starttime=now-{int(since.total_seconds())}seconds "
        "--format=" + ",".join(f"{field}%100" for field in fields),
        display=False,
        hide=True,
    ).stdout.splitlines()
    # note: using maxsplit because the State field can contain spaces: "canceled by ..."
    return [tuple(line.strip().split(maxsplit=len(fields) - 1)) for line in lines]


def get_recent_jobs_info_dicts(
    login_node: Remote,
    since: datetime.timedelta = datetime.timedelta(minutes=5),
    fields: tuple[str, ...] = ("JobID", "JobName", "Node", "State"),
) -> list[dict[str, str]]:
    return [
        dict(zip(fields, line))
        for line in get_recent_jobs_info(login_node, since=since, fields=fields)
    ]
