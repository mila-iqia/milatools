from __future__ import annotations

import re
import socket
import subprocess
import time
import webbrowser
from contextlib import ExitStack
from logging import getLogger as get_logger
from pathlib import Path
from urllib.parse import urlencode

import invoke
import questionary as qn
from rich.text import Text

from milatools.cli import console
from milatools.cli.local import Local
from milatools.cli.profile import ensure_program, setup_profile
from milatools.cli.remote import Remote, SlurmRemote
from milatools.cli.utils import (
    Cluster,
    MilatoolsUserError,
    T,
    get_fully_qualified_hostname_of_compute_node,
    randname,
    with_control_file,
)
from milatools.utils.remote_v2 import RemoteV2

logger = get_logger(__name__)


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


def check_disk_quota(remote: Remote | RemoteV2) -> None:
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


def forward(
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


def standard_server_v1(
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
    job: int | None,
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

        from milatools.cli.code_command import find_allocation_v1

        cnode = find_allocation_v1(
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

    local_proc, local_port = forward(
        local=Local(),
        node=get_fully_qualified_hostname_of_compute_node(node_name, cluster="mila"),
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


def standard_server_v2(
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
    job: int | None,
    alloc: list[str],
    port_pattern=None,
    token_pattern=None,
    cluster: Cluster = "mila",
):
    # Make the server visible from the login node (other users will be able to connect)
    # Temporarily disabled
    share = False

    if name is not None:
        persist = True
    elif persist:
        name = program

    remote = RemoteV2(cluster)

    path = path or "~"
    if path == "~" or path.startswith("~/"):
        path = remote.get_output("echo $HOME", display=False, hide=True) + path[1:]

    results: dict | None = None
    node_name: str | None = None
    to_forward: int | str | None = None
    cf: str | None = None
    proc = None
    raise NotImplementedError("TODO: adapt the rest of this to work with RemoteV2")

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
        if (
            isinstance(cat_result, invoke.runners.Result)
            and cat_result.return_code == 0
        ) or (
            isinstance(cat_result, subprocess.CompletedProcess)
            and cat_result.returncode == 0
        ):
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
        from milatools.cli.code_command import find_allocation_v1

        cnode = find_allocation_v1(
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

    local_proc, local_port = forward(
        local=Local(),
        node=get_fully_qualified_hostname_of_compute_node(node_name, cluster="mila"),
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
