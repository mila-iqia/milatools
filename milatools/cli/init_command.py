from __future__ import annotations

import difflib
from logging import getLogger as get_logger
from pathlib import Path
import sys

import questionary as qn

from .utils import SSHConfig, T, yn

logger = get_logger(__name__)


def setup_ssh_config(
    ssh_config_path: str | Path = "~/.ssh/config",
):
    """Interactively sets up some useful entries in the ~/.ssh/config file on the local machine.

    Exits if the User cancels any of the prompts or doesn't confirm the changes when asked.

    Entries:
    - "mila": Used to connect to a login node.
    - "mila-cpu": Used to connect to a compute node.

    Other entries:
    - "*.server.mila.quebec !*login.server.mila.quebec": Sets some useful attributes for connecting
      directly to compute nodes.

    TODO: Also ask if we should add entries for the ComputeCanada/DRAC clusters.
    """

    ssh_config_path = _setup_ssh_config_file(ssh_config_path)
    ssh_config = SSHConfig(ssh_config_path)
    username: str = _get_username(ssh_config)
    orig_config = ssh_config.cfg.config()

    control_path_dir = Path("~/.cache/ssh")
    # note: a bit nicer to keep the "~" in the path in the ssh config file, but we need to make
    # sure that the directory actually exists.
    control_path_dir.expanduser().mkdir(exist_ok=True, parents=True)

    if sys.platform == "win32":
        ssh_multiplexing_config = {}
    else:
        ssh_multiplexing_config = {
            # Tries to reuse an existing connection, but if it fails, it will create a new one.
            "ControlMaster": "auto",
            # This makes a file per connection, like normandf@login.server.mila.quebec:2222
            "ControlPath": str(control_path_dir / r"%r@%h:%p"),
            # persist for 10 minutes after the last connection ends.
            "ControlPersist": 600,
        }

    _add_ssh_entry(
        ssh_config,
        host="mila",
        Host=None,
        HostName="login.server.mila.quebec",
        User=username,
        PreferredAuthentications="publickey,keyboard-interactive",
        Port=2222,
        ServerAliveInterval=120,
        ServerAliveCountMax=5,
        **ssh_multiplexing_config,
    )

    _add_ssh_entry(
        ssh_config,
        "mila-cpu",
        User=username,
        Port=2222,
        ForwardAgent="yes",
        StrictHostKeyChecking="no",
        LogLevel="ERROR",
        UserKnownHostsFile="/dev/null",
        RequestTTY="force",
        ConnectTimeout=600,
        ServerAliveInterval=120,
        # NOTE: will not work with --gres prior to Slurm 22.05, because srun --overlap cannot share
        # it
        ProxyCommand=(
            'ssh mila "/cvmfs/config.mila.quebec/scripts/milatools/slurm-proxy.sh mila-cpu --mem=8G"'
        ),
        RemoteCommand="/cvmfs/config.mila.quebec/scripts/milatools/entrypoint.sh mila-cpu",
    )

    # Check for *.server.mila.quebec in ssh config, to connect to compute nodes

    old_cnode_pattern = "*.server.mila.quebec"
    cnode_pattern = "*.server.mila.quebec !*login.server.mila.quebec"

    if old_cnode_pattern in ssh_config.hosts():
        if yn(
            "The '*.server.mila.quebec' entry in ~/.ssh/config is too general and should "
            "exclude login.server.mila.quebec. Fix this?"
        ):
            if cnode_pattern in ssh_config.hosts():
                ssh_config.remove(old_cnode_pattern)
            else:
                ssh_config.rename(old_cnode_pattern, cnode_pattern)
    else:
        _add_ssh_entry(
            ssh_config,
            cnode_pattern,
            HostName="%h",
            User=username,
            ProxyJump="mila",
            **ssh_multiplexing_config,
        )

    new_config = ssh_config.cfg.config()
    if orig_config == new_config:
        print("Did not change ssh config")
    elif not _confirm_changes(ssh_config, previous=orig_config):
        exit("Did not change ssh config")
    else:
        ssh_config.save()
        print(f"Wrote {ssh_config_path}")


def _setup_ssh_config_file(config_file_path: str | Path) -> Path:
    # Save the original value for the prompt. (~/.ssh/config looks better on the command-line).
    filename_for_prompt = config_file_path

    config_file = Path(config_file_path).expanduser()
    if not config_file.exists() and not yn(
        f"There is no {filename_for_prompt} file. Create one?"
    ):
        exit("No ssh configuration file was found.")

    ssh_dir = config_file.parent
    if not ssh_dir.exists():
        ssh_dir.mkdir(mode=0o700, exist_ok=True)
        print(f"Created the ssh directory at {ssh_dir}")
    elif ssh_dir.stat().st_mode & 0o777 != 0o700:
        ssh_dir.chmod(mode=0o700)
        print(f"Fixed the permissions on ssh directory at {ssh_dir} to 700")

    if not config_file.exists():
        config_file.touch(mode=0o600)
        print(f"Created {config_file}")
        return config_file
    # Fix any permissions issues:
    if config_file.stat().st_mode & 0o777 != 0o600:
        config_file.chmod(mode=0o600)
        print(f"Fixing permissions on {config_file} to 600")
        return config_file

    return config_file


def _confirm_changes(ssh_config: SSHConfig, previous: str) -> bool:
    print(T.bold("The following modifications will be made to your ~/.ssh/config:\n"))
    diff_lines = list(
        difflib.unified_diff(
            (previous + "\n").splitlines(True),
            (ssh_config.cfg.config() + "\n").splitlines(True),
        )
    )
    for line in diff_lines[2:]:
        if line.startswith("-"):
            print(T.red(line), end="")
        elif line.startswith("+"):
            print(T.green(line), end="")
        else:
            print(line, end="")
    return yn("\nIs this OK?")


def _get_username(ssh_config: SSHConfig) -> str:
    # Check for a mila entry in ssh config
    # NOTE: This also supports the case where there's a 'HOST mila some_alias_for_mila' entry.
    # NOTE: ssh_config.host(entry) returns an empty dictionary if there is no entry.
    username: str | None = None
    hosts_with_mila_in_name_and_a_user_entry = [
        host
        for host in ssh_config.hosts()
        if "mila" in host.split() and "user" in ssh_config.host(host)
    ]
    # Note: If there are none, or more than one, then we'll ask the user for their username, just
    # to be sure.
    if len(hosts_with_mila_in_name_and_a_user_entry) == 1:
        username = ssh_config.host(hosts_with_mila_in_name_and_a_user_entry[0]).get(
            "user"
        )

    while not username:
        username = qn.text(
            "What's your username on the mila cluster?\n",
            validate=_is_valid_username,
        ).unsafe_ask()
    return username.strip()


def _is_valid_username(text: str) -> bool | str:
    return (
        "Please enter your username on the mila cluster."
        if not text or text.isspace()
        else True
    )


# NOTE: Later, if we think it can be useful, we could use some fancy TypedDict for the SSH entries.
# from .ssh_config_entry import SshConfigEntry
# from typing_extensions import Unpack


def _add_ssh_entry(
    ssh_config: SSHConfig,
    host: str,
    Host: str | None = None,
    *,
    _space_before: bool = True,
    _space_after: bool = False,
    **entry,
) -> None:
    """Interactively add an entry to the ssh config file.

    Exits if the user doesn't want to add an entry or doesn't confirm the change.

    Returns whether the changes to `ssh_config` need to be saved later using `ssh_config.save()`.
    """
    # NOTE: `Host` is also a parameter to make sure it isn't in `entry`.
    assert not (host and Host)
    host = Host or host
    if host in ssh_config.hosts():
        existing_entry = ssh_config.host(host)
        existing_entry.update(entry)
        ssh_config.cfg.set(host, **existing_entry)
        logger.debug(f"Updated {host} entry in ssh config.")
    else:
        ssh_config.add(
            host,
            _space_before=_space_before,
            _space_after=_space_after,
            **entry,
        )
        logger.debug(f"Adding new {host} entry in ssh config.")
