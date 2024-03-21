from __future__ import annotations

import argparse
import shlex
import shutil
import sys
from logging import getLogger as get_logger

from typing_extensions import deprecated

from milatools.cli import console
from milatools.cli.common import (
    check_disk_quota,
    find_allocation,
)
from milatools.cli.init_command import DRAC_CLUSTERS
from milatools.cli.local import Local
from milatools.cli.remote import Remote
from milatools.cli.utils import (
    CLUSTERS,
    Cluster,
    CommandNotFoundError,
    MilatoolsUserError,
    SortingHelpFormatter,
    currently_in_a_test,
    get_fully_qualified_hostname_of_compute_node,
    make_process,
    no_internet_on_compute_nodes,
    running_inside_WSL,
)
from milatools.utils.remote_v2 import (
    InteractiveRemote,
    RemoteV2,
    get_node_of_job,
    run,
    salloc,
    sbatch,
)
from milatools.utils.vscode_utils import (
    get_code_command,
    sync_vscode_extensions,
    sync_vscode_extensions_with_hostnames,
)

logger = get_logger(__name__)


def add_mila_code_arguments(subparsers: argparse._SubParsersAction):
    code_parser: argparse.ArgumentParser = subparsers.add_parser(
        "code",
        help="Open a remote VSCode session on a compute node.",
        formatter_class=SortingHelpFormatter,
    )
    code_parser.add_argument(
        "PATH", help="Path to open on the remote machine", type=str
    )
    code_parser.add_argument(
        "--cluster",
        choices=CLUSTERS,  # todo: widen based on the entries in ssh config?
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
        type=int,
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
    if sys.platform == "win32":
        code_parser.set_defaults(function=code_v1)
    else:
        code_parser.set_defaults(function=code)


async def code(
    path: str,
    command: str,
    persist: bool,
    job: int | None,
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
    # Check that the `code` command is in the $PATH so that we can use just `code` as
    # the command.
    code_command = command
    if not shutil.which(code_command):
        raise CommandNotFoundError(code_command)

    # Connect to the cluster's login node (TODO: only if necessary).
    login_node = RemoteV2(cluster)

    if job is not None:
        node, _state = await get_node_of_job(login_node, job_id=job)

    if node:
        node = get_fully_qualified_hostname_of_compute_node(node)
        compute_node = RemoteV2(hostname=node)
    else:
        if cluster in DRAC_CLUSTERS and not any("--account" in flag for flag in alloc):
            logger.warning(
                "Warning: When using the DRAC clusters, you usually need to "
                "specify the account to use when submitting a job. You can specify "
                "this in the job resources with `--alloc`, like so: "
                "`--alloc --account=<account_to_use>`, for example:\n"
                f"mila code {path} --cluster {cluster} --alloc "
                f"--account=your-account-here"
            )
        if persist:
            compute_node = await sbatch(login_node, sbatch_flags=alloc)
        else:
            compute_node = salloc(login_node, salloc_flags=alloc)

    try:
        check_disk_quota(login_node)
    except MilatoolsUserError:
        # Raise errors that are meant to be shown to the user (disk quota is reached).
        raise
    except Exception as exc:
        logger.warning(f"Unable to check the disk-quota on the cluster: {exc}")

    # NOTE: Perhaps we could eventually do this check dynamically, if the cluster is an
    # unknown cluster?
    if no_internet_on_compute_nodes(cluster):
        # Sync the VsCode extensions from the local machine over to the target cluster.
        run_in_the_background = True if not currently_in_a_test() else False
        console.log(
            f"Installing VSCode extensions that are on the local machine on "
            f"{cluster}" + (" in the background." if run_in_the_background else "."),
            style="cyan",
        )

        if run_in_the_background:
            # todo: use the mila or the local machine as the reference for vscode
            # extensions?
            copy_vscode_extensions_process = make_process(
                sync_vscode_extensions,
                Local(),
                [login_node],
            )
            copy_vscode_extensions_process.start()
        else:
            sync_vscode_extensions(
                Local(),
                [cluster],
            )

    try:
        while True:
            code_command_to_run = (
                code_command,
                "-nw",
                "--remote",
                f"ssh-remote+{node}",
                path,
            )
            console.log(
                f"(local) {shlex.join(code_command_to_run)}", style="bold green"
            )
            await run(code_command_to_run)
            print(
                "The editor was closed. Reopen it with <Enter>"
                " or terminate the process with <Ctrl+C>"
            )
            if currently_in_a_test():
                break
            input()
    except KeyboardInterrupt:
        if isinstance(compute_node, InteractiveRemote):
            compute_node.close()
        return


@deprecated(
    "Support for the `mila code` command is now deprecated on Windows machines, as it "
    "does not support ssh keys with passphrases or clusters where 2FA is enabled. "
    "Please consider switching to the Windows Subsystem for Linux (WSL) to run "
    "`mila code`."
)
def code_v1(
    path: str,
    command: str,
    persist: bool,
    job: int | None,
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
    here = Local()
    remote = Remote(cluster)

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
        run_in_the_background = False  # if "pytest" not in sys.modules else True
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
                Local(),
                [cluster],
            )

    if node is None:
        cnode = find_allocation(
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
        node_name = get_fully_qualified_hostname_of_compute_node(node_name)

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
        console.print("This allocation is persistent and is still active.")
        console.print("To reconnect to this node:")
        console.print(f"  mila code {path} --node {node_name}", markup=True)
        console.print("To kill this allocation:")
        assert data is not None
        assert "jobid" in data
        console.print(f"  ssh mila scancel {data['jobid']}", style="bold")
