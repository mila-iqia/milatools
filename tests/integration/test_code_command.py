import re
import time
from datetime import timedelta
from logging import getLogger as get_logger

import pytest

from milatools.cli.commands import check_disk_quota, code
from milatools.cli.remote import Remote
from milatools.cli.utils import get_fully_qualified_hostname_of_compute_node

from ..cli.common import in_github_CI, skip_param_if_on_github_ci
from .conftest import SLURM_CLUSTER, hangs_in_github_CI
from .test_slurm_remote import get_recent_jobs_info_dicts

logger = get_logger(__name__)


@pytest.mark.parametrize(
    "cluster",
    [
        pytest.param(
            SLURM_CLUSTER,
            marks=[
                pytest.mark.skipif(
                    not (in_github_CI and SLURM_CLUSTER is not None),
                    reason="Only runs in the GitHub CI where localhost is a slurm cluster.",
                ),
                # todo: remove this mark once we're able to do sbatch and salloc in the
                # GitHub CI.
                hangs_in_github_CI,
            ],
        ),
        skip_param_if_on_github_ci("mila"),
        skip_param_if_on_github_ci("narval"),
        skip_param_if_on_github_ci("beluga"),
        skip_param_if_on_github_ci("cedar"),
        skip_param_if_on_github_ci("graham"),
        skip_param_if_on_github_ci("niagara"),
    ],
    indirect=True,
)
def test_check_disk_quota(login_node: Remote):  # noqa: F811
    check_disk_quota(remote=login_node)


@pytest.mark.parametrize(
    "cluster",
    [
        pytest.param(
            "localhost",
            marks=pytest.mark.skipif(
                not (in_github_CI and SLURM_CLUSTER == "localhost"),
                reason="Only runs in the GitHub CI when SLURM_CLUSTER is localhost.",
            ),
        ),
        skip_param_if_on_github_ci("mila"),
        skip_param_if_on_github_ci("narval"),
        skip_param_if_on_github_ci("beluga"),
        skip_param_if_on_github_ci("cedar"),
        skip_param_if_on_github_ci("graham"),
        skip_param_if_on_github_ci("niagara"),
    ],
    indirect=True,
)
@pytest.mark.parametrize("persist", [True, False])
def test_code(
    login_node: Remote,
    persist: bool,
    capsys: pytest.CaptureFixture,
    allocation_flags: str,
):
    home = login_node.home()
    scratch = login_node.get_output("echo $SCRATCH")
    relative_path = "bob"
    code(
        path=relative_path,
        command="echo",  # replace the usual `code` with `echo` for testing.
        persist=persist,
        job=None,
        node=None,
        alloc=allocation_flags.split(),
        cluster=login_node.hostname,  # type: ignore
    )

    # Get the output that was printed while running that command.
    # We expect our fake vscode command (with 'code' replaced with 'echo') to have been
    # executed.
    captured_output: str = capsys.readouterr().out

    # Get the job id from the output just so we can more easily check stuff with sacct
    # below.

    if persist:
        m = re.search(r"Submitted batch job ([0-9]+)", captured_output)
        assert m
        job_id = int(m.groups()[0])
    else:
        m = re.search(r"salloc: Granted job allocation ([0-9]+)", captured_output)
        assert m
        job_id = int(m.groups()[0])

    time.sleep(5)  # give a chance to sacct to update.
    recent_jobs = get_recent_jobs_info_dicts(
        since=timedelta(minutes=5),
        login_node=login_node,
        fields=("JobID", "JobName", "Node", "WorkDir", "State"),
    )
    job_id_to_job_info = {int(job_info["JobID"]): job_info for job_info in recent_jobs}
    assert job_id in job_id_to_job_info, (job_id, job_id_to_job_info)
    job_info = job_id_to_job_info[job_id]

    node = job_info["Node"]
    node_hostname = get_fully_qualified_hostname_of_compute_node(
        node, cluster=login_node.hostname
    )
    expected_line = f"(local) $ /usr/bin/echo -nw --remote ssh-remote+{node_hostname} {home}/{relative_path}"
    assert any((expected_line in line) for line in captured_output.splitlines()), (
        captured_output,
        expected_line,
    )

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
