from __future__ import annotations

import inspect
import logging
import subprocess
from typing import Callable

import pytest

from milatools.utils.disk_quota import check_disk_quota, check_disk_quota_v1
from milatools.utils.remote_v1 import RemoteV1
from milatools.utils.remote_v2 import RemoteV2


@pytest.mark.slow
@pytest.mark.asyncio
@pytest.mark.parametrize("check_disk_quota_fn", [check_disk_quota, check_disk_quota_v1])
async def test_check_disk_quota(
    login_node_v2: RemoteV2,
    caplog: pytest.LogCaptureFixture,
    check_disk_quota_fn: Callable[[RemoteV1 | RemoteV2], None],
):
    # TODO: Figure out a way to actually test this, (not just by running it and
    # expecting no errors).
    # Check that it doesn't raise any errors.
    # IF the quota is nearly met, then a warning is logged.
    # IF the quota is met, then a `MilatoolsUserError` is logged.
    async def _check_disk_quota():
        if inspect.iscoroutinefunction(check_disk_quota_fn):
            await check_disk_quota_fn(login_node_v2)
        else:
            check_disk_quota_fn(login_node_v2)

    if (
        login_node_v2.hostname.startswith("graham")
        or login_node_v2.hostname == "localhost"
    ):
        with pytest.raises(subprocess.CalledProcessError):
            await _check_disk_quota()

    else:
        with caplog.at_level(logging.DEBUG):
            await _check_disk_quota()
