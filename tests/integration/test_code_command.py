from datetime import timedelta
from logging import getLogger as get_logger

import pytest

from milatools.cli.commands import check_disk_quota, code
from milatools.cli.remote import Remote
from tests.integration.test_slurm_remote import (
    get_recent_jobs_info_dicts,
    requires_access_to_slurm_cluster,
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
    persist: bool,
    capsys: pytest.CaptureFixture,
    cancel_jobs_after_tests,
):
    home = login_node.home()
    scratch = login_node.get_output("echo $SCRATCH")
    relative_path = "bob"
    code(
        path=relative_path,
        command="echo",
        persist=persist,
        job=None,
        node=None,
        alloc=["--time=00:00:10"],
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
    assert any(
        (
            f"(local) $ /usr/bin/echo -nw --remote "
            f"ssh-remote+{most_recent_job['Node']}.server.mila.quebec {home}/{relative_path}"
            in line
        )
        for line in output.splitlines()
    ), output.splitlines()

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
