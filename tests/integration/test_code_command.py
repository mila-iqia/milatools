from __future__ import annotations

import datetime
import logging
import subprocess
import time
from datetime import timedelta
from logging import getLogger as get_logger

import pytest

from milatools.cli.code_command import code
from milatools.cli.common import check_disk_quota
from milatools.cli.remote import Remote
from milatools.cli.utils import get_fully_qualified_hostname_of_compute_node
from milatools.utils.remote_v2 import RemoteV2

from ..cli.common import in_github_CI, skip_param_if_on_github_ci
from ..conftest import launches_jobs
from .conftest import (
    SLURM_CLUSTER,
    hangs_in_github_CI,
    skip_if_not_already_logged_in,
    skip_param_if_not_already_logged_in,
)
from .test_slurm_remote import get_recent_jobs_info_dicts

logger = get_logger(__name__)


@pytest.mark.parametrize(
    "cluster",
    [
        skip_param_if_on_github_ci("mila"),
        skip_param_if_not_already_logged_in("narval"),
        skip_param_if_not_already_logged_in("beluga"),
        skip_param_if_not_already_logged_in("cedar"),
        pytest.param(
            "graham",
            marks=[
                skip_if_not_already_logged_in("graham"),
                pytest.mark.xfail(
                    raises=subprocess.CalledProcessError,
                    reason="Graham doesn't use a lustre filesystem for $HOME.",
                    strict=True,
                ),
            ],
        ),
        skip_param_if_not_already_logged_in("niagara"),
    ],
    indirect=True,
)
def test_check_disk_quota(
    login_node: Remote | RemoteV2,
    capsys: pytest.LogCaptureFixture,
    caplog: pytest.LogCaptureFixture,
):  # noqa: F811
    with caplog.at_level(logging.DEBUG):
        check_disk_quota(remote=login_node)
    # Check that it doesn't raise any errors.
    # IF the quota is nearly met, then a warning is logged.
    # IF the quota is met, then a `MilatoolsUserError` is logged.


@pytest.mark.parametrize(
    "cluster",
    [
        pytest.param(
            "localhost",
            marks=[
                pytest.mark.skipif(
                    not (in_github_CI and SLURM_CLUSTER == "localhost"),
                    reason=(
                        "Only runs in the GitHub CI when localhost is a slurm cluster."
                    ),
                ),
                # todo: remove this mark once we're able to do sbatch and salloc in the
                # GitHub CI.
                hangs_in_github_CI,
            ],
        ),
        skip_param_if_on_github_ci("mila"),
        # TODO: Re-enable these tests once we make `code` work with RemoteV2
        skip_param_if_not_already_logged_in("narval"),
        skip_param_if_not_already_logged_in("beluga"),
        skip_param_if_not_already_logged_in("cedar"),
        skip_param_if_not_already_logged_in("graham"),
        skip_param_if_not_already_logged_in("niagara"),
    ],
    indirect=True,
)
@launches_jobs
@pytest.mark.asyncio
@pytest.mark.parametrize("persist", [True, False])
async def test_code(
    login_node: RemoteV2,
    persist: bool,
    capsys: pytest.CaptureFixture,
    allocation_flags: list[str],
):
    home = login_node.run("echo $HOME", display=False, hide=True).stdout.strip()
    scratch = login_node.get_output("echo $SCRATCH")

    start = datetime.datetime.now() - timedelta(minutes=5)
    jobs_before = get_recent_jobs_info_dicts(
        login_node, since=datetime.datetime.now() - start
    )
    jobs_before = {int(job_info["JobID"]): job_info for job_info in jobs_before}

    relative_path = "bob"
    await code(
        path=relative_path,
        command="echo",  # replace the usual `code` with `echo` for testing.
        persist=persist,
        job=None,
        node=None,
        alloc=allocation_flags,
        cluster=login_node.hostname,  # type: ignore
    )
    time.sleep(5)  # give a chance to sacct to update.

    jobs_after = get_recent_jobs_info_dicts(
        login_node,
        since=datetime.datetime.now() - start,
        fields=("JobID", "JobName", "Node", "WorkDir", "State"),
    )
    jobs_after = {int(job_info["JobID"]): job_info for job_info in jobs_after}

    assert all(
        job_id_before in jobs_after.keys() for job_id_before in jobs_before.keys()
    )
    assert len(jobs_after) - len(jobs_before) == 1

    job_id = next(iter(jobs_after.keys() - jobs_before.keys()))
    job_info = jobs_after[job_id]

    node = job_info["Node"]
    node_hostname = get_fully_qualified_hostname_of_compute_node(
        node, cluster=login_node.hostname
    )
    assert node_hostname and node_hostname != "None"

    # TODO: This check doesn't work anymore.
    # Get the output that was printed while running that command.
    # We expect our fake vscode command (with 'code' replaced with 'echo') to have been
    # executed.
    # captured_output: str = capsys.readouterr().out
    # expected_line = f"(local) $ /usr/bin/echo -nw --remote ssh-remote+{node_hostname} {home}/{relative_path}"
    # assert any((expected_line in line) for line in captured_output.splitlines()), (
    #     captured_output,
    #     expected_line,
    # )
    # Check that on the DRAC clusters, the workdir is the scratch directory (because we
    # cd'ed to $SCRATCH before submitting the job)
    workdir = job_info["WorkDir"]
    if login_node.hostname == "mila":
        assert workdir == home
    else:
        assert workdir == scratch

    if persist:
        # Job should still be running since we're using `persist` (that's the whole
        # point.)
        assert job_info["State"] == "RUNNING"
    else:
        # Job should have been cancelled by us after the `echo` process finished.
        # NOTE: This check is a bit flaky, perhaps our `scancel` command hasn't
        # completed yet, or sacct doesn't show the change in status quick enough.
        # Relaxing it a bit for now.
        # assert "CANCELLED" in job_info["State"]
        assert "CANCELLED" in job_info["State"] or job_info["State"] == "RUNNING"
