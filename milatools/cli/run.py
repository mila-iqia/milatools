from __future__ import annotations

import asyncio
import subprocess

from rich.console import Group
from rich.panel import Panel

from milatools.cli import console
from milatools.utils.remote_v2 import RemoteV2, control_socket_is_running_async

# Default list of clusters to check
DEFAULT_RUN_CLUSTERS = [
    "mila",
    "narval",
    "rorqual",
    "fir",
    "nibi",
    "tamia",
    "killarney",
    "vulcan",
    "trillium",
    "trillium-gpu",
]


async def run(
    command: str, clusters: list[RemoteV2] | None = None
) -> list[subprocess.CompletedProcess]:
    """Run a command on multiple clusters in parallel."""
    if clusters is None:
        clusters = []

    if not clusters:
        return []

    results = await asyncio.gather(
        *[
            cluster.run_async(command, display=False, warn=True, hide=True)
            for cluster in clusters
        ],
        return_exceptions=True,
    )

    # Handle exceptions (e.g. connection errors) by converting them to CompletedProcesswith error info
    return [
        result
        if not isinstance(result, Exception)
        # Create a fake CompletedProcess for the error
        else subprocess.CompletedProcess(
            args=command,
            returncode=1,
            stdout="",
            stderr=f"Error connecting to {cluster.hostname}: {result}",
        )
        for cluster, result in zip(clusters, results)
    ]


async def run_cli(command: list[str], clusters: str | None = None):
    """CLI wrapper for the run command."""
    if not command:
        console.print("No command specified.", style="red")
        return
    cmd_str = " ".join(command)

    target_clusters: list[RemoteV2] = []

    if clusters:
        # User specified clusters
        cluster_names = [c.strip() for c in clusters.split(",")]
        for name in cluster_names:
            # We don't check for active connection if user explicitly asks for a cluster
            # RemoteV2.connect will try to connect (and start the socket if needed/possible)
            # But wait, the requirement says: "otherwise use all clusters with an active connection"
            # It implies that if specified, we just use them.
            target_clusters.append(await RemoteV2.connect(name))
    else:
        # Use default list and filter for active connections
        # We need to check which ones are active WITHOUT trying to connect interactively
        # control_socket_is_running_async checks if the socket exists and is running

        # We need to construct RemoteV2 objects to get the control path, but we
        # shouldn't start them yet
        # Actually RemoteV2 constructor doesn't start if _start_control_socket=False

        potential_remotes = [
            RemoteV2(name, _start_control_socket=False) for name in DEFAULT_RUN_CLUSTERS
        ]

        # Check which ones are active
        active_checks = await asyncio.gather(
            *[
                control_socket_is_running_async(r.hostname, r.control_path)
                for r in potential_remotes
            ]
        )

        for remote, is_active in zip(potential_remotes, active_checks):
            if is_active:
                # It's active, so we can "connect" (which just sets _started=True since it's already running)
                await remote._start_async()
                target_clusters.append(remote)

        if not target_clusters:
            console.print("No active cluster connections found.", style="yellow")
            return

    console.print(
        f"Running '{cmd_str}' on {len(target_clusters)} clusters...", style="bold blue"
    )

    results = await run(cmd_str, target_clusters)

    panels = []
    for cluster, result in zip(target_clusters, results):
        style = "green" if result.returncode == 0 else "red"
        title = f"[bold]{cluster.hostname}{
            ' (returncode: ' + str(result.returncode) + ')'
            if result.returncode != 0
            else ''
        }[/bold]"

        content = ""
        if result.stdout:
            content += f"[bold]Stdout:[/bold]\n{result.stdout.strip()}\n"
        if result.stderr:
            content += f"[bold]Stderr:[/bold]\n{result.stderr.strip()}\n"

        if not content:
            content = "[italic]No output[/italic]"

        panels.append(Panel(content, title=title, border_style=style))

    console.print(Group(*panels))
