from __future__ import annotations

import logging
import subprocess
import textwrap

import pytest

from milatools.utils.disk_quota import (
    _parse_diskusage_report_output,
    _parse_lfs_quota_output,
    check_disk_quota,
)
from milatools.utils.local import Local
from milatools.utils.remote import Remote

from ..integration.conftest import skip_if_not_already_logged_in


@pytest.mark.slow  # only run in integration tests.
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cluster",
    [
        "mila",
        pytest.param("tamia", marks=skip_if_not_already_logged_in("tamia")),
        pytest.param("rorqual", marks=skip_if_not_already_logged_in("rorqual")),
        pytest.param("nibi", marks=skip_if_not_already_logged_in("nibi")),
        pytest.param(
            "narval",
            marks=[
                pytest.mark.xfail(
                    reason="Getting a weird error with lfs quota (number in brackets) on Narval"
                ),
                skip_if_not_already_logged_in("narval"),
            ],
        ),
    ],
    indirect=True,
)
async def test_check_disk_quota(
    login_node_session: Remote,
    caplog: pytest.LogCaptureFixture,
):
    # TODO: Figure out a way to actually test this, (not just by running it and
    # expecting no errors).
    # Check that it doesn't raise any errors.
    # IF the quota is nearly met, then a warning is logged.
    # IF the quota is met, then a `MilatoolsUserError` is logged.
    if (
        login_node_session.hostname.startswith("graham")
        or login_node_session.hostname == "localhost"
    ):
        with pytest.raises(subprocess.CalledProcessError):
            await check_disk_quota(login_node_session)

    else:
        with caplog.at_level(logging.DEBUG):
            await check_disk_quota(login_node_session)


def _kb_to_gb(kb: int) -> float:
    return kb / (1024**2)


@pytest.mark.parametrize(
    "output, expected",
    [
        (
            textwrap.dedent("""\
            Disk quotas for usr normandf (uid 3098083):
                 Filesystem  kbytes   quota   limit   grace   files   quota   limit   grace
            /home/n/normandf
                            79593800       0       0       -  623966       0       0       -
            uid 3098083 is using default block quota setting
            uid 3098083 is using default file quota setting
            """),
            (
                (_kb_to_gb(79593800), _kb_to_gb(0)),
                (623966, 0),
            ),
        ),
    ],
)
def test_parse_lfs_quota_output(
    output: str, expected: tuple[tuple[float, float], tuple[int, int]]
):
    result = _parse_lfs_quota_output(output)
    assert result == expected


@pytest.mark.parametrize(
    "output, expected",
    [
        (
            textwrap.dedent(
                """\
                                            Description                Space         # of files
                                  /home (user normandf)        19GiB/  25GiB         206K/ 250K
                               /scratch (user normandf)        56GiB/ 500GiB         418K/ 500K
                --
                On some clusters, a break down per user may be available by adding the option '--per_user'.
                """
            ),
            (
                (19 * 1024 / 1000, 25 * 1024 / 1000),
                (206000, 250000),
            ),
        ),
        (
            textwrap.dedent(
                """\
                                            Description                Space         # of files
                                /home (user normandf)        4942MB/  50GB          28K/ 500K
                            /scratch (user normandf)          25KB/  20TB           1 /1000K
                        /project (project def-normandf)          74KB/1000GB           3 / 500K
                --
                On some clusters, a break down per user may be available by adding the option '--per_user'.
                """
            ),
            (
                (4942 / 1024, 50),
                (28_000, 500_000),
            ),
        ),
    ],
)
def test_parse_diskusage_report_output(
    output: str, expected: tuple[tuple[float, float], tuple[int, int]]
):
    result = _parse_diskusage_report_output(output)
    assert result == expected


@pytest.mark.asyncio
async def test_check_disk_quota_fails_correctly(
    caplog: pytest.LogCaptureFixture,
):
    """Should fail appropriately when run on a system with no disk-quota or
    diskusage_report commands."""
    if (
        subprocess.getstatusoutput("type disk-quota")[0] == 0
        or subprocess.getstatusoutput("type diskusage_report")[0] == 0
    ):
        pytest.skip(
            "disk-quota or diskusage_report command is available locally, cannot test failure case."
        )

    with pytest.raises(subprocess.CalledProcessError), caplog.at_level(logging.DEBUG):
        await check_disk_quota(Local())
