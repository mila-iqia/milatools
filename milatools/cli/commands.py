"""Tools to connect to and interact with the Mila cluster.

Cluster documentation: https://docs.mila.quebec/
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import logging
import sys
import traceback
import typing
import webbrowser
from argparse import ArgumentDefaultsHelpFormatter, ArgumentParser
from collections.abc import Sequence
from logging import getLogger as get_logger
from typing import Any
from urllib.parse import urlencode

import questionary as qn
import rich.logging
from typing_extensions import TypedDict

from milatools.utils.vscode_utils import (
    sync_vscode_extensions_with_hostnames,
)

from ..__version__ import __version__
from .code_command import add_mila_code_arguments
from .common import forward, standard_server_v1
from .init_command import (
    print_welcome_message,
    setup_keys_on_login_node,
    setup_passwordless_ssh_access,
    setup_ssh_config,
    setup_vscode_settings,
    setup_windows_ssh_config_from_wsl,
)
from .local import Local
from .remote import Remote
from .utils import (
    CLUSTERS,
    MilatoolsUserError,
    SortingHelpFormatter,
    SSHConnectionError,
    T,
    get_fully_qualified_name,
    running_inside_WSL,
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
    forward_parser.set_defaults(function=forward_command)

    # ----- mila code ------
    add_mila_code_arguments(subparsers)

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
    serve_notebook_parser.set_defaults(function=serve_notebook)

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
    serve_tensorboard_parser.set_defaults(function=serve_tensorboard)

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
        return asyncio.run(function(**args_dict))
    assert callable(function)
    return function(**args_dict)


def setup_logging(verbose: int) -> None:
    global_loglevel = (
        logging.CRITICAL
        if verbose == 0
        else (
            logging.WARNING
            if verbose == 1
            else logging.INFO
            if verbose == 2
            else logging.DEBUG
        )
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
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[rich.logging.RichHandler(markup=True, rich_tracebacks=True)],
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


def forward_command(
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

    local_proc, _ = forward(
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


def connect(identifier: str, port: int | None):
    """Reconnect to a persistent server."""

    remote = Remote("mila")
    info = _get_server_info(remote, identifier)
    local_proc, _ = forward(
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
    alloc: list[str]
    """Extra options to pass to slurm."""

    job: int | None
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

    standard_server_v1(
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


def serve_notebook(path: str | None, **kwargs: Unpack[StandardServerArgs]):
    """Start a Jupyter Notebook server.

    Arguments:
        path: Path to open on the remote machine
    """
    if path and path.endswith(".ipynb"):
        exit("Only directories can be given to the mila serve notebook command")

    standard_server_v1(
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


def serve_tensorboard(logdir: str, **kwargs: Unpack[StandardServerArgs]):
    """Start a Tensorboard server.

    Arguments:
        logdir: Path to the experiment logs
    """

    standard_server_v1(
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

    standard_server_v1(
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
    standard_server_v1(
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
        type=int,
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


if __name__ == "__main__":
    main()
