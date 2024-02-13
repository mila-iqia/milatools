from datetime import timedelta
from logging import getLogger as get_logger

import fabric
import paramiko
import pytest

from milatools.cli.commands import check_disk_quota, code
from milatools.cli.remote import Remote
from tests.cli.test_slurm_remote import (
    get_recent_jobs_info,
    get_recent_jobs_info_dicts,
    requires_access_to_slurm_cluster,  # noqa: F401
)

logger = get_logger(__name__)


@requires_access_to_slurm_cluster
def test_check_disk_quota(login_node: Remote):  # noqa: F811
    check_disk_quota(remote=login_node)


@pytest.fixture(scope="function")
def cancel_jobs_after_tests(login_node: Remote):
    yield
    login_node.run("scancel -u $USER --name mila-code", display=True)


@requires_access_to_slurm_cluster
@pytest.mark.parametrize("persist", [True, False])
def test_code_persist(
    login_node: Remote,
    cancel_jobs_after_tests,
    persist: bool,
    capsys: pytest.CaptureFixture,
):
    home = login_node.home()
    scratch = login_node.get_output("echo $SCRATCH")

    code(
        path=".",
        command="echo",
        persist=persist,
        job=None,
        node=None,
        alloc=["--time=00:01:00"],
        cluster=login_node.hostname,  # type: ignore
    )

    recent_job_info = get_recent_jobs_info_dicts(
        since=timedelta(minutes=1),
        login_node=login_node,
        fields=("JobID", "JobName", "Node", "WorkDir", "State"),
    )

    assert recent_job_info
    most_recent_job = recent_job_info[0]

    output: str = capsys.readouterr().out
    assert (
        f"(local) $ /usr/bin/echo -nw --remote ssh-remote+{most_recent_job['Node']}.server.mila.quebec {home}"
        in output
    )

    workdir = most_recent_job["WorkDir"]
    if login_node.hostname == "mila":
        assert workdir == home
    else:
        assert workdir == scratch

    if persist:
        # Job should still be running.
        assert most_recent_job["State"] == "RUNNING"
    else:
        # Job should have been cancelled by us after the `echo` process finished.
        assert "CANCELLED" in most_recent_job["State"]
