from __future__ import annotations

import copy
import difflib
import functools
import json
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
import warnings
from logging import getLogger as get_logger
from pathlib import Path
from typing import Any, Literal

import paramiko
import questionary as qn
import rich.box
import rich.text
from invoke.exceptions import UnexpectedExit
from rich import print as rprint
from rich.panel import Panel
from rich.prompt import Confirm
from rich.table import Table

from milatools.cli.utils import SSH_CONFIG_FILE, SSHConfig, T, running_inside_WSL, yn
from milatools.utils.local_v1 import LocalV1, display
from milatools.utils.local_v2 import LocalV2
from milatools.utils.remote_v1 import RemoteV1
from milatools.utils.remote_v2 import RemoteV2
from milatools.utils.vscode_utils import (
    get_expected_vscode_settings_json_path,
    vscode_installed,
)

logger = get_logger(__name__)

WINDOWS_UNSUPPORTED_KEYS = ["ControlMaster", "ControlPath", "ControlPersist"]
DRAC_FORM_URL = "https://ccdb.alliancecan.ca/ssh_authorized_keys"
MILA_ONBOARDING_URL = "https://sites.google.com/mila.quebec/mila-intranet/it-infrastructure/it-onboarding-training"
MILA_SSHKEYS_DOCS_URL = "https://docs.mila.quebec/Userguide.html#ssh-private-keys"
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
        "StrictHostKeyChecking": "no",  # Prevents VsCode erroring out when using mila code.
        **ssh_multiplexing_config,
    },
    # todo: add this entry (and test that `mila code` also works with it.)
    # "cn-????": {
    #     "HostName": "%h.server.mila.quebec",
    #     "ProxyJump": "mila",
    #     **ssh_multiplexing_config,
    # },
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


def init(ssh_dir: Path = SSH_CONFIG_FILE.parent):
    """Set up your SSH configuration and keys to access the Mila / DRAC clusters.

    1. Sets up your ~/.ssh/config file.
        - Adds the entries for the Mila and optionally the DRAC clusters
        - Updates the config if it already exists
        - Asks for confirmation before making changes. if changes are rejected, exits.
    2. Setup SSH access to the Mila cluster login nodes
        - Runs `ssh-keygen` if there isn't already a key for the Mila cluster.
        - Prints the content of the public key in a nice text block to copy-paste.
        - Displays a link to the Google form(s) to use to submit the public key.
    3. Mila cluster compute nodes (only if we already have access to the Mila login nodes)
        - Run `ssh-keygen` on the login node if there isn't already a key in ~/.ssh.
        - Add the content of that public key to ~/.ssh/authorized_keys if it isn't already there
    4. SSH access to DRAC clusters
        Currently we do `ssh-copy-id` and it still works with DRAC clusters.
        Should we also transition to just displaying the form instead?
        If we also switch to the form, then do the same as for the Mila cluster in 2:
        - Generates (another?) keypair to connect to DRAC if there isn't one already.
        - Prints the content of the public key in a nice text block to copy-paste.
        - Displays a link to the CCDB form to submit the public key.
    5. DRAC cluster compute nodes (only if we have already access to the DRAC login nodes)
        - If running on Windows, print a big red warning to tell the user that they can
          either suffer through LOTS of 2FA prompts on their phone, or switch to WSL.
        - Same as in 3 but done on each of the DRAC clusters.
    6. Sets up VSCode settings if VsCode is installed locally
        - Add "remote.SSH.connectTimeout": 60 in Vscode's `settings.json` file.
    7. Displays a welcome message with further instructions and tips.
    """

    #############################
    # Step 1: SSH Configuration #
    #############################

    rprint("Checking ssh config")
    ssh_config_path = ssh_dir / "config"
    ssh_config = setup_ssh_config(ssh_config_path)

    _hosts = ssh_config.hosts()
    drac_clusters_in_config = [
        c for c in DRAC_CLUSTERS if any(c in host for host in _hosts)
    ]

    mila_success = setup_mila_ssh_access(ssh_dir, ssh_config=ssh_config)

    _drac_success = None
    if drac_clusters_in_config:
        _drac_success = setup_drac_ssh_access(
            ssh_dir, ssh_config, drac_clusters_in_config
        )

    # if we're running on WSL, we actually just copy the id_rsa + id_rsa.pub and the
    # ~/.ssh/config to the Windows ssh directory (taking care to remove the
    # ControlMaster-related entries) so that the user doesn't need to install Python and
    # milatools on the Windows side.
    if running_inside_WSL():
        setup_windows_ssh_config_from_wsl(linux_ssh_config=ssh_config)

    setup_vscode_settings()

    if mila_success:
        print_welcome_message()
    else:
        rprint(
            "[bold red]Mila cluster access is not fully configured! See the instructions above.[/]"
        )
    if _drac_success:
        rprint("[bold green]DRAC cluster access is fully setup![/]")

    elif _drac_success is None:
        rprint("Skipping setup for DRAC clusters (no DRAC entries in SSH config).")
    else:
        assert _drac_success is False
        rprint(
            "[bold orange4]DRAC cluster access is not fully configured! See the instructions above.[/]"
        )


def setup_mila_ssh_access(
    ssh_dir: Path,
    ssh_config: SSHConfig,
):
    rprint(
        Panel(
            rich.text.Text("MILA SETUP", justify="center"),
            box=rich.box.HORIZONTALS,
            style="bold blue",
        )
    )
    cluster = "mila"
    default_private_key: Path = ssh_dir / "id_rsa"
    # docs_url = MILA_SSHKEYS_DOCS_URL

    # todo: check if this would also work on Windows.
    # mila_private_key = (
    #     subprocess.get_output(shlex.split("ssh -G mila | grep identityfile"))
    #     .splitlines()[0]
    #     .split()[1]
    # )

    private_key = get_ssh_private_key_path(ssh_config, cluster) or next(
        (k.with_suffix("") for k in ssh_dir.glob("id_*.pub")),
        default_private_key,
    )
    public_key = private_key.with_suffix(".pub")

    login_node: RemoteV2 | RemoteV1 | None = None

    if not private_key.exists():
        create_ssh_keypair_and_check_exists(cluster, private_key, public_key)
        if Confirm.ask("Is this your first time connecting to the Mila cluster?"):
            rprint(
                f"Paste the public key below during the final steps of onboarding:\n"
                f" --> [bold blue]{MILA_ONBOARDING_URL}[/]"
            )
            display_public_key(
                public_key, cluster="mila", subtitle="Paste this into the form!"
            )
            return False

        if Confirm.ask(
            "Do you already have access to the Mila cluster from another machine?"
        ):
            rprint(
                "[bold orange4]You can only use up to two SSH keys in total to connect to the Mila cluster.[/]\n"
                "We recommend that you copy the public and private SSH keys from that "
                f"other machine to this machine at paths {public_key} and {private_key} respectively."
            )
            if Confirm.ask(
                "Would you like to reuse your existing key from another machine?"
            ):
                rprint(
                    "Please copy the public and private keys from the other machine to this machine, "
                    f"overwriting the contents of {public_key} and {private_key} and run `mila init` again."
                )
                return False
        rprint(
            "Here is your SSH public key on this machine. Send it to it-support@mila.quebec:\n"
        )
        display_public_key(
            public_key,
            cluster="mila",
            subtitle="Include this in your email to it-support@mila.quebec",
        )
        rprint(
            "After contacting it-support@mila.quebec, run `mila init` again to "
            "make sure that you are able to use `mila code` to connect to compute nodes."
        )
        return False

    # No point in trying to login if the config and key didn't exist to begin with.
    rprint(f"Checking connection to the {cluster} login nodes... ", flush=True)
    if login_node := try_to_login(cluster):
        rprint(f"✅ Able to `ssh {cluster}`")

        rprint(f"Checking connection to the {cluster} compute nodes... ")
        if can_access_compute_nodes(login_node):
            rprint(
                f"✅ ~/.ssh/id_rsa.pub is in ~/.ssh/authorized_keys on the {cluster} cluster."
            )
            return True  # all setup.
        rprint(
            f"❌ ~/.ssh/id_rsa.pub is not in ~/.ssh/authorized_keys on the {cluster} cluster. "
            f"Attempting to fix this now."
        )
        setup_keys_on_login_node(cluster, remote=login_node)
        return True  # should all be setup.
    rprint(f"\n❌ Unable to `ssh {cluster}`!\n")

    display_public_key(
        public_key,
        cluster,
    )

    rprint("[bold red]You seem to be unable to connect to the `Mila` cluster.[/]")
    rprint(
        f"- If this is your first time connecting to the Mila cluster, please make "
        f"sure to successfully complete the onboarding, entering the public key at the "
        f"end of the form at the final step:\n"
        f" --> [bold blue]{MILA_ONBOARDING_URL}[/]"
    )
    rprint(
        f"- If this isn't your first time connecting to the {cluster} cluster, send an email to "
        f"[link=mailto:it-support@mila.quebec]it-support@mila.quebec[/link].\n"
        "   Make sure to include :arrow_up: [bold]your public key above[/bold]:arrow_up: in the email."
    )
    rprint(
        "- If you still have issues connecting, take a look at previous messages in "
        "the [bold green]#milatools[/] and [bold green]#mila-cluster[/] channels on "
        "Slack for common questions and known solutions."
    )
    rprint(
        "- [bold green]If all else fails, contact IT-support at "
        "[link=mailto:it-support@mila.quebec]it-support@mila.quebec[/link] and provide as "
        "much information as possible.[/]"
    )
    # TODO: Include some useful information as part of the error message for users.
    return False


def setup_drac_ssh_access(
    ssh_dir: Path, ssh_config: SSHConfig, drac_clusters_in_config: list[str]
):
    rprint(
        Panel(
            rich.text.Text("DRAC SETUP", justify="center"),
            box=rich.box.HORIZONTALS,
            style="bold blue",
        )
    )

    default_private_key = ssh_dir / "id_rsa"
    # Get the private key from the SSH config, otherwise the first key found in the SSH dir, otherwise the default key.
    drac_private_key = get_ssh_private_key_path(
        ssh_config, drac_clusters_in_config[0]
    ) or next(
        (k.with_suffix("") for k in ssh_dir.glob("id_*.pub")),
        default_private_key,
    )
    drac_public_key = drac_private_key.with_suffix(".pub")

    logger.info(f"DRAC public key path: {drac_public_key}")
    if not drac_private_key.exists():
        create_ssh_keypair_and_check_exists(
            "drac", private_key=drac_private_key, public_key=drac_public_key
        )
        display_public_key(drac_public_key, cluster="DRAC")
        rprint("Submit your public key to the DRAC form at this URL:")
        rprint(f" :arrow_right: [bold blue]{DRAC_FORM_URL}[/] :arrow_left:")
        return False

    display_public_key(
        drac_public_key,
        cluster="DRAC",
    )

    if ON_WINDOWS and not Confirm.ask(
        "You are running `mila init` on a Windows terminal. "
        "We really encourage you to setup the Windows Subsystem for Linux (WSL) if you haven't already, and run `mila init` from there.\n"
        "See this link for instructions on setting up WSL: https://docs.alliancecan.ca/wiki/WSL"
        "\n"
        "Would you like to continue on the Windows side? ([bold red]You will have to click through lots and lots of 2FA popups on your phone[/]!)"
        "\n"
    ):
        if not Confirm.ask("Are you sure? (this is going to be annoying...)"):
            return False

    if not Confirm.ask(
        "[bold]Did you already submit :arrow_up: your DRAC public key above :arrow_up: "
        "to CCDB using this form?\n"
        f" :arrow_right: [link={DRAC_FORM_URL}]{DRAC_FORM_URL}[/] :arrow_left:\n"
        "\n"
    ):
        rprint(
            "Please submit your DRAC public key above using the DRAC form and run again after "
            "waiting for a few minutes."
        )
        return False

    # Setup SSH access to the DRAC compute nodes.
    for drac_cluster in drac_clusters_in_config:
        rprint(f"Checking connection to the {drac_cluster} login nodes...")

        drac_cluster_private_key = (
            get_ssh_private_key_path(ssh_config, drac_cluster) or drac_private_key
        )

        try:
            remote = (
                RemoteV2(drac_cluster) if not ON_WINDOWS else RemoteV1(drac_cluster)
            )
            rprint(f"✅ Able to `ssh {drac_cluster}`")
        except Exception as exc:
            rprint(f"❌ Unable to `ssh {drac_cluster}`! {exc}")
            continue

        rprint(f"Checking connection to the {drac_cluster} compute nodes...")
        if can_access_compute_nodes(remote):
            rprint(
                f"✅ ~/.ssh/id_rsa.pub is in ~/.ssh/authorized_keys on {drac_cluster}"
            )
        else:
            rprint(
                f"❌ ~/.ssh/id_rsa.pub is not in ~/.ssh/authorized_keys on {drac_cluster}. "
                "Attempting to fix this now."
            )
            setup_keys_on_login_node(cluster=drac_cluster, remote=remote)

        # NOTE: While we're at it, might as well try to run ssh-copy-id {drac_cluster}
        # so that if the user was able to login by putting in a password, they won't
        # have to do it anymore.
        try:
            run_ssh_copy_id(drac_cluster, ssh_private_key_path=drac_cluster_private_key)
            rprint(f"✅ Passwordless access to {drac_cluster} is now setup.")
        except Exception as exc:
            rprint(
                f"❌ Unable to run ssh-copy-id {drac_cluster} ({exc}). "
                "You may need to do this manually."
            )


def create_ssh_keypair_and_check_exists(
    cluster: str, private_key: Path, public_key: Path
):
    if public_key.exists():
        raise RuntimeError(
            f"Private key doesn't exist at {private_key}, but a public key exists "
            f"at {public_key}! Please delete the public key at {public_key} and "
            f"try again."
        )

    rprint(
        f"Creating a new SSH key to be used to connect to the {cluster} cluster at {private_key}.\n"
        "\n",
        f"[blue]Enter a passphrase to set for the SSH key {private_key}:[/]",
    )
    # `passphrase=None` lets the user set a passphrase (or not) interactively.
    create_ssh_keypair(private_key, passphrase=None)
    if not public_key.exists():
        raise RuntimeError(
            f"Expected the public key to be created by ssh-keygen at {public_key}!"
        )
    if not private_key.exists():
        raise RuntimeError(
            f"Expected the private key to be created by ssh-keygen at {private_key}!"
        )


def display_public_key(
    public_key: Path,
    cluster: str,
    subtitle: str = "(This is what you should Copy & paste if asked)",
):
    # This displays the content of the public key in a nice box with top and bottom
    # lines.
    # Unfortunately if we just print a `rich.Panel`, it adds a space at the start of
    # each line inside the box. We'd like to minimize the risk of errors when
    # copy-pasting the public key into the form, so here the output is first piped into
    # a buffer, then textwrap is used to remove the leading indent.
    # It's possible that the form is already resilient to added spaces and such, but
    # this might be safer.

    # Simpler, but not as nice-looking alternative:
    # console.print(f"------- Your public key for the {cluster} cluster -------")
    # console.print(public_key.read_text())
    # console.print(f"--------------------------------------------------------")
    from milatools.cli import console

    with console.capture() as capture:
        console.print(
            Panel(
                public_key.read_text(),
                box=rich.box.HORIZONTALS,
                title=f"Your public key for the {cluster} cluster",
                subtitle=subtitle,
                padding=(0, 0),
                safe_box=True,
                expand=False,
            )
        )

    text = capture.get()
    print(textwrap.dedent(text))


def try_to_login(cluster: str) -> RemoteV2 | RemoteV1 | None:
    try:
        return RemoteV2(cluster) if not ON_WINDOWS else RemoteV1(cluster)
    except Exception:
        return None


def can_access_compute_nodes(login_node: RemoteV2 | RemoteV1) -> bool:
    return bool(
        login_node.get_output(
            shlex.join(
                [
                    "bash",
                    "-c",
                    "comm -12 <(sort ~/.ssh/authorized_keys) <(sort ~/.ssh/*.pub)",
                ]
            )
        )
    )


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
        - A boolean indicating whether the config was changed.
        - an SSHConfig object used to read / write config options.
    """

    ssh_config_path = _setup_ssh_config_file(ssh_config_path)
    ssh_config = SSHConfig(ssh_config_path)
    mila_username: str | None = _get_mila_username(ssh_config)
    drac_username: str | None = _get_drac_username(ssh_config)
    orig_config_text = ssh_config.cfg.config()

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

    new_config_text = ssh_config.cfg.config()
    if orig_config_text == new_config_text:
        rprint("Did not change ssh config")
        return ssh_config
    if not _confirm_changes(ssh_config, previous=orig_config_text):
        exit("Refused changes to ssh config.")
        # return False, original_config

    ssh_config.save()
    rprint(f"Wrote {ssh_config_path}")
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
        rprint(f"Did not change ssh config at path {windows_ssh_config.path}")

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


def run_ssh_copy_id(cluster: str, ssh_private_key_path: Path) -> bool:
    """Sets up passwordless SSH access to the given hostname.

    On Mac/Linux, uses `ssh-copy-id`. Performs the steps of ssh-copy-id manually on
    Windows.

    Returns whether the operation completed successfully or not.
    """
    here = LocalV2()
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
    rprint(f"Running ssh-copy-id to setup passwordless access to {cluster}.")
    rprint("Please enter your password if prompted.")
    if ON_WINDOWS:
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

        with tempfile.NamedTemporaryFile("w", newline="\n") as f:
            rprint(public_key_contents, end="", file=f)
            f.seek(0)
            subprocess.run(command, check=True, text=False, stdin=f)
    else:
        here.run(
            (
                "ssh-copy-id",
                "-i",
                str(ssh_private_key_path),
                "-o",
                "StrictHostKeyChecking=no",
                cluster,
            )
        )

    # double-check that this worked.
    # if not check_passwordless(cluster):
    #     print(f"'ssh-copy-id {cluster}' appears to have failed!")
    #     return False
    return True


def setup_keys_on_login_node(
    cluster: str = "mila", remote: RemoteV1 | RemoteV2 | None = None
):
    #####################################
    # Step 3: Set up keys on login node #
    #####################################

    rprint(
        f"Checking connection to compute nodes on the {cluster} cluster. "
        "This is required for `mila code` to work properly."
    )
    if remote is None:
        remote = RemoteV1(cluster) if sys.platform == "win32" else RemoteV2(cluster)
    try:
        pubkeys = remote.get_output("ls -t ~/.ssh/id*.pub").splitlines()
        rprint("# OK")
    except UnexpectedExit:
        rprint("# MISSING")
        rprint("Generating a new public key on the login node.")
        private_file = "~/.ssh/id_rsa"
        remote.run(f'ssh-keygen -q -t rsa -N "" -f {private_file}')
        pubkeys = [f"{private_file}.pub"]
        # else:
        #     exit("Cannot proceed because there is no public key")

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
        # print("# OK")
        return True
    else:
        # print("# MISSING")
        pubkey = pubkeys[0]
        remote.run(f"cat {pubkey} >> ~/.ssh/authorized_keys", display=True, hide=False)
        return True


def print_welcome_message():
    rprint("[bold cyan]" + ("=" * 60) + "[/]")
    rprint("[bold cyan]Congrats! You are now ready to start working on the cluster![/]")
    rprint("[bold cyan]" + ("=" * 60) + "[/]")
    rprint("[bold]To connect to a login node:[/]")
    rprint("    ssh mila")
    rprint("[bold]To allocate and connect to a compute node:[/]")
    rprint("    ssh mila-cpu")
    rprint("[bold]To open a directory on the cluster with VSCode:[/]")
    rprint("    mila code path/to/code/on/cluster")
    rprint("[bold]Same as above, but allocate 1 GPU, 4 CPUs, 32G of RAM:[/]")
    rprint("    mila code path/to/code/on/cluster --alloc --gres=gpu:1 --mem=32G -c 4")
    rprint()
    rprint(
        "For more information, read the milatools documentation at",
        "https://github.com/mila-iqia/milatools",
        "or run `mila --help`.",
        "Also make sure you read the Mila cluster documentation at",
        "https://docs.mila.quebec/",
        "and join the",
        "[bold green]#mila-cluster[/]",
        "channel on Slack.",
    )


def _copy_if_needed(linux_key_file: Path, windows_key_file: Path):
    if not linux_key_file.exists():
        raise RuntimeError(
            f"Assumed that {linux_key_file} would exists, but it doesn't!"
        )
    if not windows_key_file.exists():
        rprint(
            f"Copying {linux_key_file} over to the Windows ssh folder at "
            f"{windows_key_file}."
        )
        shutil.copy2(src=linux_key_file, dst=windows_key_file)
        return

    rprint(
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
) -> Path:
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
    return ssh_private_key_path


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
    rprint("Setting up VsCode settings for Remote development.")

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
        rprint(f"Didn't change the VsCode settings at {vscode_settings_json_path}")
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
    config_file = Path(config_file_path).expanduser()
    if not config_file.exists():
        logger.info(f"Creating a new SSH config file at {config_file_path}")

    ssh_dir = config_file.parent
    if not ssh_dir.exists():
        ssh_dir.mkdir(mode=0o700, exist_ok=True)
        rprint(f"Created the ssh directory at {ssh_dir}")
    elif ssh_dir.stat().st_mode & 0o777 != 0o700:
        ssh_dir.chmod(mode=0o700)
        rprint(f"Fixed the permissions on ssh directory at {ssh_dir} to 700")

    if not config_file.exists():
        config_file.touch(mode=0o600)
        rprint(f"Created {config_file}")
        return config_file
    # Fix any permissions issues:
    if config_file.stat().st_mode & 0o777 != 0o600:
        config_file.chmod(mode=0o600)
        rprint(f"Fixing permissions on {config_file} to 600")
        return config_file

    return config_file


def show_modifications(before: str, after: str, path: str | Path):
    rprint(f"[bold]The following modifications will be made to {path}:\n[/bold]")
    diff_lines = list(
        difflib.unified_diff(
            before.splitlines(True),
            after.splitlines(True),
        )
    )
    for line in diff_lines[2:]:
        if line.startswith("-"):
            rprint(f"[red]{line}[/red]", end="")
        elif line.startswith("+"):
            rprint(f"[green]{line}[/green]", end="")
        else:
            rprint(line, end="")


def ask_to_confirm_changes(before: str, after: str, path: str | Path) -> bool:
    show_modifications(before, after, path)
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
