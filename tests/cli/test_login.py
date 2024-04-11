import textwrap
from logging import getLogger as get_logger
from pathlib import Path

import pytest

from milatools.cli.login import login
from milatools.utils.remote_v2 import SSH_CACHE_DIR, RemoteV2

from .common import requires_ssh_to_localhost

logger = get_logger(__name__)


@requires_ssh_to_localhost
@pytest.mark.asyncio
async def test_login(tmp_path: Path):  # ssh_config_file: Path):
    assert SSH_CACHE_DIR.exists()
    ssh_config_path = tmp_path / "ssh_config"
    ssh_config_path.write_text(
        textwrap.dedent(
            """\
            Host foo
                hostname localhost
            Host bar
                hostname localhost
            """
        )
        + "\n"
    )

    # Should create a connection to every host in the ssh config file.
    remotes = await login(ssh_config_path=ssh_config_path)
    assert all(isinstance(remote, RemoteV2) for remote in remotes)
    assert set(remote.hostname for remote in remotes) == {"foo", "bar"}
    for remote in remotes:
        logger.info(f"Removing control socket at {remote.control_path}")
        remote.control_path.unlink()
