"""Tools to connect to and interact with the Mila cluster.

Cluster documentation: https://docs.mila.quebec/
"""
from __future__ import annotations

import argparse
import operator
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import traceback
import typing
import webbrowser
from argparse import ArgumentParser, _HelpAction
from contextlib import ExitStack
from logging import getLogger as get_logger
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlencode

import questionary as qn
from invoke.exceptions import UnexpectedExit
from typing_extensions import TypedDict

from ..version import version as mversion
from .init_command import (
    create_ssh_keypair,
    setup_ssh_config,
    setup_vscode_settings,
    setup_windows_ssh_config_from_wsl,
)
from .local import Local
from .profile import ensure_program, setup_profile
from .remote import Remote, SlurmRemote
from .utils import (
    CommandNotFoundError,
    MilatoolsUserError,
    SSHConnectionError,
    T,
    get_fully_qualified_name,
    qualified,
    randname,
    running_inside_WSL,
    with_control_file,
    yn,
)

logger = get_logger(__name__)
if typing.TYPE_CHECKING:
    from typing_extensions import Unpack


def main():
    if sys.platform != "win32" and get_fully_qualified_name().endswith(
        ".server.mila.quebec"
    ):
        exit(
            "ERROR: 'mila ...' should be run on your local machine and not on the Mila "
            "cluster"
        )

    try:
        mila()
    except MilatoolsUserError as exc:
        # These are user errors and should not be reported
        print("ERROR:", exc, file=sys.stderr)
    except SSHConnectionError as err:
        # These are errors coming from paramiko's failure to connect to the
        # host
        print("ERROR:", f"{err}", file=sys.stderr)
    except Exception:
        print(T.red(traceback.format_exc()), file=sys.stderr)
        command = sys.argv[1] if len(sys.argv) > 1 else None
        options = {
            "labels": ",".join([command, mversion] if command else [mversion]),
            "template": "bug_report.md",
            "title": f"[v{mversion}] Issue running the command "
            + (f"`mila {command}`" if command else "`mila`"),
        }
        github_issue_url = (
            f"https://github.com/mila-iqia/milatools/issues/new?{urlencode(options)}"
        )
        print(
            T.bold_yellow(
                f"An error occurred during the execution of the command "
                f"`{command}`. "
            )
            + T.yellow(
                "Please try updating milatools by running\n"
                "  pip install milatools --upgrade\n"
                "in the terminal. If the issue persists, consider filling a bug "
                "report at\n  "
            )
            + T.italic_yellow(github_issue_url)
            + T.yellow(
                "\nPlease provide the error traceback with the report "
                "(the red text above)."
            ),
            file=sys.stderr,
        )
        exit(1)


def mila():
    parser = ArgumentParser(prog="mila", description=__doc__, add_help=True)
    parser.add_argument(
        "--version",
        "-v",
        action="version",
        version=f"milatools v{mversion}",
        help="Milatools version",
    )

    subparsers = parser.add_subparsers(required=True, dest="<command>")

    # ----- mila docs ------

    docs_parser = subparsers.add_parser(
        "docs",
        help="Open the Mila cluster documentation.",
        formatter_class=SortingHelpFormatter,
    )
    docs_parser.add_argument("SEARCH", nargs=argparse.REMAINDER, help="Search terms")
    docs_parser.set_defaults(function=docs)

    # ----- mila intranet ------

    intranet_parser = subparsers.add_parser(
        "intranet", help="Open the Mila intranet in a browser."
    )
    intranet_parser.add_argument(
        "SEARCH", nargs=argparse.REMAINDER, help="Search terms"
    )
    intranet_parser.set_defaults(function=intranet)

    # ----- mila init ------

    init_parser = subparsers.add_parser(
        "init",
        help="Set up your configuration and credentials.",
        formatter_class=SortingHelpFormatter,
    )
    init_parser.set_defaults(function=init)

    # ----- mila forward ------

    forward_parser = subparsers.add_parser(
        "forward",
        help="Forward a port on a compute node to your local machine.",
        formatter_class=SortingHelpFormatter,
    )
    forward_parser.add_argument("REMOTE", help="node:port to forward")
    forward_parser.add_argument(
        "--page",
        help="String to append after the URL",
        default=None,
        metavar="VALUE",
    )
    forward_parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port to open on the local machine",
        metavar="VALUE",
    )
    forward_parser.set_defaults(function=forward)

    # ----- mila code ------

    code_parser = subparsers.add_parser(
        "code",
        help="Open a remote VSCode session on a compute node.",
        formatter_class=SortingHelpFormatter,
    )
    code_parser.add_argument(
        "PATH", help="Path to open on the remote machine", type=str
    )
    code_parser.add_argument(
        "--alloc",
        nargs=argparse.REMAINDER,
        help="Extra options to pass to slurm",
        metavar="VALUE",
        default=[],
    )
    code_parser.add_argument(
        "--command",
        default=os.environ.get("MILATOOLS_CODE_COMMAND", "code"),
        help=(
            "Command to use to start vscode\n"
            '(defaults to "code" or the value of $MILATOOLS_CODE_COMMAND)'
        ),
        metavar="VALUE",
    )
    code_parser.add_argument(
        "--job",
        type=str,
        default=None,
        help="Job ID to connect to",
        metavar="VALUE",
    )
    code_parser.add_argument(
        "--node",
        type=str,
        default=None,
        help="Node to connect to",
        metavar="VALUE",
    )
    code_parser.add_argument(
        "--persist",
        action="store_true",
        help="Whether the server should persist or not",
    )
    code_parser.set_defaults(function=code)

    # ----- mila serve ------

    serve_parser = subparsers.add_parser(
        "serve",
        help="Start services on compute nodes and forward them to your local machine.",
        formatter_class=SortingHelpFormatter,
    )
    serve_subparsers = serve_parser.add_subparsers(
        dest="<serve_subcommand>", required=True
    )

    # ----- mila serve connect ------

    serve_connect_parser = serve_subparsers.add_parser(
        "connect",
        help="Reconnect to a persistent server.",
        formatter_class=SortingHelpFormatter,
    )
    serve_connect_parser.add_argument(
        "IDENTIFIER",
        type=str,
        help="Server identifier output by the original mila serve command",
    )
    serve_connect_parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port to open on the local machine",
        metavar="VALUE",
    )
    serve_connect_parser.set_defaults(function=connect)

    # ----- mila serve kill ------

    serve_kill_parser = serve_subparsers.add_parser(
        "kill",
        help="Kill a persistent server.",
        formatter_class=SortingHelpFormatter,
    )
    serve_kill_parser.add_argument(
        "IDENTIFIER",
        type=str,
        nargs="?",
        default=None,
        help="Server identifier output by the original mila serve command",
    )
    serve_kill_parser.add_argument(
        "--all", action="store_true", help="Kill all servers"
    )
    serve_kill_parser.set_defaults(function=kill)

    # ----- mila serve list ------

    serve_list_parser = serve_subparsers.add_parser(
        "list",
        help="List active servers.",
        formatter_class=SortingHelpFormatter,
    )
    serve_list_parser.add_argument(
        "--purge", action="store_true", help="Purge dead or invalid servers"
    )
    serve_list_parser.set_defaults(function=serve_list)

    # ----- mila serve lab ------

    serve_lab_parser = serve_subparsers.add_parser(
        "lab",
        help="Start a Jupyterlab server.",
        formatter_class=SortingHelpFormatter,
    )
    serve_lab_parser.add_argument(
        "PATH",
        default=None,
        nargs="?",
        help="Path to open on the remote machine",
    )
    _add_standard_server_args(serve_lab_parser)
    serve_lab_parser.set_defaults(function=lab)

    # ----- mila serve notebook ------

    serve_notebook_parser = serve_subparsers.add_parser(
        "notebook",
        help="Start a Jupyter Notebook server.",
        formatter_class=SortingHelpFormatter,
    )
    serve_notebook_parser.add_argument(
        "PATH",
        default=None,
        nargs="?",
        help="Path to open on the remote machine",
    )
    _add_standard_server_args(serve_notebook_parser)
    serve_notebook_parser.set_defaults(function=notebook)

    # ----- mila serve tensorboard ------

    serve_tensorboard_parser = serve_subparsers.add_parser(
        "tensorboard",
        help="Start a Tensorboard server.",
        formatter_class=SortingHelpFormatter,
    )
    serve_tensorboard_parser.add_argument(
        "LOGDIR", type=str, help="Path to the experiment logs"
    )
    _add_standard_server_args(serve_tensorboard_parser)
    serve_tensorboard_parser.set_defaults(function=tensorboard)

    # ----- mila serve mlflow ------

    serve_mlflow_parser = serve_subparsers.add_parser(
        "mlflow",
        help="Start an MLFlow server.",
        formatter_class=SortingHelpFormatter,
    )
    serve_mlflow_parser.add_argument(
        "LOGDIR", type=str, help="Path to the experiment logs"
    )
    _add_standard_server_args(serve_mlflow_parser)
    serve_mlflow_parser.set_defaults(function=mlflow)

    # ----- mila serve aim ------

    serve_aim_parser = serve_subparsers.add_parser(
        "aim",
        help="Start an AIM server.",
        formatter_class=SortingHelpFormatter,
    )
    serve_aim_parser.add_argument(
        "LOGDIR", type=str, help="Path to the experiment logs"
    )
    _add_standard_server_args(serve_aim_parser)
    serve_aim_parser.set_defaults(function=aim)

    args = parser.parse_args()
    args_dict = vars(args)
    function = args_dict.pop("function")
    _ = args_dict.pop("<command>")
    _ = args_dict.pop("<serve_subcommand>", None)
    # replace SEARCH -> "search", REMOTE -> "remote", etc.
    args_dict = _convert_uppercase_keys_to_lowercase(args_dict)
    assert callable(function)
    return function(**args_dict)


def _convert_uppercase_keys_to_lowercase(args_dict: dict[str, Any]) -> dict[str, Any]:
    return {(k.lower() if k.isupper() else k): v for k, v in args_dict.items()}


def docs(search: Sequence[str]) -> None:
    url = "https://docs.mila.quebec"
    if search:
        terms = "+".join(search)
        url = f"{url}/search.html?q={terms}"
    print(f"Opening the docs: {url}")
    webbrowser.open(url)


def intranet(search: Sequence[str]) -> None:
    """Open the Mila intranet in a browser."""
    if search:
        terms = "+".join(search)
        url = f"https://sites.google.com/search/mila.quebec/mila-intranet?query={terms}&scope=site&showTabs=false"
    else:
        url = "https://intranet.mila.quebec"
    print(f"Opening the intranet: {url}")
    webbrowser.open(url)


def init():
    """Set up your configuration and credentials."""

    #############################
    # Step 1: SSH Configuration #
    #############################

    print("Checking ssh config")

    ssh_config = setup_ssh_config()
    print("# OK")

    # if we're running on WSL, we actually just copy the id_rsa + id_rsa.pub and the
    # ~/.ssh/config to the Windows ssh directory (taking care to remove the
    # ControlMaster-related entries) so that the user doesn't need to install Python on
    # the Windows side.
    if running_inside_WSL():
        setup_windows_ssh_config_from_wsl(linux_ssh_config=ssh_config)

    setup_passwordless_ssh_access()
    setup_keys_on_login_node()
    setup_vscode_settings()
    print_welcome_message()


def setup_passwordless_ssh_access():
    print("Checking passwordless authentication")

    here = Local()

    # Check that there is an id file
    ssh_private_key_path = Path.home() / ".ssh" / "id_rsa"

    sshdir = os.path.expanduser("~/.ssh")
    if not any(
        entry.startswith("id") and entry.endswith(".pub")
        for entry in os.listdir(sshdir)
    ):
        if yn("You have no public keys. Generate one?"):
            # Run ssh-keygen with the given location and no passphrase.
            create_ssh_keypair(ssh_private_key_path, here)
        else:
            exit("No public keys.")

    # Check that it is possible to connect using the key

    if not here.check_passwordless("mila"):
        if yn(
            "Your public key does not appear be registered on the cluster. Register it?"
        ):
            # NOTE: If we're on a Windows machine, we do something different here:
            if sys.platform == "win32":
                command = (
                    "powershell.exe type $env:USERPROFILE\\.ssh\\id_rsa.pub | ssh mila "
                    '"cat >> ~/.ssh/authorized_keys"'
                )
                here.run(command)
            else:
                here.run("ssh-copy-id", "mila")
            if not here.check_passwordless("mila"):
                exit("ssh-copy-id appears to have failed")
        else:
            exit("No passwordless login.")


def setup_keys_on_login_node():
    print("Checking connection to compute nodes")

    remote = Remote("mila")
    try:
        pubkeys = remote.get_lines("ls -t ~/.ssh/id*.pub")
        print("# OK")
    except UnexpectedExit:
        print("# MISSING")
        if yn("You have no public keys on the login node. Generate them?"):
            # print("(Note: You can just press Enter 3x to accept the defaults)")
            # _, keyfile = remote.extract(
            #     "ssh-keygen",
            #     pattern="Your public key has been saved in ([^ ]+)",
            #     wait=True,
            # )
            private_file = "~/.ssh/id_rsa"
            remote.run(f'ssh-keygen -q -t rsa -N "" -f {private_file}')
            pubkeys = [f"{private_file}.pub"]
        else:
            exit("Cannot proceed because there is no public key")

    common = remote.with_bash().get_output(
        "comm -12 <(sort ~/.ssh/authorized_keys) <(sort ~/.ssh/*.pub)"
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


def forward(
    remote: str,
    page: str | None,
    port: int | None,
):
    """Forward a port on a compute node to your local machine."""
    node, remote_port = remote.split(":")
    try:
        remote_port = int(remote_port)
    except ValueError:
        pass

    local_proc, _ = _forward(
        local=Local(),
        node=f"{node}.server.mila.quebec",
        to_forward=remote_port,
        page=page,
        port=port,
    )

    try:
        local_proc.wait()
    except KeyboardInterrupt:
        exit("Terminated by user.")
    finally:
        local_proc.kill()


def code(
    path: str,
    command: str,
    persist: bool,
    job: str | None,
    node: str | None,
    alloc: Sequence[str],
):
    """Open a remote VSCode session on a compute node.

    Arguments:
        path: Path to open on the remote machine
        command: Command to use to start vscode
            (defaults to "code" or the value of $MILATOOLS_CODE_COMMAND)
        persist: Whether the server should persist or not
        job: Job ID to connect to
        node: Node to connect to
        alloc: Extra options to pass to slurm
    """
    here = Local()
    remote = Remote("mila")

    if command is None:
        command = os.environ.get("MILATOOLS_CODE_COMMAND", "code")

    try:
        check_disk_quota(remote)
    except MilatoolsUserError:
        raise
    except Exception as exc:
        logger.warning(f"Unable to check the disk-quota on the cluster: {exc}")

    cnode = _find_allocation(
        remote, job_name="mila-code", job=job, node=node, alloc=alloc
    )
    if persist:
        cnode = cnode.persist()
    data, proc = cnode.ensure_allocation()

    node_name = data["node_name"]

    if not path.startswith("/"):
        # Get $HOME because we have to give the full path to code
        home = remote.home()
        path = "/".join([home, path])

    command_path = shutil.which(command)
    if not command_path:
        raise CommandNotFoundError(command)
    qualified_node_name = qualified(node_name)

    # Try to detect if this is being run from within the Windows Subsystem for Linux.
    # If so, then we run `code` through a powershell.exe command to open VSCode without
    # issues.
    inside_WSL = running_inside_WSL()
    try:
        while True:
            if inside_WSL:
                here.run(
                    "powershell.exe",
                    "code",
                    "-nw",
                    "--remote",
                    f"ssh-remote+{qualified_node_name}",
                    path,
                )
            else:
                here.run(
                    command_path,
                    "-nw",
                    "--remote",
                    f"ssh-remote+{qualified_node_name}",
                    path,
                )
            print(
                "The editor was closed. Reopen it with <Enter>"
                " or terminate the process with <Ctrl+C>"
            )
            input()

    except KeyboardInterrupt:
        if not persist:
            if proc is not None:
                proc.kill()
            print(f"Ended session on '{node_name}'")

    if persist:
        print("This allocation is persistent and is still active.")
        print("To reconnect to this node:")
        print(T.bold(f"  mila code {path} --node {node_name}"))
        print("To kill this allocation:")
        assert "jobid" in data
        print(T.bold(f"  ssh mila scancel {data['jobid']}"))


def connect(identifier: str, port: int | None):
    """Reconnect to a persistent server."""

    remote = Remote("mila")
    info = _get_server_info(remote, identifier)
    local_proc, _ = _forward(
        local=Local(),
        node=f"{info['node_name']}.server.mila.quebec",
        to_forward=info["to_forward"],
        options={"token": info.get("token", None)},
        port=port or int(info["local_port"]),
        through_login=info["host"] == "0.0.0.0",
    )

    try:
        local_proc.wait()
    except KeyboardInterrupt:
        exit("Terminated by user.")
    finally:
        local_proc.kill()


def kill(identifier: str | None, all: bool = False):
    """Kill a persistent server."""
    remote = Remote("mila")

    if all:
        for identifier in remote.get_lines("ls .milatools/control", hide=True):
            assert isinstance(identifier, str)  # note: was implicit before.
            info = _get_server_info(remote, identifier, hide=True)
            if "jobid" in info:
                remote.run(f"scancel {info['jobid']}")
            remote.run(f"rm .milatools/control/{identifier}")

    elif identifier is None:
        exit("Please give the name of the server to kill")

    else:
        info = _get_server_info(remote, identifier)

        remote.run(f"scancel {info['jobid']}")
        remote.run(f"rm .milatools/control/{identifier}")


def serve_list(purge: bool):
    """List active servers."""
    remote = Remote("mila")

    to_purge = []

    remote.run("mkdir -p ~/.milatools/control", hide=True)

    for identifier in remote.get_lines("ls .milatools/control", hide=True):
        info = _get_server_info(remote, identifier, hide=True)
        jobid = info.get("jobid", None)
        status = remote.get_output(f"squeue -j {jobid} -ho %T", hide=True, warn=True)
        program = info.pop("program", "???")
        if status == "RUNNING":
            necessary_keys = {"node_name", "to_forward"}
            if any(k not in info for k in necessary_keys):
                qn.print(f"{identifier} ({program}, MISSING INFO)", style="bold red")
                to_purge.append((identifier, jobid))
            else:
                qn.print(f"{identifier} ({program})", style="bold yellow")
        else:
            qn.print(f"{identifier} ({program}, DEAD)", style="bold red")
            to_purge.append((identifier, None))
        for k, v in info.items():
            print(f"    {k:20} : {v}")

    if purge:
        for identifier, jobid in to_purge:
            if jobid is not None:
                remote.run(f"scancel {jobid}")
            remote.run(f"rm .milatools/control/{identifier}")


class StandardServerArgs(TypedDict):
    alloc: Sequence[str]
    """Extra options to pass to slurm."""

    job: str | None
    """Job ID to connect to."""

    name: str | None
    """Name of the persistent server."""

    node: str | None
    """Node to connect to."""

    persist: bool
    """Whether the server should persist or not."""

    port: int | None
    """Port to open on the local machine."""

    profile: str | None
    """Name of the profile to use."""


def lab(path: str | None, **kwargs: Unpack[StandardServerArgs]):
    """Start a Jupyterlab server.

    Arguments:
        path: Path to open on the remote machine
    """

    if path and path.endswith(".ipynb"):
        exit("Only directories can be given to the mila serve lab command")

    _standard_server(
        path,
        program="jupyter-lab",
        installers={
            "conda": "conda install -y jupyterlab",
            "pip": "pip install jupyterlab",
        },
        command="jupyter lab --sock {sock} {path}",
        # command="jupyter lab --ip {host} --port 0",
        token_pattern=r"\?token=([a-f0-9]+)",
        **kwargs,
    )


def notebook(path: str | None, **kwargs: Unpack[StandardServerArgs]):
    """Start a Jupyter Notebook server.

    Arguments:
        path: Path to open on the remote machine
    """
    if path and path.endswith(".ipynb"):
        exit("Only directories can be given to the mila serve notebook command")

    _standard_server(
        path,
        program="jupyter-notebook",
        installers={
            "conda": "conda install -y jupyter",
            "pip": "pip install jupyter",
        },
        command="jupyter notebook --sock {sock} {path}",
        # command="jupyter notebook --ip {host} --port 0",
        token_pattern=r"\?token=([a-f0-9]+)",
        **kwargs,
    )


def tensorboard(logdir: str, **kwargs: Unpack[StandardServerArgs]):
    """Start a Tensorboard server.

    Arguments:
        logdir: Path to the experiment logs
    """

    _standard_server(
        logdir,
        program="tensorboard",
        installers={
            "conda": "conda install -y tensorboard",
            "pip": "pip install tensorboard",
        },
        command="tensorboard --logdir {path} --host {host} --port 0",
        port_pattern="TensorBoard [^ ]+ at http://[^:]+:([0-9]+)/",
        **kwargs,
    )


def mlflow(logdir: str, **kwargs: Unpack[StandardServerArgs]):
    """Start an MLFlow server.

    Arguments:
        logdir: Path to the experiment logs
    """

    _standard_server(
        logdir,
        program="mlflow",
        installers={
            "pip": "pip install mlflow",
        },
        command="mlflow ui --backend-store-uri {path} --host {host} --port 0",
        port_pattern="Listening at: http://[^:]+:([0-9]+)",
        **kwargs,
    )


def aim(logdir: str, **kwargs: Unpack[StandardServerArgs]):
    """Start an AIM server.

    Arguments:
        logdir: Path to the experiment logs
    """
    _standard_server(
        logdir,
        program="aim",
        installers={
            "pip": "pip install aim",
        },
        command="aim up --repo {path} --host {host} --port 0",
        port_pattern=r"Open http://[^:]+:([0-9]+)",
        **kwargs,
    )


def _get_server_info(
    remote: Remote, identifier: str, hide: bool = False
) -> dict[str, str]:
    text = remote.get_output(f"cat .milatools/control/{identifier}", hide=hide)
    info = dict(line.split(" = ") for line in text.split("\n") if line)
    return info


class SortingHelpFormatter(argparse.HelpFormatter):
    """Taken and adapted from https://stackoverflow.com/a/12269143/6388696."""

    def add_arguments(self, actions):
        actions = sorted(actions, key=operator.attrgetter("option_strings"))
        # put help actions first.
        actions = sorted(
            actions, key=lambda action: not isinstance(action, _HelpAction)
        )
        super().add_arguments(actions)


def _add_standard_server_args(parser: ArgumentParser):
    parser.add_argument(
        "--alloc",
        nargs=argparse.REMAINDER,
        help="Extra options to pass to slurm",
        metavar="VALUE",
        default=[],
    )
    parser.add_argument(
        "--job",
        type=str,
        default=None,
        help="Job ID to connect to",
        metavar="VALUE",
    )
    parser.add_argument(
        "--name",
        default=None,
        type=str,
        help="Name of the persistent server",
        metavar="VALUE",
    )
    parser.add_argument(
        "--node",
        type=str,
        default=None,
        help="Node to connect to",
        metavar="VALUE",
    )
    parser.add_argument(
        "--persist",
        action="store_true",
        help="Whether the server should persist or not",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port to open on the local machine",
        metavar="VALUE",
    )
    parser.add_argument(
        "--profile",
        default=None,
        type=str,
        help="Name of the profile to use",
        metavar="VALUE",
    )


def _standard_server(
    path: str | None,
    *,
    program: str,
    installers: dict[str, str],
    command: str,
    profile: str | None,
    persist: bool,
    port: int | None,
    name: str | None,
    node: str | None,
    job: str | None,
    alloc: Sequence[str],
    port_pattern=None,
    token_pattern=None,
):
    # Make the server visible from the login node (other users will be able to connect)
    # Temporarily disabled
    share = False

    if name is not None:
        persist = True
    elif persist:
        name = program

    remote = Remote("mila")

    path = path or "~"
    if path == "~" or path.startswith("~/"):
        path = remote.home() + path[1:]

    results: dict | None = None
    node_name: str | None = None
    to_forward: int | str | None = None
    cf: str | None = None
    proc = None
    with ExitStack() as stack:
        if persist:
            cf = stack.enter_context(with_control_file(remote, name=name))
        else:
            cf = None

        if profile:
            prof = f"~/.milatools/profiles/{profile}.bash"
        else:
            prof = setup_profile(remote, path)

        qn.print(f"Using profile: {prof}")
        cat_result = remote.run(f"cat {prof}", hide=True, warn=True)
        if cat_result.ok:
            qn.print("=" * 50)
            qn.print(cat_result.stdout.rstrip())
            qn.print("=" * 50)
        else:
            exit(f"Could not find or load profile: {prof}")

        premote = remote.with_profile(prof)

        if not ensure_program(
            remote=premote,
            program=program,
            installers=installers,
        ):
            exit(f"Exit: {program} is not installed.")

        cnode = _find_allocation(
            remote,
            job_name=f"mila-serve-{program}",
            node=node,
            job=job,
            alloc=alloc,
        )

        patterns = {
            "node_name": "#### ([A-Za-z0-9_-]+)",
        }

        if port_pattern:
            patterns["port"] = port_pattern
        elif share:
            exit(
                "Server cannot be shared because it is serving over a Unix domain "
                "socket"
            )
        else:
            remote.run("mkdir -p ~/.milatools/sockets", hide=True)

        if share:
            host = "0.0.0.0"
        else:
            host = "localhost"

        sock_name = name or randname()
        command = command.format(
            path=path,
            sock=f"~/.milatools/sockets/{sock_name}.sock",
            host=host,
        )

        if token_pattern:
            patterns["token"] = token_pattern

        if persist:
            cnode = cnode.persist()

        proc, results = (
            cnode.with_profile(prof)
            .with_precommand("echo '####' $(hostname)")
            .extract(
                command,
                patterns=patterns,
            )
        )
        node_name = results["node_name"]

        if port_pattern:
            to_forward = int(results["port"])
        else:
            to_forward = f"{remote.home()}/.milatools/sockets/{sock_name}.sock"

        if cf is not None:
            remote.simple_run(f"echo program = {program} >> {cf}")
            remote.simple_run(f"echo node_name = {results['node_name']} >> {cf}")
            remote.simple_run(f"echo host = {host} >> {cf}")
            remote.simple_run(f"echo to_forward = {to_forward} >> {cf}")
            if token_pattern:
                remote.simple_run(f"echo token = {results['token']} >> {cf}")

    assert results is not None
    assert node_name is not None
    assert to_forward is not None
    assert proc is not None
    if token_pattern:
        options = {"token": results["token"]}
    else:
        options = {}

    local_proc, local_port = _forward(
        local=Local(),
        node=qualified(node_name),
        to_forward=to_forward,
        options=options,
        port=port,
    )

    if cf is not None:
        remote.simple_run(f"echo local_port = {local_port} >> {cf}")

    try:
        local_proc.wait()
    except KeyboardInterrupt:
        qn.print("Terminated by user.")
        if cf is not None:
            name = Path(cf).name
            qn.print("To reconnect to this server, use the command:")
            qn.print(f"  mila serve connect {name}", style="bold yellow")
            qn.print("To kill this server, use the command:")
            qn.print(f"  mila serve kill {name}", style="bold red")
    finally:
        local_proc.kill()
        proc.kill()


def _get_disk_quota_usage(
    remote: Remote, print_command_output: bool = True
) -> tuple[tuple[float, float], tuple[int, int]]:
    """Checks the disk quota on the $HOME filesystem on the mila cluster.

    Returns whether the quota is exceeded, in terms of storage space or number of files.
    """

    # NOTE: This is what the output of the command looks like on the Mila cluster:
    #
    # $ lfs quota -u $USER /home/mila
    # Disk quotas for usr normandf (uid 1471600598):
    #     Filesystem  kbytes   quota   limit   grace   files   quota   limit   grace
    #     /home/mila 101440844       0 104857600       -  936140       0 1048576       -
    # uid 1471600598 is using default block quota setting
    # uid 1471600598 is using default file quota setting
    #
    home_disk_quota_output = remote.get_output(
        "lfs quota -u $USER /home/mila", hide=not print_command_output
    )
    lines = home_disk_quota_output.splitlines()
    (
        _filesystem,
        used_kbytes,
        _quota1,
        limit_kbytes,
        _grace1,
        files,
        _quota2,
        limit_files,
        _grace2,
    ) = (
        lines[2].strip().split()
    )

    used_gb = float(int(used_kbytes.strip()) / (1024) ** 2)
    max_gb = float(int(limit_kbytes.strip()) / (1024) ** 2)
    used_files = int(files.strip())
    max_files = int(limit_files.strip())
    return (used_gb, max_gb), (used_files, max_files)


def check_disk_quota(remote: Remote) -> None:
    cluster = (
        "mila"  # todo: if we run this on CC, then we should use `diskusage_report`
    )
    # todo: Check the disk-quota of other filesystems if needed.
    filesystem = "$HOME"
    logger.debug("Checking disk quota on $HOME...")
    (used_gb, max_gb), (used_files, max_files) = _get_disk_quota_usage(remote)
    logger.debug(
        f"Disk usage: {used_gb:.1f} / {max_gb} GiB and {used_files} / {max_files} files"
    )
    size_ratio = used_gb / max_gb
    files_ratio = used_files / max_files
    reason = (
        f"{used_gb:.1f} / {max_gb} GiB"
        if size_ratio > files_ratio
        else f"{used_files} / {max_files} files"
    )

    freeing_up_space_instructions = (
        "For example, temporary files (logs, checkpoints, etc.) can be moved to "
        "$SCRATCH, while files that need to be stored for longer periods can be moved "
        "to $ARCHIVE or to a shared project folder under /network/projects.\n"
        "Visit https://docs.mila.quebec/Information.html#storage to learn more about "
        "how to best make use of the different filesystems available on the cluster."
    )

    if used_gb >= max_gb or used_files >= max_files:
        raise MilatoolsUserError(
            T.red(
                f"ERROR: Your disk quota on the {filesystem} filesystem is exceeded! "
                f"({reason}).\n"
                f"To fix this, login to the cluster with `ssh {cluster}` and free up "
                f"some space, either by deleting files, or by moving them to a "
                f"suitable filesystem.\n" + freeing_up_space_instructions
            )
        )
    if max(size_ratio, files_ratio) > 0.9:
        warning_message = (
            f"WARNING: You are getting pretty close to your disk quota on the $HOME "
            f"filesystem: ({reason})\n"
            "Please consider freeing up some space in your $HOME folder, either by "
            "deleting files, or by moving them to a more suitable filesystem.\n"
            + freeing_up_space_instructions
        )
        # TODO: Perhaps we could use the logger or the warnings package instead of just
        # printing?
        # logger.warning(UserWarning(warning_message))
        # warnings.warn(UserWarning(T.yellow(warning_message)))
        print(UserWarning(T.yellow(warning_message)))


def _find_allocation(
    remote: Remote,
    node: str | None,
    job: str | None,
    alloc: Sequence[str],
    job_name: str = "mila-tools",
):
    if (node is not None) + (job is not None) + bool(alloc) > 1:
        exit("ERROR: --node, --job and --alloc are mutually exclusive")

    if node is not None:
        node_name = qualified(node)
        return Remote(node_name)

    elif job is not None:
        node_name = remote.get_output(f"squeue --jobs {job} -ho %N")
        return Remote(node_name)

    else:
        alloc = ["-J", job_name, *alloc]
        return SlurmRemote(
            connection=remote.connection,
            alloc=alloc,
        )


def _forward(
    local: Local,
    node: str,
    to_forward: int | str,
    port: int | None,
    page: str | None = None,
    options: dict[str, str | None] = {},
    through_login: bool = False,
):
    if port is None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # Find a free local port by binding to port 0
        sock.bind(("localhost", 0))
        _, port = sock.getsockname()
        # Close it for ssh -L. It is *unlikely* it will not be available.
        sock.close()

    if isinstance(to_forward, int) or re.match("[0-9]+", to_forward):
        if through_login:
            to_forward = f"{node}:{to_forward}"
            args = [f"localhost:{port}:{to_forward}", "mila"]
        else:
            to_forward = f"localhost:{to_forward}"
            args = [f"localhost:{port}:{to_forward}", node]
    else:
        args = [f"localhost:{port}:{to_forward}", node]

    proc = local.popen(
        "ssh",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        "StrictHostKeyChecking=no",
        "-nNL",
        *args,
    )

    url = f"http://localhost:{port}"
    if page is not None:
        if not page.startswith("/"):
            page = f"/{page}"
        url += page

    options = {k: v for k, v in options.items() if v is not None}
    if options:
        url += f"?{urlencode(options)}"

    qn.print("Waiting for connection to be active...")
    nsecs = 10
    period = 0.2
    for _ in range(int(nsecs / period)):
        time.sleep(period)
        try:
            # This feels stupid, there's probably a better way
            local.silent_get("nc", "-z", "localhost", str(port))
        except subprocess.CalledProcessError:
            continue
        except Exception:
            break
        break

    qn.print(
        "Starting browser. You might need to refresh the page.",
        style="bold",
    )
    webbrowser.open(url)
    return proc, port


if __name__ == "__main__":
    main()
