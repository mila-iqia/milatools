from __future__ import annotations

import copy
import difflib
import functools
import json
import shutil
import subprocess
import sys
import tempfile
import warnings
from logging import getLogger as get_logger
from pathlib import Path, PosixPath
from typing import Any, Literal

import rich.box
import rich.prompt
import rich.text
from rich import print as rprint
from rich.panel import Panel
from rich.prompt import Confirm
from rich.table import Table

from milatools.cli.utils import SSH_CONFIG_FILE, SSHConfig, T, running_inside_WSL, yn
from milatools.utils.local_v1 import display
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

ssh_multiplexing_config = (
    {
        # Tries to reuse an existing connection, but if it fails, it will create a
        # new one.
        "ControlMaster": "auto",
        # This makes a file per connection, like
        # normandf@login.server.mila.quebec:2222
        "ControlPath": r"~/.cache/ssh/%r@%h:%p",
        # persist forever (at least while the local machine is turned on).
        "ControlPersist": "yes",
    }
    if sys.platform != "win32"
    else {}
)


MILA_ENTRIES: dict[str, dict[str, int | str]] = {
    "mila": {
        "HostName": "login.server.mila.quebec",
        # "User": mila_username,
        "PreferredAuthentications": "publickey,keyboard-interactive",
        "Port": 2222,
        "ServerAliveInterval": 120,
        "ServerAliveCountMax": 5,
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
        # TODO: If we include both scripts as part of milatools, we could copy them to
        # the login nodes of other clusters as well to enable things like `ssh narval-gpu`
        "ProxyCommand": (
            'ssh mila "/cvmfs/config.mila.quebec/scripts/milatools/slurm-proxy.sh '
            "mila-cpu "  # job name
            "--mem=8G"  # resources.
            '"'
        ),
        "RemoteCommand": (
            "/cvmfs/config.mila.quebec/scripts/milatools/entrypoint.sh "
            "mila-cpu"  # job name (matches above).
        ),
    },
    # Compute nodes:
    "*.server.mila.quebec !*login.server.mila.quebec": {
        "ProxyJump": "mila",
        # "User": mila_username,
    },
    "cn-????": {
        # "HostName": "%h.server.mila.quebec",
        "ProxyJump": "mila",
    },
}
DRAC_CLUSTERS = [
    # "beluga",
    # "cedar",
    # "graham",
    # "niagara",
    "narval",
    "rorqual",
    "fir",
    "nibi",
    "trillium",
    "trillium-gpu",
    "tamia",
    "killarney",
    "vulcan",
]
DRAC_ENTRIES: dict[str, dict[str, int | str]] = {
    " ".join(DRAC_CLUSTERS): {
        "Hostname": "%h.alliancecan.ca",
        # User=drac_username,
        # SSH multiplexing is useful here to go through 2FA only once.
        **ssh_multiplexing_config,
    },
    # "bc????? bg????? bl?????": {
    #     "ProxyJump": "beluga",
    #     # User=drac_username,
    # },
    # "cdr? cdr?? cdr??? cdr????": {
    #     "ProxyJump": "cedar",
    #     # User=drac_username,
    # },
    # "!graham  gra??? gra????": {
    #     "ProxyJump": "graham",
    #     # User=drac_username,
    # },
    # "!niagara nia????": {
    #     "ProxyJump": "niagara",
    #     # User=drac_username,
    # },
    "nc????? ng?????": {
        "ProxyJump": "narval",
        # User=drac_username,
    },
    "rc????? rg????? rl?????": {
        "ProxyJump": "rorqual",
        # User=drac_username,
    },
    "fc????? fb?????": {
        "ProxyJump": "fir",
        # User=drac_username,
    },
    "c? c?? c??? g? g?? l? l?? m? m?? u?": {
        "ProxyJump": "nibi",
        # User=drac_username,
    },
    "tg????? tc?????": {
        "ProxyJump": "tamia",
        # User=drac_username,
    },
    "kn???": {
        "ProxyJump": "killarney",
        # User=drac_username,
    },
    "rack??-??": {
        "ProxyJump": "vulcan",
        # User=drac_username,
    },
    "!trillium tri????": {
        "ProxyJump": "trillium",
    },
    "!trillium trig????": {
        "ProxyJump": "trillium-gpu",
    },
}

VSCODE_SETTINGS = {"remote.SSH.connectTimeout": 60}


def init(ssh_dir: Path = SSH_CONFIG_FILE.parent):
    """Set up your SSH configuration and keys to access the Mila / DRAC clusters.

    1. Sets up your ~/.ssh/config file.
        - If you have a Mila account, adds the entries for Mila
        - If you have a DRAC account, adds the entries for DRAC.
        - Updates the config if it already exists
        - Asks for confirmation before making changes - if changes are rejected, exits.
    2. If the user has a Mila account, setup the SSH access to the Mila cluster.
        - Checks access to Mila login nodes. If not setup:
            - If there isn't already an SSH keypair in ~/.ssh, runs `ssh-keygen`.
            - Prints the content of the public key in a nice text block that is easy to copy-paste.
            - Instructs the user to send it to it-support@mila.quebec.
        - Checks that everything is setup for compute node node access:
            - Checks that the public key is in ~/.ssh/authorized_keys on the login node.
              If not, copies it explicitly. (Note: ssh-copy-id won't do it without the
              -f option).
            - Checks that the permissions are set correctly on the ~/.ssh directory, keys,
              and ~/.ssh/authorized_keys. If not, corrects them.
    3. If the user has a DRAC account, setup the SSH access to the DRAC cluster(s).
        - Login node access:
            - Generates a keypair to connect to DRAC if there isn't one already.
            - Prints the content of the public key in a nice text block to copy-paste.
            - Displays a link to the CCDB form to submit the public key.
        - DRAC compute nodes access
            - If running on Windows, print a big red warning and errors out, telling the
              user that have to switch to WSL.
    5. Sets up VSCode settings if VsCode is installed locally
        - Add "remote.SSH.connectTimeout": 60 in Vscode's `settings.json` file.
    6. Displays a welcome message *only if all previous steps succeeded* with further
       instructions and tips.
    """

    #############################
    # Step 1: SSH Configuration #
    #############################

    rprint("Checking ssh config")
    ssh_config_path = ssh_dir / "config"
    ssh_config, mila_username, drac_username = _setup_ssh_config(ssh_config_path)

    # if we're running on WSL, we actually just copy the id_rsa + id_rsa.pub and the
    # ~/.ssh/config to the Windows ssh directory (taking care to remove the
    # ControlMaster-related entries) so that the user doesn't need to install Python and
    # milatools on the Windows side.
    if running_inside_WSL():
        assert isinstance(ssh_dir, PosixPath)
        setup_windows_ssh_config_from_wsl(ssh_dir, linux_ssh_config=ssh_config)

    ##################################
    # Step 2: Mila login node access #
    ##################################

    mila_login_node = None
    if mila_username:
        mila_login_node = setup_mila_ssh_access(ssh_dir, ssh_config=ssh_config)

    _hosts = ssh_config.hosts()
    drac_clusters_in_config = [
        c for c in DRAC_CLUSTERS if any(c in host for host in _hosts)
    ]

    drac_success = None
    if drac_username:
        if sys.platform == "win32":
            warnings.warn(
                RuntimeWarning(
                    "Setup of DRAC clusters is not supported on Windows.\n"
                    "You need to setup the Windows Subsystem for Linux (WSL), and run `mila init` from there.\n"
                    "See this link for instructions on setting up WSL: https://docs.alliancecan.ca/wiki/WSL\n"
                )
            )
        else:
            drac_success = setup_drac_ssh_access(
                ssh_dir, ssh_config, drac_clusters_in_config
            )

    setup_vscode_settings()

    if mila_login_node:
        print_welcome_message()
    else:
        rprint(
            "[bold red]Mila cluster access is not fully configured! See the instructions above.[/]"
        )

    if drac_success:
        rprint("[bold green]DRAC cluster access is fully setup![/]")
    elif not drac_success:
        rprint("Skipped setup for DRAC clusters.")
    else:
        rprint(
            "[bold orange4]DRAC cluster access is not fully configured! See the instructions above.[/]"
        )


def setup_mila_ssh_access(
    ssh_dir: Path,
    ssh_config: SSHConfig,
) -> RemoteV2 | RemoteV1 | None:
    rprint(
        Panel(
            rich.text.Text("MILA SETUP", justify="center"),
            box=rich.box.HORIZONTALS,
            style="bold blue",
        )
    )
    cluster = "mila"

    public_key_path = (
        get_ssh_public_key_path("mila", ssh_config)
        or Path.home() / ".ssh" / "id_rsa_mila.pub"
    )
    private_key_path = public_key_path.with_suffix("")
    logger.debug(f"Expecting the Mila public key to be at {public_key_path}")
    rprint(f"Checking connection to the {cluster} login nodes... ", flush=True)

    if login_node := try_to_login(cluster):
        if not private_key_path.exists():
            raise RuntimeError(
                f"Able to `ssh {cluster}`, but the private key expected at "
                f"{private_key_path} doesn't exist! "
                f"Please consider adding an IdentityFile entry pointing to the private "
                f"key you are using to connect to the Mila cluster in the mila entry of your SSH config. "
            )

        rprint(f"✅ Able to `ssh {cluster}`")
        if can_access_compute_nodes(login_node, public_key_path=public_key_path):
            rprint(
                f"✅ Local {public_key_path} is in ~/.ssh/authorized_keys on the "
                f"{cluster} cluster, and the permissions are correct. "
                f"You should have SSH access to the compute nodes."
            )
            return login_node  # all setup.
        rprint(
            f"❌ Local {public_key_path} is not in ~/.ssh/authorized_keys on the "
            f"{cluster} cluster, or file permissions are incorrect. Attempting to fix "
            f"this now."
        )
        setup_access_to_compute_nodes(
            cluster, remote=login_node, public_key_path=public_key_path
        )
        if not can_access_compute_nodes(login_node, public_key_path=public_key_path):
            raise RuntimeError(
                f"Unable to setup SSH access to the compute nodes of the {cluster} "
                f"cluster! Please reach out to IT-support@mila.quebec or or ask for help "
                f"on the #mila-cluster slack channel."
            )
        rprint(
            f"✅ Local {public_key_path} is in ~/.ssh/authorized_keys on the "
            f"{cluster} cluster and file permissions are correct. You should now "
            f"be able to connect to compute nodes with SSH."
        )
        return login_node  # all setup.

    if Confirm.ask("Is this your first time connecting to the Mila cluster?"):
        if not private_key_path.exists():
            create_ssh_keypair_and_check_exists(
                cluster, private_key_path, public_key_path
            )

        # If we're on WSL and we just created a new keypair. We also copy it to the
        # Windows ssh folder.
        if running_inside_WSL():
            assert isinstance(ssh_dir, PosixPath)
            _copy_keys_from_wsl_to_windows(ssh_dir)

        rprint(
            f"Please follow the onboarding steps provided by IT-support and paste "
            f"this public key below during the final steps:\n"
            f" --> [bold blue]{MILA_ONBOARDING_URL}[/]"
        )
        display_public_key(
            public_key_path,
            cluster="mila",
            subtitle="⬆️ Paste this into the form! ⬆️",
        )
        rprint(
            "After completing the onboarding, you will have to [bold]wait up to "
            "24h[/bold] to get access to the Mila cluster."
        )
        return None

    if Confirm.ask(
        "Do you already have access to the Mila cluster from another machine?"
    ):
        rprint(
            "[bold orange4]You can only use up to two SSH keys in total to connect to the Mila cluster.[/]\n"
            "We recommend that you copy the public and private SSH keys from that "
            f"other machine to this machine at paths {public_key_path} and {private_key_path} respectively."
        )
        rprint(
            "After this is done, run `mila init` again, and reach out to IT-support@mila.quebec if you have any issues."
        )
        return None
    rprint(f"\n❌ Unable to `ssh {cluster}`!\n")

    if not private_key_path.exists():
        create_ssh_keypair_and_check_exists(cluster, private_key_path, public_key_path)

    display_public_key(
        public_key_path,
        cluster="mila",
        subtitle="⬆️ This is your Mila SSH public key. ⬆️",
    )
    rprint("[bold red]You seem to be unable to connect to the Mila cluster.[/]")
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
    return None


def copy_ssh_keys_between_wsl_and_windows(ssh_dir: PosixPath):
    if not ssh_dir.exists():
        ssh_dir.mkdir(parents=True, mode=0o700)
    # todo: Do we need to set permissions on that windows .ssh directory?
    # windows_ssh_dir = get_windows_home_path_in_wsl() / ".ssh"
    _copy_keys_from_windows_to_wsl(ssh_dir)
    # If we are using WSL now, and already have the keys in WSL but not in Windows, do
    # the opposite.
    _copy_keys_from_wsl_to_windows(ssh_dir)


def _copy_keys_from_wsl_to_windows(ssh_dir: PosixPath):
    windows_ssh_dir = get_windows_home_path_in_wsl() / ".ssh"
    windows_ssh_dir.mkdir(exist_ok=True)

    for wsl_public_key_path in ssh_dir.glob("*.pub"):
        wsl_private_key_path = wsl_public_key_path.with_suffix("")
        if not wsl_private_key_path.exists():
            logger.warning(
                f"There is a dangling public key at {wsl_public_key_path} with no associated private key!"
            )
            continue
        windows_public_key_path = windows_ssh_dir / wsl_public_key_path.name
        windows_private_key_path = windows_ssh_dir / wsl_private_key_path.name
        if windows_public_key_path.exists() and windows_private_key_path.exists():
            logger.debug(
                f"Windows SSH keypair {windows_private_key_path}, "
                f"{windows_public_key_path} already exists, skipping copy from WSL side."
            )
            continue
        rprint(
            f"Copying SSH keypair from WSL .ssh folder at "
            f"{wsl_private_key_path} to Windows ssh directory at {windows_ssh_dir}"
        )
        if not windows_private_key_path.exists():
            shutil.copy(wsl_private_key_path, windows_private_key_path)
        if not windows_public_key_path.exists():
            shutil.copy(wsl_public_key_path, windows_public_key_path)
        # TODO: make sure that this works okay and does not mess up the permissions on
        # the Windows files!
        windows_private_key_path.chmod(0o600)
        windows_public_key_path.chmod(0o644)

        # Replace the windows line endings with linux ones.
        windows_private_key_path.write_bytes(
            # Double replace since we don't want to end up with '\r\r\n's.
            windows_private_key_path.read_bytes()
            .replace(b"\r\n", b"\n")
            .replace(b"\n", b"\r\n")
        )
        windows_public_key_path.write_bytes(
            windows_public_key_path.read_bytes()
            .replace(b"\r\n", b"\n")
            .replace(b"\n", b"\r\n")
        )


def _copy_keys_from_windows_to_wsl(ssh_dir: PosixPath):
    windows_ssh_dir = get_windows_home_path_in_wsl() / ".ssh"
    windows_ssh_dir.mkdir(exist_ok=True)

    for windows_public_key_path in windows_ssh_dir.glob("*.pub"):
        windows_private_key_path = windows_public_key_path.with_suffix("")
        if not windows_private_key_path.exists():
            logger.warning(
                f"There is a dangling public key at {windows_public_key_path} with no associated private key!"
            )
            continue
        wsl_public_key_path = ssh_dir / windows_public_key_path.name
        wsl_private_key_path = ssh_dir / windows_private_key_path.name
        if wsl_public_key_path.exists() and wsl_private_key_path.exists():
            logger.debug(
                f"WSL SSH keypair {wsl_private_key_path}, {wsl_public_key_path} "
                f"already exists, skipping copy from Windows side."
            )
            continue
        rprint(
            f"Copying SSH keypair from Windows .ssh folder at "
            f"{windows_private_key_path} to WSL ssh directory at {ssh_dir}"
        )
        if not wsl_private_key_path.exists():
            shutil.copy(windows_private_key_path, wsl_private_key_path)
        if not wsl_public_key_path.exists():
            shutil.copy(windows_public_key_path, wsl_public_key_path)
        wsl_private_key_path.chmod(0o600)
        wsl_public_key_path.chmod(0o644)

        # Replace the windows line endings with linux ones.
        wsl_private_key_path.write_bytes(
            wsl_private_key_path.read_bytes().replace(b"\r\n", b"\n")
        )
        wsl_public_key_path.write_bytes(
            wsl_public_key_path.read_bytes().replace(b"\r\n", b"\n")
        )


def setup_drac_ssh_access(
    ssh_dir: Path, ssh_config: SSHConfig, drac_clusters_in_config: list[str]
):
    assert not ON_WINDOWS
    drac_login_nodes: list[RemoteV2] = []
    rprint(
        Panel(
            rich.text.Text("DRAC SETUP", justify="center"),
            box=rich.box.HORIZONTALS,
            style="bold blue",
        )
    )

    #  This should not be the same SSH key as the one used for the Mila cluster!
    mila_private_key = get_ssh_private_key_path(ssh_config, "mila") or next(
        (k.with_suffix("") for k in ssh_dir.glob("id_*.pub")),
        ssh_dir / "id_rsa_mila",  # default private key path for DRAC.
    )
    # Get the private key from the SSH config, otherwise the first key found in the SSH
    # dir, otherwise the default key.
    drac_private_key = get_ssh_private_key_path(
        ssh_config, drac_clusters_in_config[0]
    ) or next(
        (
            k.with_suffix("")
            for k in ssh_dir.glob("id_*.pub")
            if k.with_suffix("") != mila_private_key
        ),
        ssh_dir / "id_rsa_drac",  # default private key path for DRAC.
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
        return []

    display_public_key(drac_public_key, cluster="DRAC")

    # Setup SSH access to the DRAC compute nodes.
    for drac_cluster in drac_clusters_in_config:
        rprint(f"Checking connection to the {drac_cluster} login nodes...")

        private_key_path = (
            get_ssh_private_key_path(ssh_config, drac_cluster) or drac_private_key
        )
        public_key_path = private_key_path.with_suffix(".pub")
        if not (login_node := try_to_login(drac_cluster)):
            assert False, RemoteV2(drac_cluster)

            rprint(f"❌ Unable to `ssh {drac_cluster}`!")
            rprint(
                "[bold]Please submit :arrow_up: your DRAC public key above :arrow_up: "
                "to CCDB using this form: \n"
                f" :arrow_right: [link={DRAC_FORM_URL}]{DRAC_FORM_URL}[/] :arrow_left:\n"
                "\n"
            )
            rprint(
                "Then, wait a few minutes and run this again. If you still have "
                "issues, reach out to DRAC support at support@tech.alliancecan.ca"
            )
            continue

        assert isinstance(login_node, RemoteV2)  # since we're not on windows.
        drac_login_nodes.append(login_node)
        cluster = drac_cluster

        rprint(f"✅ Able to `ssh {cluster}`")
        rprint(f"Checking connection to the {cluster} compute nodes... ")
        if can_access_compute_nodes(login_node, public_key_path=public_key_path):
            rprint(
                f"✅ Local {public_key_path} is in ~/.ssh/authorized_keys on the "
                f"{cluster} cluster, and the permissions are correct."
            )
            continue  # all setup, go to the next cluster in the list.
        rprint(
            f"❌ Local {public_key_path} is not in ~/.ssh/authorized_keys on the "
            f"{cluster} cluster, or file permissions are incorrect. Attempting to fix "
            f"this now."
        )
        setup_access_to_compute_nodes(
            cluster, remote=login_node, public_key_path=public_key_path
        )
        if not can_access_compute_nodes(login_node, public_key_path=public_key_path):
            warnings.warn(
                RuntimeWarning(
                    f"Unable to setup SSH access to the compute nodes of the {cluster} "
                    f"cluster! Please reach out to DRAC support at "
                    f"support@tech.alliancecan.ca or check for similar issues on the "
                    f"#compute-canada or #milatools Slack channels."
                )
            )

        rprint(
            f"✅ Local {public_key_path} is in ~/.ssh/authorized_keys on the "
            f"{cluster} cluster and file permissions are correct. You should now "
            f"be able to connect to compute nodes with SSH."
        )


def create_ssh_keypair_and_check_exists(
    cluster: str, private_key: Path, public_key: Path
):
    assert not private_key.exists()
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
    # `passphrase=None` lets the user set a passphrase (or not) interactively in ssh-keygen

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
    subtitle: str = "( ⬆️  This is what you should Copy & paste if asked ⬆️  )",
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
    from milatools.cli import console

    console.rule(f" Your public key for the {cluster} cluster ({public_key})")
    print(public_key.read_text())
    console.rule(subtitle)
    return


def try_to_login(cluster: str) -> RemoteV2 | RemoteV1 | None:
    try:
        return RemoteV2(cluster) if not ON_WINDOWS else RemoteV1(cluster)
    except Exception:
        return None


def can_access_compute_nodes(
    login_node: RemoteV2 | RemoteV1, public_key_path: Path
) -> bool:
    if not public_key_path.exists():
        return False
    public_key = public_key_path.read_text().strip()
    assert public_key
    try:
        authorized_keys = login_node.get_output(
            "cat ~/.ssh/authorized_keys", hide=True, warn=True, display=False
        ).splitlines()
    except subprocess.CalledProcessError:
        return False

    if public_key not in authorized_keys:
        return False

    if login_node.get_output("stat -c %a ~/.ssh/authorized_keys") != "600":
        logger.info(
            "Permissions for ~/.ssh/authorized_keys on the login node are not correct!"
        )
        return False
    if sshdir_permissions := login_node.get_output("stat -c %a ~/.ssh") != "700":
        logger.info(
            f"Permissions for ~/.ssh on the login node are {sshdir_permissions}, "
            "should be 700!"
        )
        return False

    home_permissions = login_node.get_output("stat -c %a ~")
    # Need to be 700 ideally, but actually just need for others not to have the 'w'
    # permission.
    _has_write_perm = ["2", "3", "6", "7"]  # 010, 011, 110, 111

    if home_permissions[1] in _has_write_perm or home_permissions[2] in _has_write_perm:
        logger.info(
            f"Permissions on the $HOME directory on the {login_node.hostname} cluster "
            f"is {home_permissions}, but it should be drwx------ (700), or "
            "group/others should not have write permissions!"
        )
        return False
    return True


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
        - an SSHConfig object used to read / write config options.
    """
    ssh_config, _, _ = _setup_ssh_config(ssh_config_path)
    return ssh_config


def _setup_ssh_config(
    ssh_config_path: str | Path = "~/.ssh/config",
) -> tuple[SSHConfig, str | None, str | None]:
    """Interactively set up the ~/.ssh/config file with entries for clusters."""
    ssh_config_path = _setup_ssh_config_file(ssh_config_path)
    ssh_config = SSHConfig(ssh_config_path)
    initial_config_text = ssh_config.cfg.config()

    mila_username: str | None = _get_mila_username(ssh_config)
    drac_username: str | None = _get_drac_username(ssh_config)
    mila_private_key_path = get_ssh_private_key_path(ssh_config, "mila") or (
        Path.home() / ".ssh/id_rsa_mila"
    )
    if mila_username:
        # Check for *.server.mila.quebec in ssh config, to connect to compute nodes
        old_cnode_pattern = "*.server.mila.quebec"
        if old_cnode_pattern in ssh_config.hosts():
            logger.info(
                f"The '{old_cnode_pattern}' entry in ~/.ssh/config is too general and "
                "should exclude login.server.mila.quebec. Fixing this."
            )
            ssh_config.rename(
                old_cnode_pattern, "*.server.mila.quebec !*login.server.mila.quebec"
            )

        for hostname, entry in MILA_ENTRIES.copy().items():
            # todo: Do we want to set the `IdentityFile` value to the ssh key path?
            entry.update(User=mila_username)
            if not mila_private_key_path.name.startswith("id_"):
                # Need to add the IdentityFile entry only if the key doesn't have a standard name.
                entry.update(IdentityFile=str(mila_private_key_path))
            _add_ssh_entry(ssh_config, hostname, entry)
            # if "ControlPath" in entry:
            _make_controlpath_dir(entry)

    if drac_username:
        logger.debug(
            f"Adding entries for the DRAC/PAICE clusters to {ssh_config_path}."
        )
        drac_private_key_path = None
        for hostname, entry in DRAC_ENTRIES.items():
            entry = entry.copy()
            if drac_private_key_path is None:
                # Get the private key from the SSH config, otherwise the first key found in the SSH
                # dir, otherwise the default key.
                drac_private_key_path = get_ssh_private_key_path(
                    ssh_config, hostname
                ) or next(
                    (
                        k.with_suffix("")
                        for k in ssh_config_path.parent.glob("*.pub")
                        # Try to find a different key than the one used for Mila.
                        if k.with_suffix("") != mila_private_key_path
                    ),
                    ssh_config_path.parent
                    / "id_rsa_drac",  # default private key path for DRAC.
                )
                if not drac_private_key_path.exists():
                    create_ssh_keypair_and_check_exists(
                        hostname,
                        drac_private_key_path,
                        drac_private_key_path.with_suffix(".pub"),
                    )

                # If we can find the private key used for one of the DRAC clusters,
                # then use that same key for all the drac clusters.
                # This needs to be set for the compute nodes too.

            entry.update(User=drac_username)
            if not drac_private_key_path.name.startswith("id_"):
                # Need to add the IdentityFile entry only if the key doesn't have a standard name.
                entry.update(IdentityFile=str(drac_private_key_path))
            _add_ssh_entry(ssh_config, hostname, entry)
            _make_controlpath_dir(entry)

    new_config_text = ssh_config.cfg.config()
    if initial_config_text == new_config_text:
        rprint("Did not change ssh config")
        return ssh_config, mila_username, drac_username
    if not _confirm_changes(ssh_config, previous=initial_config_text):
        exit("Refused changes to ssh config.")
        # return False, original_config

    ssh_config.save()
    rprint(f"Wrote {ssh_config_path}")
    return ssh_config, mila_username, drac_username


def setup_windows_ssh_config_from_wsl(ssh_dir: PosixPath, linux_ssh_config: SSHConfig):
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
    copy_ssh_keys_between_wsl_and_windows(ssh_dir)


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


def get_ssh_public_key_path(
    hostname: str, ssh_config: SSHConfig | None = None
) -> Path | None:
    if ssh_config is None:
        ssh_config = SSHConfig(SSH_CONFIG_FILE)
    identity_file = ssh_config.lookup(hostname).get("identityfile", None)
    # Seems to be a list for some reason?
    if isinstance(identity_file, list):
        assert identity_file
        identity_file = identity_file[0]
    if identity_file:
        return Path(identity_file).expanduser().with_suffix(".pub")
    # No IdentityFile specified in config, try to guess.
    public_keys = list((Path.home() / ".ssh").glob("*.pub"))
    if len(public_keys) == 1:
        return public_keys[0]
    if not public_keys:
        return None
    # There are multiple public keys, and the config doesn't specify which one to use.
    # Use some common-sense heuristics, and warn if we can't make a good guess.

    for public_key_path in public_keys:
        if hostname in public_key_path.name:
            # for example id_rsa_mila.pub
            return public_key_path

    public_keys = sorted(public_keys, key=lambda p: p.stat().st_mtime, reverse=True)
    guessed_key_path = public_keys[0]
    warnings.warn(
        RuntimeWarning(
            f"The SSH config does not specify which key is to be used for host {hostname}, "
            f"and there are multiple public keys in the ~/.ssh directory! We will use "
            f"the most recent key at path {guessed_key_path}"
        )
    )
    return guessed_key_path


def get_ssh_private_key_path(ssh_config: SSHConfig, hostname: str) -> Path | None:
    pubkey = get_ssh_public_key_path(hostname, ssh_config=ssh_config)
    if pubkey:
        return pubkey.with_suffix("")
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
                # On the Mila cluster, the ~/.ssh/authorized_keys file is not used when
                # connecting to login nodes. Therefore ssh-copy-id does not add the key
                # to that file. We have to use the `-f` flag to force it to add the key.
                *(["-f"] if cluster == "mila" else []),
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


def setup_access_to_compute_nodes(
    cluster: str, remote: RemoteV1 | RemoteV2, public_key_path: Path
):
    #####################################
    # Step 3: Set up keys on login node #
    #####################################
    if not public_key_path.exists():
        raise RuntimeError(f"Public key {public_key_path} does not exist!")
    public_key = public_key_path.read_text().strip()
    rprint(
        f"Checking connection to compute nodes on the {cluster} cluster. "
        "This is required for `mila code` to work properly."
    )
    try:
        authorized_keys = remote.get_output(
            "cat ~/.ssh/authorized_keys", hide=True, warn=True
        ).splitlines()
    except subprocess.CalledProcessError as err:
        logger.error(f"Unable to get the authorized keys: {err}")
        authorized_keys = []

    if public_key in authorized_keys:
        rprint(
            f"✅ Your public key is already present in ~/.ssh/authorized_keys on the {cluster} cluster."
        )
    else:
        remote.run("mkdir -p ~/.ssh", display=True, hide=False)
        remote.run(
            f"echo '{public_key}' >> ~/.ssh/authorized_keys", display=True, hide=False
        )
        remote.run("chmod 600 ~/.ssh/authorized_keys", display=True, hide=False)
        remote.run("chmod 700 ~/.ssh", display=True, hide=False)
        remote.run("chmod go-w ~", display=True, hide=False)
        rprint(
            f"✅ Your public key is now present in ~/.ssh/authorized_keys on the "
            f"{cluster} cluster, and file permissions are correct."
        )


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
    windows_home = subprocess.getoutput("powershell.exe '$env:USERPROFILE'").strip()
    return windows_home


def create_ssh_keypair(
    ssh_private_key_path: Path | None,
    local: LocalV2 | None = None,
    passphrase: str | None = "",
) -> Path:
    """Creates a public/private key pair at the given path using ssh-keygen.

    If passphrase is `None`, ssh-keygen will prompt the user for a passphrase.
    Otherwise, if passphrase is an empty string, no passphrase will be used (default).
    If a string is passed, it is passed to ssh-keygen and used as the passphrase.
    """
    local = local or LocalV2()
    command = [
        "ssh-keygen",
        "-t",
        "rsa",
    ] + (["-f", str(ssh_private_key_path.expanduser())] if ssh_private_key_path else [])

    if passphrase is not None:
        command.extend(["-N", passphrase])
    display(command)
    out = subprocess.check_output(command)
    if not ssh_private_key_path:
        raise NotImplementedError(
            f"TODO: Find the new key path from the output of ssh-keygen: {out}"
        )
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
            vscode_settings_json_path, new_values=VSCODE_SETTINGS
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
    """Retrieve or ask the user for their username on the ComputeCanada/DRAC
    clusters."""
    return get_username_on_cluster(
        ssh_config,
        cluster_hostname="mila",
        cluster_full_name="Mila",
    )


def _get_drac_username(ssh_config: SSHConfig) -> str | None:
    """Retrieve or ask the user for their username on the ComputeCanada/DRAC
    clusters."""
    return get_username_on_cluster(
        ssh_config,
        cluster_hostname=DRAC_CLUSTERS,
        cluster_full_name="DRAC/ComputeCanada",
    )


def get_username_on_cluster(
    ssh_config: SSHConfig,
    cluster_hostname: str | list[str],
    cluster_full_name: str,
) -> str | None:
    cluster_hostnames: list[str]
    if isinstance(cluster_hostname, str):
        cluster_hostnames = [cluster_hostname]
    else:
        cluster_hostnames = cluster_hostname
    s = "s" if len(cluster_hostnames) > 1 else ""

    for cluster_hostname in cluster_hostnames:
        if user := ssh_config.lookup(cluster_hostname).get("user"):
            return user.strip()
    if not yn(f"Do you have an account on the {cluster_full_name} cluster{s}?"):
        return None

    while not (
        username := rich.prompt.Prompt.ask(
            f"What's your username on the {cluster_full_name} cluster{s}?\n"
        )
    ).strip():
        rich.print("[red]Please enter a valid username.[/red]")
        pass
    return None if username.isspace() else username.strip()


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
    """Adds or updates an entry in the ssh config object.

    - If an entry already exists and matches the given host, it updates the entry.
    """
    # NOTE: `Host` is also a parameter to make sure it isn't in `entry`.
    assert "Host" not in entry

    sorted_by_keys = False

    # not quite true:
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
        windows_ssh_entry = {
            key: value
            for key, value in linux_ssh_entry.items()
            if key.lower() not in unsupported_keys_lowercase
        }

        if identityfile := linux_ssh_entry.get("IdentityFile"):
            # Tricky: need to remap the path to the Windows path.
            identityfile_path = Path(identityfile).expanduser().resolve()
            windows_identityfile = (
                get_windows_home_path_in_wsl()
                / ".ssh"
                / identityfile_path.relative_to(Path.home() / ".ssh")
            )
            windows_ssh_entry["IdentityFile"] = str(windows_identityfile)

        _add_ssh_entry(windows_ssh_config, host, windows_ssh_entry)


def _make_controlpath_dir(entry: dict[str, str | int]) -> None:
    if "ControlPath" not in entry:
        return
    control_path = entry["ControlPath"]
    assert isinstance(control_path, str)
    # Create the ControlPath directory if it doesn't exist:
    control_path_dir = Path(control_path).expanduser().parent
    control_path_dir.mkdir(exist_ok=True, parents=True)
