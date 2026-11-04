"""Tools to connect to and interact with the Mila cluster.

Cluster documentation: https://docs.mila.quebec/
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import logging
import operator
import sys
import traceback
from argparse import ArgumentParser, _HelpAction
from logging import getLogger as get_logger
from typing import Any, Callable
from urllib.parse import urlencode

import rich.logging

from milatools.cli import console
from milatools.cli.code import code
from milatools.cli.init_command import init
from milatools.cli.utils import (
    AllocationFlagsAction,
    MilatoolsUserError,
    SSHConnectionError,
    T,
    get_fully_qualified_name,
)
from milatools.utils.vscode_utils import get_code_command

from ..__version__ import __version__

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
    except KeyboardInterrupt:
        console.print("Exited by user.")
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
                f"An error occurred during the execution of the command `{command}`. "
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
    add_arguments(parser)

    verbose, function, args_dict = parse_args(parser)
    setup_logging(verbose)

    if inspect.iscoroutinefunction(function):
        try:
            return asyncio.run(function(**args_dict))
        except KeyboardInterrupt:
            console.log("Terminated by user.")
        return
    else:
        return function(**args_dict)


def add_arguments(parser: argparse.ArgumentParser):
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

    # ----- mila init ------
    init_parser = subparsers.add_parser(
        "init",
        help="Set up your configuration and credentials.",
        formatter_class=SortingHelpFormatter,
    )

    init_parser.set_defaults(function=init)

    # ----- mila code ------

    code_parser = subparsers.add_parser(
        "code",
        help="Open a remote VSCode session on a compute node.",
    )
    code_parser.add_argument(
        "PATH",
        help=(
            "Path to open on the remote machine. Defaults to $HOME.\n"
            "Can be a relative or absolute path. When a relative path (that doesn't "
            "start with a '/', like foo/bar) is passed, the path is relative to the "
            "$HOME directory on the selected cluster.\n"
            "For example, foo/project will be interpreted as $HOME/foo/project."
        ),
        type=str,
        default=".",
        nargs="?",
    )
    code_parser.add_argument(
        "--cluster",
        type=str,
        # choices=CLUSTERS,
        default="mila",
        help="Which cluster to connect to.",
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
        type=int,
        default=None,
        help="Job ID to connect to",
        metavar="JOB_ID",
    )
    code_parser.add_argument(
        "--node",
        type=str,
        default=None,
        help="Node to connect to",
        metavar="NODE",
    )

    _add_allocation_options(code_parser)

    code_parser.set_defaults(function=code)


def parse_args(parser: argparse.ArgumentParser) -> tuple[int, Callable, dict[str, Any]]:
    """Parses the command-line arguments.

    Returns the verbosity level, the function (or awaitable) to call, and the arguments
    to the function.
    """
    args = parser.parse_args()
    args_dict = vars(args)

    verbose: int = args_dict.pop("verbose")

    function = args_dict.pop("function")
    _ = args_dict.pop("<command>")
    # replace SEARCH -> "search", REMOTE -> "remote", etc.
    args_dict = _convert_uppercase_keys_to_lowercase(args_dict)

    assert callable(function)
    return verbose, function, args_dict


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


class SortingHelpFormatter(argparse.HelpFormatter):
    """Taken and adapted from https://stackoverflow.com/a/12269143/6388696."""

    def add_arguments(self, actions):
        actions = sorted(actions, key=operator.attrgetter("option_strings"))
        # put help actions first.
        actions = sorted(
            actions, key=lambda action: not isinstance(action, _HelpAction)
        )
        super().add_arguments(actions)


def _add_allocation_options(parser: ArgumentParser):
    # note: Ideally we'd like [--persist --alloc] | [--salloc] | [--sbatch] (i.e. a
    # subgroup with alloc and persist within a mutually exclusive group with salloc and
    # sbatch) but that doesn't seem possible with argparse as far as I can tell.
    arg_group = parser.add_argument_group(
        "Allocation options", description="Extra options to pass to slurm."
    )
    alloc_group = arg_group.add_mutually_exclusive_group()
    common_kwargs = {
        "dest": "alloc",
        "nargs": argparse.REMAINDER,
        "action": AllocationFlagsAction,
        "metavar": "VALUE",
        "default": [],
    }
    alloc_group.add_argument(
        "--persist",
        action="store_true",
        help="Whether the server should persist or not when using --alloc",
    )
    # --persist can be used with --alloc
    arg_group.add_argument(
        "--alloc",
        **common_kwargs,
        help="Extra options to pass to salloc or to sbatch if --persist is set.",
    )
    # --persist cannot be used with --salloc or --sbatch.
    # Note: REMAINDER args like --alloc, --sbatch and --salloc are already mutually
    # exclusive in a sense, since it's only possible to use one correctly, the other
    # args are stored in the first one (e.g. mila code --alloc --salloc bob will have
    # alloc of ["--salloc", "bob"]).
    alloc_group.add_argument(
        "--salloc",
        **common_kwargs,
        help="Extra options to pass to salloc. Same as using --alloc without --persist.",
    )
    alloc_group.add_argument(
        "--sbatch",
        **common_kwargs,
        help="Extra options to pass to sbatch. Same as using --alloc with --persist.",
    )


if __name__ == "__main__":
    main()
