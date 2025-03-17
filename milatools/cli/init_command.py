from __future__ import annotations

import copy
import difflib
import functools
import json
import shlex
import shutil
import subprocess
import sys
import warnings
from logging import getLogger as get_logger
from pathlib import Path
from typing import Any, Literal

import paramiko
import questionary as qn
from invoke.exceptions import UnexpectedExit
from rich import print

from milatools.utils.remote_v2 import RemoteV2

from ..utils.local_v1 import LocalV1, check_passwordless, display
from ..utils.remote_v1 import RemoteV1
from ..utils.vscode_utils import (
    get_expected_vscode_settings_json_path,
    vscode_installed,
)
from .utils import SSHConfig, T, running_inside_WSL, yn

logger = get_logger(__name__)

WINDOWS_UNSUPPORTED_KEYS = ["ControlMaster", "ControlPath", "ControlPersist"]

MILA_FORM_URL = "https://docs.google.com/forms/d/e/1FAIpQLSeole2_fB_o0RF-FwEfdqvxV-qGN4nbGDnMbbYSi5ygMVeezg/viewform?usp=sf_link"
ON_WINDOWS = sys.platform == "win32"

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
        # persist forever (at least while the local machine is turned on).
        "ControlPersist": "yes",
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
DRAC_CLUSTERS = ["beluga", "cedar", "graham", "narval"]
DRAC_ENTRIES: dict[str, dict[str, int | str]] = {
    "beluga cedar graham narval niagara": {
        "Hostname": "%h.alliancecan.ca",
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
    mila_username: str | None = _get_mila_username(ssh_config)
    drac_username: str | None = _get_drac_username(ssh_config)
    orig_config = ssh_config.cfg.config()

    if mila_username:
        for hostname, entry in MILA_ENTRIES.copy().items():
            # todo: Do we want to set the `IdentityFile` value to the ssh key path?
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

    # if running inside WSL, copy the keys to the Windows folder.
    # Copy the SSH key to the windows folder so that passwordless SSH also works on
    # Windows.
    assert running_inside_WSL()
    windows_home = get_windows_home_path_in_wsl()
    linux_private_key_file = (
        Path(
            linux_ssh_config.host("mila").get(
                "identityfile", Path.home() / ".ssh/id_rsa"
            )
        )
        .expanduser()
        .resolve()
    )
    windows_private_key_file = windows_home / (
        linux_private_key_file.relative_to(Path.home())
    )
    windows_private_key_file.parent.mkdir(exist_ok=True, mode=0o700, parents=True)

    for linux_key_file, windows_key_file in [
        (linux_private_key_file, windows_private_key_file),
        (
            linux_private_key_file.with_suffix(".pub"),
            windows_private_key_file.with_suffix(".pub"),
        ),
    ]:
        _copy_if_needed(linux_key_file, windows_key_file)


def setup_passwordless_ssh_access(ssh_config: SSHConfig) -> bool:
    """Sets up passwordless ssh access to the Mila and optionally also to DRAC.

    Sets up ssh connection to the DRAC clusters if they are present in the SSH config
    file.

    Returns whether the operation completed successfully or not.
    """
    import rich.box
    from rich import print
    from rich.panel import Panel
    from rich.prompt import Confirm

    if ON_WINDOWS:
        if not Confirm.ask(
            "You are running `mila init` on a Windows terminal. This will eventually become unsupported. "
            "We really encourage you to setup the Windows Subsystem for Linux (WSL) if you haven't already, and run `mila init` from there.\n"
            "\n"
            "Please setup WSL following the instructions here: https://docs.alliancecan.ca/wiki/WSL"
            "\n"
            "Would you like to continue on the Windows side (and suffer lots and lots of popups and 2FA confirmation prompts?)"
        ):
            return False

    # print("Checking passwordless authentication")
    default_private_key = Path.home() / ".ssh/id_rsa"
    mila_private_key = (
        get_ssh_private_key_path(ssh_config, "mila") or default_private_key
    )
    drac_private_key = (
        get_ssh_private_key_path(ssh_config, "narval") or default_private_key
    )
    # todo: Should probably set `IdentityFile` in the ssh config if it wasn't
    # already there.
    mila_public_key = mila_private_key.with_suffix(".pub")
    drac_public_key = drac_private_key.with_suffix(".pub")

    drac_clusters_in_ssh_config: list[str] = []
    hosts_in_config = ssh_config.hosts()
    for cluster in DRAC_CLUSTERS:
        if any(cluster in hostname for hostname in hosts_in_config):
            drac_clusters_in_ssh_config.append(cluster)

    status: dict[str, dict[Literal["login", "compute"], bool | None]] = {
        "mila": {"login": None, "compute": None},
        **{cluster: {"login": None, "compute": None} for cluster in DRAC_CLUSTERS},
    }

    from rich.live import Live
    from rich.table import Table

    def _table(
        status: dict[str, dict[Literal["login", "compute"], bool | None]],
    ) -> Table:
        table = Table(title="SSH access status")
        table.add_column("Cluster")
        table.add_column("Login node access")
        table.add_column("Compute node access")

        def _icon(_s: bool | None) -> str:
            return "✅" if _s else "❔" if _s is None else "❌"

        for cluster, _values in status.items():
            table.add_row(cluster, _icon(_values["login"]), _icon(_values["compute"]))
        return table

    with Live(_table(status), refresh_per_second=4) as live:
        # fake demo:
        # status["mila"] = (True, None)
        # time.sleep(2.0)
        # live.update(_table(status))
        # status["narval"] = (True, None)
        # time.sleep(0.5)
        # live.update(_table(status))
        # status["beluga"] = (True, None)
        # time.sleep(0.5)
        # live.update(_table(status))

        # status["mila"] = (True, True)
        # time.sleep(0.5)
        # live.update(_table(status))

        if not mila_private_key.exists():
            if not Confirm.ask(
                f"There is no SSH key at {mila_private_key}. Generate one?"
            ):
                print("No private keys.")
                return False
            # Run ssh-keygen with the given location and no passphrase.
            create_ssh_keypair(mila_private_key)
            status["mila"]["login"] = False
            mila_login_access_is_setup = False
        else:
            print("Checking connection to the mila login nodes...")
            mila_login_access_is_setup = check_passwordless("mila")

        status["mila"]["login"] = mila_login_access_is_setup
        live.update(_table(status))

        if not mila_login_access_is_setup:
            # print(f"To get access to the `mila` cluster, please open up this google form")
            print(
                Panel(
                    mila_public_key.read_text(),
                    box=rich.box.HORIZONTALS,
                    title="Your public key for the Mila cluster",
                    subtitle=f"Paste this in the form at [blue][link={MILA_FORM_URL}]this link[/link][/blue]",
                    safe_box=True,
                )
            )
            if not Confirm.ask(
                f"Did you enter your public key in [blue][link={MILA_FORM_URL}]this google form[/link][/blue]?"
            ):
                pass

        else:
            setup_keys_on_login_node("mila")
            status["mila"]["compute"] = True
            live.update(_table(status))

    # success = setup_passwordless_ssh_access_to_cluster(ssh_config, "mila")
    # if not success:
    #     return False
    # setup_keys_on_login_node("mila")

    if not drac_clusters_in_ssh_config:
        logger.debug(
            f"There are no DRAC clusters in the SSH config at {ssh_config.path}."
        )
        return True

    print(
        "Setting up passwordless ssh access to the DRAC clusters with ssh-copy-id.\n"
        "\n"
        "Please note that you can also setup passwordless SSH access to all the DRAC "
        "clusters by visiting https://ccdb.alliancecan.ca/ssh_authorized_keys and "
        "copying in the content of your public key in the box.\n"
        "See https://docs.alliancecan.ca/wiki/SSH_Keys#Using_CCDB for more info."
    )

    if sys.platform == "win32":
        print(
            "Skipping the setup of passwordless SSH access to the DRAC clusters on "
            "Windows, as it would otherwise generate a lot of 2FA prompts. Please run this in WSL."
        )
        return

    drac_public_key = drac_private_key.with_suffix(".pub")
    print(
        Panel(
            drac_public_key.read_text(),
            title="DRAC public key",
            subtitle="Paste this into the box at https://ccdb.alliancecan.ca/ssh_authorized_keys",
        )
    )
    for drac_cluster in drac_clusters_in_ssh_config:
        success = setup_passwordless_ssh_access_to_cluster(ssh_config, drac_cluster)
        if not success:
            return False
        setup_keys_on_login_node(drac_cluster)
    return True


def get_ssh_private_key_path(ssh_config: SSHConfig, hostname: str) -> Path | None:
    config = paramiko.SSHConfig.from_path(ssh_config.path)
    identity_file = config.lookup(hostname).get("identityfile", None)
    # Seems to be a list for some reason?
    if isinstance(identity_file, list):
        assert identity_file
        identity_file = identity_file[0]
    if identity_file:
        return Path(identity_file).expanduser()
    return None


def setup_passwordless_ssh_access_to_cluster(
    ssh_config: SSHConfig, cluster: str
) -> bool:
    """Sets up passwordless SSH access to the given hostname.

    On Mac/Linux, uses `ssh-copy-id`. Performs the steps of ssh-copy-id manually on
    Windows.

    Returns whether the operation completed successfully or not.
    """
    here = LocalV1()
    # Check that it is possible to connect without using a password.
    print(f"Checking if passwordless SSH access is setup for the {cluster} cluster.")
    # TODO: Potentially use a custom key like `~/.ssh/id_milatools.pub` instead of
    # the default.

    ssh_private_key_path = get_ssh_private_key_path(ssh_config, cluster) or (
        Path.home() / ".ssh/id_rsa"
    )
    ssh_public_key_path = ssh_private_key_path.with_suffix(".pub")
    assert ssh_public_key_path.exists()

    # TODO: This will fail on Windows for clusters with 2FA.
    # if check_passwordless(cluster):
    #     logger.info(f"Passwordless SSH access to {cluster} is already setup correctly.")
    #     return True
    # if not yn(
    #     f"Your public key does not appear be registered on the {cluster} cluster. "
    #     "Register it?"
    # ):
    #     print("No passwordless login.")
    #     return False
    print("Please enter your password if prompted.")
    if sys.platform == "win32":
        # NOTE: This is to remove extra '^M' characters that would be added at the end
        # of the file on the remote!
        public_key_contents = ssh_public_key_path.read_text().replace("\r\n", "\n")
        command = (
            "ssh",
            "-i",
            str(ssh_private_key_path),
            "-o",
            "StrictHostKeyChecking=no",
            cluster,
            "cat >> ~/.ssh/authorized_keys",
        )
        display(command)
        import tempfile

        with tempfile.NamedTemporaryFile("w", newline="\n") as f:
            print(public_key_contents, end="", file=f)
            f.seek(0)
            subprocess.run(command, check=True, text=False, stdin=f)
    else:
        here.run(
            "ssh-copy-id",
            "-i",
            str(ssh_private_key_path),
            "-o",
            "StrictHostKeyChecking=no",
            cluster,
            check=True,
        )

    # double-check that this worked.
    if not check_passwordless(cluster):
        print(f"'ssh-copy-id {cluster}' appears to have failed!")
        return False
    return True


def setup_keys_on_login_node(cluster: str = "mila"):
    #####################################
    # Step 3: Set up keys on login node #
    #####################################

    print(
        f"Checking connection to compute nodes on the {cluster} cluster. "
        "This is required for `mila code` to work properly."
    )
    # todo: avoid re-creating the `Remote` here, since it goes through 2FA each time!
    remote = RemoteV1(cluster) if sys.platform == "win32" else RemoteV2(cluster)
    try:
        pubkeys = remote.get_output("ls -t ~/.ssh/id*.pub").splitlines()
        print("# OK")
    except UnexpectedExit:
        print("# MISSING")
        if yn("You have no public keys on the login node. Generate them?"):
            private_file = "~/.ssh/id_rsa"
            remote.run(f'ssh-keygen -q -t rsa -N "" -f {private_file}')
            pubkeys = [f"{private_file}.pub"]
        else:
            exit("Cannot proceed because there is no public key")

    common = remote.get_output(
        shlex.join(
            [
                "bash",
                "-c",
                "comm -12 <(sort ~/.ssh/authorized_keys) <(sort ~/.ssh/*.pub)",
            ]
        )
    )
    if common:
        print("# OK")
    else:
        print("# MISSING")
        if yn(
            "To connect to a compute node from a login node you need one id_*.pub to "
            "be in authorized_keys. Do it?"
        ):
            pubkey = pubkeys[0]
            remote.run(f"cat {pubkey} >> ~/.ssh/authorized_keys")
        else:
            exit("You will not be able to SSH to a compute node")


def print_welcome_message():
    print(T.bold_cyan("=" * 60))
    print(T.bold_cyan("Congrats! You are now ready to start working on the cluster!"))
    print(T.bold_cyan("=" * 60))
    print(T.bold("To connect to a login node:"))
    print("    ssh mila")
    print(T.bold("To allocate and connect to a compute node:"))
    print("    ssh mila-cpu")
    print(T.bold("To open a directory on the cluster with VSCode:"))
    print("    mila code path/to/code/on/cluster")
    print(T.bold("Same as above, but allocate 1 GPU, 4 CPUs, 32G of RAM:"))
    print("    mila code path/to/code/on/cluster --alloc --gres=gpu:1 --mem=32G -c 4")
    print()
    print(
        "For more information, read the milatools documentation at",
        T.bold_cyan("https://github.com/mila-iqia/milatools"),
        "or run `mila --help`.",
        "Also make sure you read the Mila cluster documentation at",
        T.bold_cyan("https://docs.mila.quebec/"),
        "and join the",
        T.bold_green("#mila-cluster"),
        "channel on Slack.",
    )


def _copy_if_needed(linux_key_file: Path, windows_key_file: Path):
    if not linux_key_file.exists():
        raise RuntimeError(
            f"Assumed that {linux_key_file} would exists, but it doesn't!"
        )
    if not windows_key_file.exists():
        print(
            f"Copying {linux_key_file} over to the Windows ssh folder at "
            f"{windows_key_file}."
        )
        shutil.copy2(src=linux_key_file, dst=windows_key_file)
        return

    print(
        f"{windows_key_file} already exists. Not overwriting it with contents of {linux_key_file}."
    )


@functools.lru_cache
def get_windows_home_path_in_wsl() -> Path:
    assert running_inside_WSL()
    windows_username = subprocess.getoutput("powershell.exe '$env:UserName'").strip()
    return Path(f"/mnt/c/Users/{windows_username}")


def create_ssh_keypair(
    ssh_private_key_path: Path,
    local: LocalV1 | None = None,
    passphrase: str | None = "",
) -> None:
    """Creates a public/private key pair at the given path using ssh-keygen.

    If passphrase is `None`, ssh-keygen will prompt the user for a passphrase.
    Otherwise, if passphrase is an empty string, no passphrase will be used (default).
    If a string is passed, it is passed to ssh-keygen and used as the passphrase.
    """
    local = local or LocalV1()
    command = [
        "ssh-keygen",
        "-f",
        str(ssh_private_key_path.expanduser()),
        "-t",
        "rsa",
    ]
    if passphrase is not None:
        command.extend(["-N", passphrase])
    display(command)
    subprocess.run(command, check=True)


def has_passphrase(ssh_private_key_path: Path) -> bool:
    """Returns whether the SSH private key has a passphrase or not."""
    assert ssh_private_key_path.exists()
    result = subprocess.run(
        args=(
            "ssh-keygen",
            "-y",
            "-P=''",
            "-f",
            str(ssh_private_key_path),
        ),
        capture_output=True,
        text=True,
    )
    logger.debug(f"Result of ssh-keygen: {result}")
    if result.returncode == 0:
        return False
    elif "incorrect passphrase supplied to decrypt private key" in result.stderr:
        return True
    raise NotImplementedError(
        f"TODO: Unable to tell if the key at {ssh_private_key_path} has a passphrase "
        f"or not! (result={result})"
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
            (
                f"Unable to setup VsCode settings file at {vscode_settings_json_path} "
                f"for remote development. Skipping and leaving the settings unchanged. "
                f"(Use 'mila -vvv init' to see the full error stacktrace.)"
            ),
        )
        logger.debug(f"Error: {err}", exc_info=err)


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


def _get_mila_username(ssh_config: SSHConfig) -> str | None:
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
        return ssh_config.host(hosts_with_mila_in_name_and_a_user_entry[0]).get("user")

    if not yn("Do you have an account on the Mila cluster?"):
        return None

    while not username:
        username = qn.text(
            "What's your username on the mila cluster?\n",
            validate=functools.partial(_is_valid_username, cluster_name="mila cluster"),
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
            for cc_cluster in DRAC_CLUSTERS
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
    elif yn("Do you have an account on the ComputeCanada/DRAC clusters?"):
        while not username:
            username = qn.text(
                "What's your username on the CC/DRAC clusters?\n",
                validate=functools.partial(
                    _is_valid_username, cluster_name="ComputeCanada/DRAC clusters"
                ),
            ).unsafe_ask()
    return username.strip() if username else None


def _is_valid_username(text: str, cluster_name: str = "mila cluster") -> bool | str:
    return (
        f"Please enter your username on the {cluster_name}."
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
