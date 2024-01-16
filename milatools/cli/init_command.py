from __future__ import annotations

import copy
import difflib
import json
import shlex
import shutil
import subprocess
import sys
import warnings
from logging import getLogger as get_logger
from pathlib import Path
from typing import Any

import questionary as qn

from .local import Local
from .utils import SSHConfig, T, running_inside_WSL, yn
from .vscode_utils import (
    get_expected_vscode_settings_json_path,
    vscode_installed,
)

logger = get_logger(__name__)

WINDOWS_UNSUPPORTED_KEYS = ["ControlMaster", "ControlPath", "ControlPersist"]


if sys.platform == "win32":
    ssh_multiplexing_config = {}
else:
    ssh_multiplexing_config = {
        # Tries to reuse an existing connection, but if it fails, it will create a
        # new one.
        "ControlMaster": "auto",
        # This makes a file per connection, like
        # normandf@login.server.mila.quebec:2222
        "ControlPath": r"~/.cache/ssh/%r@%h:%p",
        # persist for 10 minutes after the last connection ends.
        "ControlPersist": 600,
    }


MILA_ENTRIES: dict[str, dict[str, int | str]] = {
    "mila": {
        "HostName": "login.server.mila.quebec",
        # "User": mila_username,
        "PreferredAuthentications": "publickey,keyboard-interactive",
        "Port": 2222,
        "ServerAliveInterval": 120,
        "ServerAliveCountMax": 5,
        **ssh_multiplexing_config,
    },
    "mila-cpu": {
        # "User": mila_username,
        "Port": 2222,
        "ForwardAgent": "yes",
        "StrictHostKeyChecking": "no",
        "LogLevel": "ERROR",
        "UserKnownHostsFile": "/dev/null",
        "RequestTTY": "force",
        "ConnectTimeout": 600,
        "ServerAliveInterval": 120,
        # NOTE: will not work with --gres prior to Slurm 22.05, because srun --overlap
        # cannot share gpus
        "ProxyCommand": (
            'ssh mila "/cvmfs/config.mila.quebec/scripts/milatools/slurm-proxy.sh '
            'mila-cpu --mem=8G"'
        ),
        "RemoteCommand": (
            "/cvmfs/config.mila.quebec/scripts/milatools/entrypoint.sh mila-cpu"
        ),
    },
    "*.server.mila.quebec !*login.server.mila.quebec": {
        "HostName": "%h",
        # "User": mila_username,
        "ProxyJump": "mila",
        **ssh_multiplexing_config,
    },
}
DRAC_ENTRIES: dict[str, dict[str, int | str]] = {
    "beluga cedar graham narval niagara": {
        "Hostname": "%h.computecanada.ca",
        # User=drac_username,
        **ssh_multiplexing_config,
    },
    "!beluga  bc????? bg????? bl?????": {
        "ProxyJump": "beluga",
        # User=drac_username,
    },
    "!cedar   cdr? cdr?? cdr??? cdr????": {
        "ProxyJump": "cedar",
        # User=drac_username,
    },
    "!graham  gra??? gra????": {
        "ProxyJump": "graham",
        # User=drac_username,
    },
    "!narval  nc????? ng?????": {
        "ProxyJump": "narval",
        # User=drac_username,
    },
    "!niagara nia????": {
        "ProxyJump": "niagara",
        # User=drac_username,
    },
}


def setup_ssh_config(
    ssh_config_path: str | Path = "~/.ssh/config",
) -> SSHConfig:
    """Interactively sets up some useful entries in the ~/.ssh/config file on the local
    machine.

    Exits if the User cancels any of the prompts or doesn't confirm the changes when
    asked.

    Entries:
    - "mila": Used to connect to a login node.
    - "mila-cpu": Used to connect to a compute node.

    Other entries:
    - "*.server.mila.quebec !*login.server.mila.quebec": Sets some useful attributes for
      connecting directly to compute nodes.

    Returns:
        The resulting SSHConfig if the changes are approved.
    """

    ssh_config_path = _setup_ssh_config_file(ssh_config_path)
    ssh_config = SSHConfig(ssh_config_path)
    mila_username: str = _get_mila_username(ssh_config)
    drac_username: str | None = _get_drac_username(ssh_config)
    orig_config = ssh_config.cfg.config()

    for hostname, entry in MILA_ENTRIES.copy().items():
        entry.update(User=mila_username)
        _add_ssh_entry(ssh_config, hostname, entry)
        _make_controlpath_dir(entry)

    if drac_username:
        logger.debug(
            f"Adding entries for the ComputeCanada/DRAC clusters to {ssh_config_path}."
        )
        for hostname, entry in DRAC_ENTRIES.copy().items():
            entry.update(User=drac_username)
            _add_ssh_entry(ssh_config, hostname, entry)
            _make_controlpath_dir(entry)

    # Check for *.server.mila.quebec in ssh config, to connect to compute nodes
    old_cnode_pattern = "*.server.mila.quebec"

    if old_cnode_pattern in ssh_config.hosts():
        logger.info(
            f"The '{old_cnode_pattern}' entry in ~/.ssh/config is too general and "
            "should exclude login.server.mila.quebec. Fixing this."
        )
        ssh_config.remove(old_cnode_pattern)

    new_config = ssh_config.cfg.config()
    if orig_config == new_config:
        print("Did not change ssh config")
    elif not _confirm_changes(ssh_config, previous=orig_config):
        exit("Did not change ssh config")
    else:
        ssh_config.save()
        print(f"Wrote {ssh_config_path}")
    return ssh_config


def setup_windows_ssh_config_from_wsl(linux_ssh_config: SSHConfig):
    """Setup the Windows SSH configuration and public key from within WSL.

    This copies over the entries from the linux ssh configuration file, except for the
    values that aren't supported on Windows (e.g. "ControlMaster").

    This also copies the public key file from the linux SSH directory over to the
    Windows SSH directory if it isn't already present.

    This makes it so the user doesn't need to install Python/Anaconda on the Windows
    side in order to use `mila code` from within WSL.
    """
    assert running_inside_WSL()
    # NOTE: This also assumes that a public/private key pair has already been generated
    # at ~/.ssh/id_rsa.pub and ~/.ssh/id_rsa.
    windows_home = get_windows_home_path_in_wsl()
    windows_ssh_config_path = windows_home / ".ssh/config"
    windows_ssh_config_path = _setup_ssh_config_file(windows_ssh_config_path)

    windows_ssh_config = SSHConfig(windows_ssh_config_path)

    initial_windows_config_contents = windows_ssh_config.cfg.config()
    _copy_valid_ssh_entries_to_windows_ssh_config_file(
        linux_ssh_config, windows_ssh_config
    )
    new_windows_config_contents = windows_ssh_config.cfg.config()

    if (
        new_windows_config_contents != initial_windows_config_contents
        and _confirm_changes(windows_ssh_config, initial_windows_config_contents)
    ):
        # We made changes and they were accepted.
        windows_ssh_config.save()
    else:
        print(f"Did not change ssh config at path {windows_ssh_config.path}")
        return  # also skip copying the SSH keys.

    # Copy the SSH key to the windows folder so that passwordless SSH also works on
    # Windows.
    # TODO: This will need to change if we support using a non-default location at some
    # point.
    linux_private_key_file = Path.home() / ".ssh/id_rsa"
    windows_private_key_file = windows_home / ".ssh/id_rsa"

    for linux_key_file, windows_key_file in [
        (linux_private_key_file, windows_private_key_file),
        (
            linux_private_key_file.with_suffix(".pub"),
            windows_private_key_file.with_suffix(".pub"),
        ),
    ]:
        _copy_if_needed(linux_key_file, windows_key_file)


def _copy_if_needed(linux_key_file: Path, windows_key_file: Path):
    if linux_key_file.exists() and not windows_key_file.exists():
        print(
            f"Copying {linux_key_file} over to the Windows ssh folder at "
            f"{windows_key_file}."
        )
        shutil.copy2(src=linux_key_file, dst=windows_key_file)


def get_windows_home_path_in_wsl() -> Path:
    assert running_inside_WSL()
    windows_username = subprocess.getoutput("powershell.exe '$env:UserName'").strip()
    return Path(f"/mnt/c/Users/{windows_username}")


def create_ssh_keypair(ssh_private_key_path: Path, local: Local) -> None:
    local.run(
        *shlex.split(
            f'ssh-keygen -f {shlex.quote(str(ssh_private_key_path))} -t rsa -N=""'
        ),
    )


def setup_vscode_settings():
    print("Setting up VsCode settings for Remote development.")

    # TODO: Could also change some other useful settings as needed.

    # For example, we could skip a prompt if we had the qualified node name:
    # remote_platform = settings_json.get("remote.SSH.remotePlatform", {})
    # remote_platform.setdefault(fully_qualified_node_name, "linux")
    # settings_json["remote.SSH.remotePlatform"] = remote_platform
    if not vscode_installed():
        # Display a message inviting the user to install VsCode:
        warnings.warn(
            T.orange(
                "Visual Studio Code doesn't seem to be installed on your machine "
                "(either that, or the `code` command is not available on the "
                "command-line.)\n"
                "We would recommend installing Visual Studio Code if you want to "
                "easily edit code on the cluster with the `mila code` command. "
            )
        )
        return
    vscode_settings_json_path = get_expected_vscode_settings_json_path()
    try:
        _update_vscode_settings_json(
            vscode_settings_json_path, new_values={"remote.SSH.connectTimeout": 60}
        )
    except Exception as err:
        logger.warning(
            T.orange(
                f"Unable to setup VsCode settings file at {vscode_settings_json_path} "
                f"for remote development.\n"
                f"Skipping and leaving the settings unchanged."
            ),
            exc_info=err,
        )


def _update_vscode_settings_json(
    vscode_settings_json_path: Path, new_values: dict[str, Any]
) -> None:
    settings_json: dict[str, Any] = {}
    if vscode_settings_json_path.exists():
        logger.info(f"Reading VsCode settings from {vscode_settings_json_path}")
        with open(vscode_settings_json_path) as f:
            settings_json = json.loads(
                "\n".join(
                    line for line in f.readlines() if not line.strip().startswith("#")
                )
            )

    settings_before = copy.deepcopy(settings_json)
    settings_json.update(
        {k: v for k, v in new_values.items() if k not in settings_json}
    )

    if settings_json == settings_before or not ask_to_confirm_changes(
        before=json.dumps(settings_before, indent=4),
        after=json.dumps(settings_json, indent=4),
        path=vscode_settings_json_path,
    ):
        print(f"Didn't change the VsCode settings at {vscode_settings_json_path}")
        return

    if not vscode_settings_json_path.exists():
        logger.info(
            f"Creating a new VsCode settings file at {vscode_settings_json_path}"
        )
        vscode_settings_json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(vscode_settings_json_path, "w") as f:
        json.dump(settings_json, f, indent=4)


def _setup_ssh_config_file(config_file_path: str | Path) -> Path:
    # Save the original value for the prompt. (~/.ssh/config looks better on the
    # command-line).
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


def ask_to_confirm_changes(before: str, after: str, path: str | Path) -> bool:
    print(T.bold(f"The following modifications will be made to {path}:\n"))
    diff_lines = list(
        difflib.unified_diff(
            before.splitlines(True),
            after.splitlines(True),
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


def _confirm_changes(ssh_config: SSHConfig, previous: str) -> bool:
    before = previous + "\n"
    after = ssh_config.cfg.config() + "\n"
    return ask_to_confirm_changes(before, after, ssh_config.path)


def _get_mila_username(ssh_config: SSHConfig) -> str:
    # Check for a mila entry in ssh config
    # NOTE: This also supports the case where there's a 'HOST mila some_alias_for_mila'
    # entry.
    # NOTE: ssh_config.host(entry) returns an empty dictionary if there is no entry.
    username: str | None = None
    hosts_with_mila_in_name_and_a_user_entry = [
        host
        for host in ssh_config.hosts()
        if "mila" in host.split() and "user" in ssh_config.host(host)
    ]
    # Note: If there are none, or more than one, then we'll ask the user for their
    # username, just to be sure.
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


def _get_drac_username(ssh_config: SSHConfig) -> str | None:
    """Retrieve or ask the user for their username on the ComputeCanada/DRAC
    clusters."""
    # Check for one of the DRAC entries in ssh config
    username: str | None = None
    hosts_with_cluster_in_name_and_a_user_entry = [
        host
        for host in ssh_config.hosts()
        if any(
            cc_cluster in host.split() or f"!{cc_cluster}" in host.split()
            for cc_cluster in (
                "beluga",
                "cedar",
                "graham",
                "narval",
                "niagara",
            )
        )
        and "user" in ssh_config.host(host)
    ]
    users_from_drac_config_entries = set(
        ssh_config.host(host)["user"]
        for host in hosts_with_cluster_in_name_and_a_user_entry
    )
    # Note: If there are none, or more than one, then we'll ask the user for their
    # username, just to be sure.
    if len(users_from_drac_config_entries) == 1:
        username = users_from_drac_config_entries.pop()
    elif yn("Do you also have an account on the ComputeCanada/DRAC clusters?"):
        while not username:
            username = qn.text(
                "What's your username on the CC/DRAC clusters?\n",
                validate=_is_valid_username,
            ).unsafe_ask()
    return username.strip() if username else None


def _is_valid_username(text: str) -> bool | str:
    return (
        "Please enter your username on the mila cluster."
        if not text or text.isspace()
        else True
    )


# NOTE: Later, if we think it can be useful, we could use some fancy TypedDict for the
# SSH entries.
# from .ssh_config_entry import SshConfigEntry
# from typing_extensions import Unpack


def _add_ssh_entry(
    ssh_config: SSHConfig,
    host: str,
    entry: dict[str, str | int],
    *,
    _space_before: bool = True,
    _space_after: bool = False,
) -> None:
    """Adds or updates an entry in the ssh config object."""
    # NOTE: `Host` is also a parameter to make sure it isn't in `entry`.
    assert "Host" not in entry

    sorted_by_keys = False

    if host in ssh_config.hosts():
        existing_entry = ssh_config.host(host)
        existing_entry.update(entry)
        if sorted_by_keys:
            existing_entry = dict(sorted(existing_entry.items()))
        ssh_config.cfg.set(host, **existing_entry)
        logger.debug(f"Updated {host} entry in ssh config at path {ssh_config.path}.")
    else:
        if sorted_by_keys:
            entry = dict(sorted(entry.items()))
        ssh_config.add(
            host,
            _space_before=_space_before,
            _space_after=_space_after,
            **entry,
        )
        logger.debug(
            f"Adding new {host} entry in ssh config at path {ssh_config.path}."
        )


def _copy_valid_ssh_entries_to_windows_ssh_config_file(
    linux_ssh_config: SSHConfig, windows_ssh_config: SSHConfig
):
    unsupported_keys_lowercase = set(k.lower() for k in WINDOWS_UNSUPPORTED_KEYS)

    # NOTE: need to preserve the ordering of entries:
    entries_to_move = list(MILA_ENTRIES.keys()) + list(DRAC_ENTRIES.keys())
    for host in entries_to_move + [
        host for host in linux_ssh_config.hosts() if host not in entries_to_move
    ]:
        if host not in linux_ssh_config.hosts():
            warnings.warn(
                RuntimeWarning(
                    f"We expected to have a {host!r} entry in the SSH config. "
                    f"Did you run `mila init`?"
                )
            )
            continue
        linux_ssh_entry: dict[str, Any] = linux_ssh_config.host(host)
        _add_ssh_entry(
            windows_ssh_config,
            host,
            {
                key: value
                for key, value in linux_ssh_entry.items()
                if key.lower() not in unsupported_keys_lowercase
            },
        )


def _make_controlpath_dir(entry: dict[str, str | int]) -> None:
    if "ControlPath" not in entry:
        return
    control_path = entry["ControlPath"]
    assert isinstance(control_path, str)
    # Create the ControlPath directory if it doesn't exist:
    control_path_dir = Path(control_path).expanduser().parent
    control_path_dir.mkdir(exist_ok=True, parents=True)
