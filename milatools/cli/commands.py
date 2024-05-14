"""Tools to connect to and interact with the Mila cluster.

Cluster documentation: https://docs.mila.quebec/
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import logging
import operator
import re
import shutil
import socket
import subprocess
import sys
import time
import traceback
import typing
import webbrowser
from argparse import ArgumentDefaultsHelpFormatter, ArgumentParser, _HelpAction
from collections.abc import Sequence
from contextlib import ExitStack
from logging import getLogger as get_logger
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import questionary as qn
import rich.logging
from typing_extensions import TypedDict

from milatools.cli import console
from milatools.utils.local_v1 import LocalV1
from milatools.utils.remote_v1 import RemoteV1, SlurmRemote
from milatools.utils.remote_v2 import RemoteV2
from milatools.utils.vscode_utils import (
    get_code_command,
    # install_local_vscode_extensions_on_remote,
    sync_vscode_extensions,
    sync_vscode_extensions_with_hostnames,
)

from ..__version__ import __version__
from .init_command import (
    print_welcome_message,
    setup_keys_on_login_node,
    setup_passwordless_ssh_access,
    setup_ssh_config,
    setup_vscode_settings,
    setup_windows_ssh_config_from_wsl,
)
from .profile import ensure_program, setup_profile
from .utils import (
    CLUSTERS,
    Cluster,
    CommandNotFoundError,
    MilatoolsUserError,
    SSHConnectionError,
    T,
    cluster_to_connect_kwargs,
    currently_in_a_test,
    get_fully_qualified_name,
    get_hostname_to_use_for_compute_node,
    make_process,
    no_internet_on_compute_nodes,
    randname,
    running_inside_WSL,
    with_control_file,
)

if typing.TYPE_CHECKING:
    from typing_extensions import Unpack


logger = get_logger(__name__)


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
            "labels": ",".join([command, __version__] if command else [__version__]),
            "template": "bug_report.md",
            "title": f"[v{__version__}] Issue running the command "
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
        action="version",
        version=f"milatools v{__version__}",
        help="Milatools version",
    )
    parser.add_argument(
        "-v", "--verbose", action="count", default=0, help="Enable verbose logging."
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
        "--cluster",
        choices=CLUSTERS,
        default="mila",
        help="Which cluster to connect to.",
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
        default=get_code_command(),
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

    # ----- mila sync vscode-extensions ------

    sync_parser = subparsers.add_parser(
        "sync",
        help="Various commands used to synchronize things between the the local machine and remote clusters.",
        formatter_class=SortingHelpFormatter,
    )
    sync_subparsers = sync_parser.add_subparsers(
        dest="<sync_subcommand>", required=True
    )
    sync_vscode_parser = sync_subparsers.add_parser(
        "vscode-extensions",
        help="Sync vscode extensions between a source and one or more target machines.",
        formatter_class=ArgumentDefaultsHelpFormatter,
    )
    sync_vscode_parser.add_argument(
        "--source",
        type=str,
        default="localhost",
        help=(
            "Source machine whose vscode extensions should be installed on all "
            "machines in `destinations`. This can either be a local machine or a "
            "remote cluster. Defaults to 'localhost', assuming that your local editor "
            "has the extensions you want to have on other machines."
        ),
    )
    sync_vscode_parser.add_argument(
        "--destinations",
        type=str,
        default=CLUSTERS,
        nargs="+",
        help=(
            "hostnames of target machines on which vscode extensions from `source` "
            "should be installed. These can also include 'localhost' to install remote "
            "extensions locally. Defaults to all the available SLURM clusters."
        ),
    )
    sync_vscode_parser.set_defaults(function=sync_vscode_extensions_with_hostnames)

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
    verbose: int = args_dict.pop("verbose")
    function = args_dict.pop("function")
    _ = args_dict.pop("<command>")
    _ = args_dict.pop("<serve_subcommand>", None)
    _ = args_dict.pop("<sync_subcommand>", None)
    setup_logging(verbose)
    # replace SEARCH -> "search", REMOTE -> "remote", etc.
    args_dict = _convert_uppercase_keys_to_lowercase(args_dict)

    if inspect.iscoroutinefunction(function):
        try:
            return asyncio.run(function(**args_dict))
        except KeyboardInterrupt:
            console.log("Terminated by user.")
        return

    assert callable(function)
    return function(**args_dict)


def setup_logging(verbose: int) -> None:
    global_loglevel = (
        logging.CRITICAL
        if verbose == 0
        else logging.WARNING
        if verbose == 1
        else logging.INFO
        if verbose == 2
        else logging.DEBUG
    )
    package_loglevel = (
        logging.WARNING
        if verbose == 0
        else logging.INFO
        if verbose == 1
        else logging.DEBUG
    )
    logging.basicConfig(
        level=global_loglevel,
        format="%(message)s",
        handlers=[
            rich.logging.RichHandler(markup=True, rich_tracebacks=True, console=console)
        ],
    )
    get_logger("milatools").setLevel(package_loglevel)


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

    # if we're running on WSL, we actually just copy the id_rsa + id_rsa.pub and the
    # ~/.ssh/config to the Windows ssh directory (taking care to remove the
    # ControlMaster-related entries) so that the user doesn't need to install Python on
    # the Windows side.
    if running_inside_WSL():
        setup_windows_ssh_config_from_wsl(linux_ssh_config=ssh_config)

    success = setup_passwordless_ssh_access(ssh_config=ssh_config)
    if not success:
        exit()
    setup_keys_on_login_node()
    setup_vscode_settings()
    print_welcome_message()


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
        local=LocalV1(),
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
    alloc: list[str],
    cluster: Cluster = "mila",
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
    here = LocalV1()
    remote = RemoteV1(cluster)

    if cluster != "mila" and job is None and node is None:
        if not any("--account" in flag for flag in alloc):
            logger.warning(
                "Warning: When using the DRAC clusters, you usually need to "
                "specify the account to use when submitting a job. You can specify "
                "this in the job resources with `--alloc`, like so: "
                "`--alloc --account=<account_to_use>`, for example:\n"
                f"mila code {path} --cluster {cluster} --alloc "
                f"--account=your-account-here"
            )

    if command is None:
        command = get_code_command()

    try:
        check_disk_quota(remote)
    except MilatoolsUserError:
        raise
    except Exception as exc:
        logger.warning(f"Unable to check the disk-quota on the cluster: {exc}")

    if sys.platform == "win32":
        print(
            "Syncing vscode extensions in the background isn't supported on "
            "Windows. Skipping."
        )
    elif no_internet_on_compute_nodes(cluster):
        # Sync the VsCode extensions from the local machine over to the target cluster.
        # TODO: Make this happen in the background (without overwriting the output).
        run_in_the_background = False
        print(
            console.log(
                f"[cyan]Installing VSCode extensions that are on the local machine on "
                f"{cluster}" + (" in the background." if run_in_the_background else ".")
            )
        )
        if run_in_the_background:
            copy_vscode_extensions_process = make_process(
                sync_vscode_extensions_with_hostnames,
                # todo: use the mila cluster as the source for vscode extensions? Or
                # `localhost`?
                source="localhost",
                destinations=[cluster],
            )
            copy_vscode_extensions_process.start()
        else:
            sync_vscode_extensions(
                LocalV1(),
                [cluster],
            )

    if node is None:
        cnode = _find_allocation(
            remote,
            job_name="mila-code",
            job=job,
            node=node,
            alloc=alloc,
            cluster=cluster,
        )
        if persist:
            cnode = cnode.persist()

        data, proc = cnode.ensure_allocation()

        node_name = data["node_name"]
    else:
        node_name = node
        proc = None
        data = None

    if not path.startswith("/"):
        # Get $HOME because we have to give the full path to code
        home = remote.home()
        path = home if path == "." else f"{home}/{path}"

    command_path = shutil.which(command)
    if not command_path:
        raise CommandNotFoundError(command)

    # NOTE: Since we have the config entries for the DRAC compute nodes, there is no
    # need to use the fully qualified hostname here.
    if cluster == "mila":
        node_name = get_hostname_to_use_for_compute_node(node_name)

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
                    f"ssh-remote+{node_name}",
                    path,
                )
            else:
                here.run(
                    command_path,
                    "-nw",
                    "--remote",
                    f"ssh-remote+{node_name}",
                    path,
                )
            print(
                "The editor was closed. Reopen it with <Enter>"
                " or terminate the process with <Ctrl+C>"
            )
            if currently_in_a_test():
                break
            input()

    except KeyboardInterrupt:
        if not persist:
            if proc is not None:
                proc.kill()
            print(f"Ended session on '{node_name}'")

    if persist:
        print("This allocation is persistent and is still active.")
        print("To reconnect to this node:")
        print(
            T.bold(
                f"  mila code {path} "
                + (f"--cluster={cluster} " if cluster != "mila" else "")
                + f"--node {node_name}"
            )
        )
        print("To kill this allocation:")
        assert data is not None
        assert "jobid" in data
        print(T.bold(f"  ssh {cluster} scancel {data['jobid']}"))


def connect(identifier: str, port: int | None):
    """Reconnect to a persistent server."""

    remote = RemoteV1("mila")
    info = _get_server_info(remote, identifier)
    local_proc, _ = _forward(
        local=LocalV1(),
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
    remote = RemoteV1("mila")

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
    remote = RemoteV1("mila")

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
    alloc: list[str]
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
    remote: RemoteV1, identifier: str, hide: bool = False
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
    alloc: list[str],
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

    remote = RemoteV1("mila")

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
            cluster="mila",
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
        local=LocalV1(),
        node=get_hostname_to_use_for_compute_node(node_name, cluster="mila"),
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


def _parse_lfs_quota_output(
    lfs_quota_output: str,
) -> tuple[tuple[float, float], tuple[int, int]]:
    """Parses space and # of files (usage, limit) from the  output of `lfs quota`."""
    lines = lfs_quota_output.splitlines()

    header_line: str | None = None
    header_line_index: int | None = None
    for index, line in enumerate(lines):
        if (
            len(line_parts := line.strip().split()) == 9
            and line_parts[0].lower() == "filesystem"
        ):
            header_line = line
            header_line_index = index
            break
    assert header_line
    assert header_line_index is not None

    values_line_parts: list[str] = []
    # The next line may overflow to two (or maybe even more?) lines if the name of the
    # $HOME dir is too long.
    for content_line in lines[header_line_index + 1 :]:
        additional_values = content_line.strip().split()
        assert len(values_line_parts) < 9
        values_line_parts.extend(additional_values)
        if len(values_line_parts) == 9:
            break

    assert len(values_line_parts) == 9, values_line_parts
    (
        _filesystem,
        used_kbytes,
        _quota_kbytes,
        limit_kbytes,
        _grace_kbytes,
        files,
        _quota_files,
        limit_files,
        _grace_files,
    ) = values_line_parts

    used_gb = int(used_kbytes.strip()) / (1024**2)
    max_gb = int(limit_kbytes.strip()) / (1024**2)
    used_files = int(files.strip())
    max_files = int(limit_files.strip())
    return (used_gb, max_gb), (used_files, max_files)


def check_disk_quota(remote: RemoteV1 | RemoteV2) -> None:
    cluster = remote.hostname

    # NOTE: This is what the output of the command looks like on the Mila cluster:
    #
    # Disk quotas for usr normandf (uid 1471600598):
    #      Filesystem  kbytes   quota   limit   grace   files   quota   limit   grace
    # /home/mila/n/normandf
    #                 95747836       0 104857600       -  908722       0 1048576       -
    # uid 1471600598 is using default block quota setting
    # uid 1471600598 is using default file quota setting

    # Need to assert this, otherwise .get_output calls .run which would spawn a job!
    assert not isinstance(remote, SlurmRemote)
    if not remote.get_output("which lfs", hide=True):
        logger.debug("Cluster doesn't have the lfs command. Skipping check.")
        return

    console.log("Checking disk quota on $HOME...")

    home_disk_quota_output = remote.get_output("lfs quota -u $USER $HOME", hide=True)
    if "not on a mounted Lustre filesystem" in home_disk_quota_output:
        logger.debug("Cluster doesn't use lustre on $HOME filesystem. Skipping check.")
        return

    (used_gb, max_gb), (used_files, max_files) = _parse_lfs_quota_output(
        home_disk_quota_output
    )

    def get_colour(used: float, max: float) -> str:
        return "red" if used >= max else "orange" if used / max > 0.7 else "green"

    disk_usage_style = get_colour(used_gb, max_gb)
    num_files_style = get_colour(used_files, max_files)
    from rich.text import Text

    console.log(
        "Disk usage:",
        Text(f"{used_gb:.2f} / {max_gb:.2f} GiB", style=disk_usage_style),
        "and",
        Text(f"{used_files} / {max_files} files", style=num_files_style),
        markup=False,
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
                f"ERROR: Your disk quota on the $HOME filesystem is exceeded! "
                f"({reason}).\n"
                f"To fix this, login to the cluster with `ssh {cluster}` and free up "
                f"some space, either by deleting files, or by moving them to a "
                f"suitable filesystem.\n" + freeing_up_space_instructions
            )
        )
    if max(size_ratio, files_ratio) > 0.9:
        warning_message = (
            f"You are getting pretty close to your disk quota on the $HOME "
            f"filesystem: ({reason})\n"
            "Please consider freeing up some space in your $HOME folder, either by "
            "deleting files, or by moving them to a more suitable filesystem.\n"
            + freeing_up_space_instructions
        )
        logger.warning(UserWarning(warning_message))


def _find_allocation(
    remote: RemoteV1,
    node: str | None,
    job: str | None,
    alloc: list[str],
    cluster: Cluster = "mila",
    job_name: str = "mila-tools",
):
    if (node is not None) + (job is not None) + bool(alloc) > 1:
        exit("ERROR: --node, --job and --alloc are mutually exclusive")

    if node is not None:
        node_name = get_hostname_to_use_for_compute_node(node, cluster=cluster)
        return RemoteV1(
            node_name, connect_kwargs=cluster_to_connect_kwargs.get(cluster)
        )

    elif job is not None:
        node_name = remote.get_output(f"squeue --jobs {job} -ho %N")
        return RemoteV1(
            node_name, connect_kwargs=cluster_to_connect_kwargs.get(cluster)
        )

    else:
        alloc = ["-J", job_name, *alloc]
        return SlurmRemote(
            connection=remote.connection,
            alloc=alloc,
            hostname=remote.hostname,
        )


def _forward(
    local: LocalV1,
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
