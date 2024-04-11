from __future__ import annotations

import asyncio
from pathlib import Path

from paramiko import SSHConfig

from milatools.cli import console
from milatools.utils.remote_v2 import SSH_CONFIG_FILE, RemoteV2


async def login(
    ssh_config_path: Path = SSH_CONFIG_FILE,
) -> list[RemoteV2]:
    """Logs in and sets up reusable SSH connections to all the hosts in the SSH config.

    Returns the list of remotes where the connection was successfully established.
    """
    ssh_config = SSHConfig.from_path(str(ssh_config_path.expanduser()))
    potential_clusters = [
        host
        for host in ssh_config.get_hostnames()
        if not any(c in host for c in ["*", "?", "!"])
    ]
    # take out entries like `mila-cpu` that have a proxy and remote command.
    potential_clusters = [
        hostname
        for hostname in potential_clusters
        if not (
            (config := ssh_config.lookup(hostname)).get("proxycommand")
            and config.get("remotecommand")
        )
    ]
    remotes = await asyncio.gather(
        *(
            RemoteV2.connect(hostname, ssh_config_path=ssh_config_path)
            for hostname in potential_clusters
        ),
        return_exceptions=True,
    )
    remotes = [remote for remote in remotes if isinstance(remote, RemoteV2)]
    console.log(f"Successfully connected to {[remote.hostname for remote in remotes]}")
    return remotes


if __name__ == "__main__":
    asyncio.run(login())
