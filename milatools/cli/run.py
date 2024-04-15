import asyncio
import shlex
import sys
from pathlib import Path

import rich
import rich.columns
import rich.live
import rich.table
import rich.text

from milatools.cli import console
from milatools.cli.login import login
from milatools.cli.utils import SSH_CONFIG_FILE
from milatools.utils.remote_v2 import RemoteV2


async def run_command(
    command: str | list[str], ssh_config_path: Path = SSH_CONFIG_FILE
):
    command = shlex.join(command) if isinstance(command, list) else command
    if command.startswith("'") and command.endswith("'"):
        # NOTE: Need to remove leading and trailing quotes so the ssh subprocess doesn't
        # give an error. For example, with `mila run 'echo $SCRATCH'`, we would
        # otherwise get the error: bash: echo: command not found
        command = command[1:-1]

    remotes = await login(ssh_config_path=ssh_config_path)

    async def _is_slurm_cluster(remote: RemoteV2) -> bool:
        sbatch_path = await remote.get_output_async(
            "which sbatch", warn=True, hide=True, display=False
        )
        return bool(sbatch_path)

    is_slurm_cluster = await asyncio.gather(
        *(_is_slurm_cluster(remote) for remote in remotes),
    )
    cluster_login_nodes = [
        remote
        for remote, is_slurm_cluster in zip(remotes, is_slurm_cluster)
        if is_slurm_cluster
    ]

    results = await asyncio.gather(
        *(
            login_node.run_async(command=command, warn=True, display=True, hide=False)
            for login_node in cluster_login_nodes
        )
    )
    for remote, result in zip(cluster_login_nodes, results):
        for line in result.stdout.splitlines():
            print(f"({remote.hostname}) {line}")
        for line in result.stderr.splitlines():
            print(f"({remote.hostname}) {line}", file=sys.stderr)

    return results

    table = rich.table.Table(title=command)
    table.add_column("Cluster")

    # need an stdout column.
    need_stdout_column = any(result.stdout for result in results)
    need_stderr_column = any(result.stderr for result in results)

    if not need_stderr_column and not need_stdout_column:
        return results

    if need_stdout_column:
        table.add_column("stdout")
    if need_stderr_column:
        table.add_column("stderr")

    for remote, result in zip(cluster_login_nodes, results):
        row = [remote.hostname]
        if need_stdout_column:
            row.append(result.stdout)
        if need_stderr_column:
            row.append(result.stderr)
        table.add_row(*row, end_section=True)

    console.print(table)
    # table = rich.table.Table(title=command)
    # with rich.live.Live(table, refresh_per_second=1):

    # async with asyncio.TaskGroup() as group:
    #     for remote in remotes:
    #         table.add_column(remote.hostname, no_wrap=True)
    #         task = group.create_task(remote.run_async(command))
    #         task.add_done_callback(lambda _: table.add_row())
    return results


if __name__ == "main":
    asyncio.run(run_command("hostname"))
