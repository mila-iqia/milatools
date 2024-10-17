from __future__ import annotations

import asyncio
import shutil
from logging import getLogger as get_logger
from pathlib import PurePosixPath
from typing import Awaitable

from milatools.cli import console
from milatools.cli.init_command import DRAC_CLUSTERS
from milatools.cli.utils import (
    CommandNotFoundError,
    MilatoolsUserError,
    currently_in_a_test,
    internet_on_compute_nodes,
    running_inside_WSL,
)
from milatools.utils.compute_node import ComputeNode, salloc, sbatch
from milatools.utils.disk_quota import check_disk_quota
from milatools.utils.local_v2 import LocalV2
from milatools.utils.remote_v2 import RemoteV2
from milatools.utils.vscode_utils import sync_vscode_extensions

logger = get_logger(__name__)


async def code(
    path: str,
    command: str,
    persist: bool,
    job: int | None,
    node: str | None,
    alloc: list[str],
    cluster: str = "mila",
) -> ComputeNode | int:
    """Open a remote VSCode session on a compute node.

    Arguments:
        path: Path to open on the remote machine
        command: Command to use to start vscode (defaults to "code" or the value of \
            $MILATOOLS_CODE_COMMAND)
        persist: Whether the server should persist or not after exiting the terminal.
        job: ID of the job to connect to
        node: Name of the node to connect to
        alloc: Extra options to pass to slurm
    """
    # Check that the `code` command is in the $PATH so that we can use just `code` as
    # the command.
    if not shutil.which(command):
        raise CommandNotFoundError(command)

    if (job or node) and not persist:
        logger.warning("Assuming persist=True since a job or node was specified.")
        persist = True

    # Connect to the cluster's login node.
    login_node = await RemoteV2.connect(cluster)

    relative_path: PurePosixPath | None = None
    # Get $HOME because we have to give the full path to the folder to the code command.
    home = PurePosixPath(
        await login_node.get_output_async("echo $HOME", display=False, hide=True)
    )
    if not path.startswith("/"):
        relative_path = PurePosixPath(path)
        path = str(home if path == "." else home / path)
    elif (_path := PurePosixPath(path)).is_relative_to(home):
        relative_path = _path.relative_to(home)
        console.log(
            f"Hint: you can use a path relative to your $HOME instead of an absolute path.\n"
            f"For example, `mila code {path}` is the same as `mila code {relative_path}`.",
            highlight=True,
            markup=True,
        )

    try:
        await check_disk_quota(login_node)
    except MilatoolsUserError:
        # Raise errors that are meant to be shown to the user (disk quota is reached).
        raise
    except Exception as exc:
        logger.warning(
            f"Unable to check the disk-quota on the {cluster} cluster: {exc}"
        )

    # NOTE: Perhaps we could eventually do this check dynamically, if the cluster is an
    # unknown cluster?
    sync_vscode_extensions_task = None
    if not internet_on_compute_nodes(cluster):
        # Sync the VsCode extensions from the local machine over to the target cluster.
        console.log(
            f"Installing VSCode extensions that are on the local machine on "
            f"{cluster}.",
            style="cyan",
        )
        # todo: use the mila or the local machine as the reference for vscode
        # extensions?
        # TODO: If the remote is a cluster that doesn't yet have `vscode-server`, we
        # could launch vscode at the same time (or before) syncing the vscode extensions?
        sync_vscode_extensions_task = sync_vscode_extensions(
            LocalV2(),
            [login_node],
        )

    compute_node_task: Awaitable[ComputeNode]
    if job or node:
        if job and node:
            logger.warning(
                "Both job ID and node name were specified. Ignoring the node name and "
                "only using the job id."
            )
        job_id_or_node = job or node
        assert job_id_or_node is not None
        compute_node_task = ComputeNode.connect(
            login_node=login_node, job_id_or_node_name=job_id_or_node
        )
    else:
        if cluster in DRAC_CLUSTERS and not any("--account" in flag for flag in alloc):
            logger.warning(
                "Warning: When using the DRAC clusters, you usually need to "
                "specify the account to use when submitting a job. You can specify "
                "this in the job resources with `--alloc`, like so: "
                "`--alloc --account=<account_to_use>`, for example:\n"
                f"mila code some_path --cluster {cluster} --alloc "
                f"--account=your-account-here"
            )
        # Set the job name to `mila-code`. This should not be changed by the user
        # ideally, so we can collect some simple stats about the use of `milatools` on
        # the clusters.
        if any(flag.split("=")[0] in ("-J", "--job-name") for flag in alloc):
            raise MilatoolsUserError(
                "The job name flag (--job-name or -J) should be left unset for now "
                "because we use the job name to measure how many people use `mila "
                "code` on the various clusters. We also make use of the job name when "
                "the call to `salloc` is interrupted before we have a chance to know "
                "the job id."
            )
        job_name = "mila-code"
        alloc = alloc + [f"--job-name={job_name}"]

        if persist:
            compute_node_task = sbatch(
                login_node, sbatch_flags=alloc, job_name=job_name
            )
        else:
            # NOTE: Here we actually need the job name to be known, so that we can
            # scancel jobs if the call is interrupted.
            compute_node_task = salloc(
                login_node, salloc_flags=alloc, job_name=job_name
            )

    if sync_vscode_extensions_task is not None:
        # Sync the vscode extensions at the same time as waiting for the job.
        # Wait until all extensions are done syncing before launching vscode.
        # If any of the tasks failed, we want to raise the exception.
        # NOTE: Not using this at the moment because when interrupted, the job request
        # isn't cancelled properly.
        compute_node, _ = await asyncio.gather(
            compute_node_task,
            sync_vscode_extensions_task,
        )
    else:
        compute_node = await compute_node_task

    await launch_vscode_loop(command, compute_node, path)

    if not persist and not (job or node):
        # Cancel the job if it was not persistent.
        # (--job and --node are used to connect to persistent jobs)
        await compute_node.close_async()
        console.print(f"Ended session on '{compute_node.hostname}'")
        return compute_node.job_id

    console.print("This allocation is persistent and is still active.")
    console.print("To reconnect to this job, run the following:")
    console.print(
        f"  mila code {relative_path or path} "
        + (f"--cluster {cluster} " if cluster != "mila" else "")
        + f"--job {compute_node.job_id}",
        style="bold",
    )
    console.print("To kill this allocation:")
    console.print(f"  ssh {cluster} scancel {compute_node.job_id}", style="bold")
    return compute_node


async def launch_vscode_loop(code_command: str, compute_node: ComputeNode, path: str):
    while True:
        code_command_to_run = (
            code_command,
            "--new-window",
            "--wait",
            "--remote",
            f"ssh-remote+{compute_node.hostname}",
            path,
        )
        if running_inside_WSL():
            code_command_to_run = ("powershell.exe", *code_command_to_run)

        await LocalV2.run_async(code_command_to_run, display=True)
        # TODO: BUG: This now requires two Ctrl+C's instead of one!
        console.print(
            "The editor was closed. Reopen it with <Enter> or terminate the "
            "process with <Ctrl+C> (maybe twice)."
        )
        if currently_in_a_test():
            # NOTE: This early exit kills the job when it is not persistent.
            break
        try:
            input()
        except KeyboardInterrupt:
            break
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(f"Error while waiting for user input: {exc}")
            break
