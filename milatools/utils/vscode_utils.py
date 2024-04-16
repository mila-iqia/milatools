from __future__ import annotations

import functools
import os
import shlex
import shutil
import subprocess
import sys
import textwrap
from logging import getLogger as get_logger
from pathlib import Path
from typing import Literal, Sequence

from milatools.cli.local import Local
from milatools.cli.remote import Remote
from milatools.cli.utils import (
    CLUSTERS,
    batched,
    stripped_lines_of,
)
from milatools.utils.parallel_progress import (
    DictProxy,
    ProgressDict,
    TaskFn,
    TaskID,
    parallel_progress_bar,
)
from milatools.utils.remote_v2 import RemoteV2

logger = get_logger(__name__)


def running_inside_WSL() -> bool:
    return sys.platform == "linux" and bool(shutil.which("powershell.exe"))


def get_expected_vscode_settings_json_path() -> Path:
    if sys.platform == "win32":
        return Path.home() / "AppData\\Roaming\\Code\\User\\settings.json"
    if sys.platform == "darwin":  # MacOS
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "Code"
            / "User"
            / "settings.json"
        )
    if running_inside_WSL():
        # Need to get the Windows Home directory, not the WSL one!
        windows_username = subprocess.getoutput("powershell.exe '$env:UserName'")
        return Path(
            f"/mnt/c/Users/{windows_username}/AppData/Roaming/Code/User/settings.json"
        )
    # Linux:
    return Path.home() / ".config/Code/User/settings.json"


def get_code_command() -> str:
    return os.environ.get("MILATOOLS_CODE_COMMAND", "code")


def get_vscode_executable_path() -> str | None:
    return shutil.which(get_code_command())


def vscode_installed() -> bool:
    return bool(get_vscode_executable_path())


def sync_vscode_extensions_with_hostnames(
    source: str,
    destinations: list[str],
):
    destinations = list(destinations)
    if source in destinations:
        if source == "mila" and destinations == CLUSTERS:
            logger.info("Assuming you want to sync from mila to all DRAC/CC clusters.")
        else:
            logger.warning(
                f"{source=} is also in the destinations to sync to. " f"Removing it."
            )
        destinations.remove(source)

    if len(set(destinations)) != len(destinations):
        raise ValueError(f"{destinations=} contains duplicate hostnames!")

    source_obj = Local() if source == "localhost" else RemoteV2(source)
    return sync_vscode_extensions(source_obj, destinations)


def sync_vscode_extensions(
    source: str | Local | RemoteV2,
    dest_clusters: Sequence[str | Local | RemoteV2],
):
    """Syncs vscode extensions between `source` all all the clusters in `dest`.

    This spawns a thread for each cluster in `dest` and displays a parallel progress bar
    for the syncing of vscode extensions to each cluster.
    """
    if isinstance(source, Local):
        source_hostname = "localhost"
        source_extensions = get_local_vscode_extensions()
    elif isinstance(source, RemoteV2):
        source_hostname = source.hostname
        code_server_executable = find_code_server_executable(
            source, remote_vscode_server_dir="~/.vscode-server"
        )
        if not code_server_executable:
            raise RuntimeError(
                f"The vscode-server executable was not found on {source.hostname}."
            )
        source_extensions = get_remote_vscode_extensions(source, code_server_executable)
    else:
        assert isinstance(source, str)
        source_hostname = source
        source = RemoteV2(source)

    task_hostnames: list[str] = []
    task_fns: list[TaskFn[ProgressDict]] = []
    task_descriptions: list[str] = []

    for dest_remote in dest_clusters:
        dest_hostname: str

        if dest_remote == "localhost":
            dest_hostname = dest_remote  # type: ignore
            dest_remote = Local()  # pickleable
        elif isinstance(dest_remote, Local):
            dest_hostname = "localhost"
            dest_remote = dest_remote  # again, pickleable
        elif isinstance(dest_remote, RemoteV2):
            dest_hostname = dest_remote.hostname
            dest_remote = dest_remote  # pickleable
        elif isinstance(dest_remote, Remote):
            # We unfortunately can't pass this kind of object to another process or
            # thread because it uses `fabric.Connection` which don't appear to be
            # pickleable. This means we will have to re-connect in the subprocess.
            dest_hostname = dest_remote.hostname
            dest_remote = None
        else:
            assert isinstance(dest_remote, str)
            # The dest_remote is a hostname. Try to connect to it with a reusable SSH
            # control socket so we can get the 2FA prompts out of the way in advance.
            # NOTE: We could fallback to using the `Remote` class with paramiko inside
            # the subprocess if this doesn't work, but it would suck because it messes
            # up the UI, and you need to press 1 in the terminal to get the 2FA prompt,
            # which screws up the progress bars.
            dest_hostname = dest_remote
            dest_remote = RemoteV2(hostname=dest_hostname)

        task_hostnames.append(dest_hostname)
        task_fns.append(
            functools.partial(
                install_vscode_extensions_task_function,
                dest_hostname=dest_hostname,
                source_extensions=source_extensions,
                remote=dest_remote,
                source_name=source_hostname,
            )
        )
        task_descriptions.append(f"{source_hostname} -> {dest_hostname}")

    results: dict[str, ProgressDict] = {}

    for hostname, result in zip(
        task_hostnames,
        parallel_progress_bar(
            task_fns=task_fns,
            task_descriptions=task_descriptions,
            overall_progress_task_description="[green]Syncing vscode extensions:",
        ),
    ):
        results[hostname] = result
    return results


def install_vscode_extensions_task_function(
    task_progress_dict: DictProxy[TaskID, ProgressDict],
    task_id: TaskID,
    dest_hostname: str | Literal["localhost"],
    source_extensions: dict[str, str],
    remote: RemoteV2 | Local | None,
    source_name: str,
    verbose: bool = False,
) -> ProgressDict:
    """Installs vscode extensions on the remote cluster.

    1. Finds the `code-server` executable on the remote;
    2. Get the list of installed extensions on the remote;
    3. Compare the list of installed extensions on the remote with the list of
       extensions on the source;
    4. Install the extensions that are missing or out of date on the remote, updating
       the progress dict as it goes.
    """

    def _update_progress(
        progress: int, status: str, total: int = len(source_extensions)
    ):
        # Show progress to the parent process by setting an item in the task progress
        # dict.
        progress_dict: ProgressDict = {
            "progress": progress,
            "total": total,
            "info": textwrap.shorten(status, 50, placeholder="..."),
        }
        task_progress_dict[task_id] = progress_dict
        return progress_dict

    if remote is None:
        if dest_hostname == "localhost":
            remote = Local()
        else:
            _update_progress(0, "Connecting...")
            remote = RemoteV2(dest_hostname)

    if isinstance(remote, Local):
        assert dest_hostname == "localhost"
        code_server_executable = get_vscode_executable_path()
        assert code_server_executable
        extensions_on_dest = get_local_vscode_extensions()
    else:
        dest_hostname = remote.hostname
        remote_vscode_server_dir = "~/.vscode-server"
        _update_progress(0, f"Looking for code-server in {remote_vscode_server_dir}")
        code_server_executable = find_code_server_executable(
            remote,
            remote_vscode_server_dir=remote_vscode_server_dir,
        )
        if not code_server_executable:
            logger.debug(
                f"The vscode-server executable was not found on {remote.hostname}."
                f"Skipping syncing extensions to {remote.hostname}."
            )
            return _update_progress(
                # IDEA: Use a progress of `-1` to signify an error, and use a "X"
                # instead of a checkmark?
                progress=0,
                total=0,
                status="code-server executable not found!",
            )
        _update_progress(0, status="fetching installed extensions...")
        extensions_on_dest = get_remote_vscode_extensions(
            remote, code_server_executable
        )

    logger.debug(f"{len(source_extensions)=}, {len(extensions_on_dest)=}")
    to_install = extensions_to_install(
        source_extensions,
        extensions_on_dest,
        source_name=source_name,
        dest_name=dest_hostname,
    )

    if to_install:
        logger.debug(f"Will install {len(to_install)} extensions on {dest_hostname}.")
    else:
        logger.info(
            f"No extensions to sync to {dest_hostname} (all {len(to_install)} extensions are up to date.)"
        )

    for index, (extension_name, extension_version) in enumerate(to_install.items()):
        try:
            _update_progress(
                progress=index + 1,
                total=len(to_install),
                status=f"Installing {extension_name}",
            )
            result = install_vscode_extension(
                remote,
                code_server_executable,
                extension=f"{extension_name}@{extension_version}",
                verbose=verbose,
            )
        except KeyboardInterrupt:
            return _update_progress(
                progress=index,
                total=len(to_install),
                status="Interrupted.",
            )

        if result.returncode != 0:
            logger.debug(f"{dest_hostname}: {result.stderr}")

    return _update_progress(
        progress=len(to_install),
        total=len(to_install),
        status="Done.",
    )


def install_vscode_extension(
    remote: Local | RemoteV2,
    code_server_executable: str,
    extension: str,
    verbose: bool = False,
):
    command = (
        code_server_executable,
        "--install-extension",
        extension,
    )
    if isinstance(remote, RemoteV2):
        result = remote.run(
            shlex.join(command),
            display=verbose,
            warn=True,
            hide=not verbose,
        )
    else:
        result = remote.run(
            *command,
            capture_output=not verbose,
            display_command=verbose,
        )
    if result.stdout:
        logger.debug(result.stdout)
    return result


def get_local_vscode_extensions() -> dict[str, str]:
    output = subprocess.run(
        (
            get_vscode_executable_path() or get_code_command(),
            "--list-extensions",
            "--show-versions",
        ),
        shell=False,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return parse_vscode_extensions_versions(stripped_lines_of(output))


def get_remote_vscode_extensions(
    remote: Remote | RemoteV2,
    remote_code_server_executable: str,
) -> dict[str, str]:
    """Returns the list of isntalled extensions and the path to the code-server
    executable."""
    remote_extensions = parse_vscode_extensions_versions(
        stripped_lines_of(
            remote.get_output(
                f"{remote_code_server_executable} --list-extensions --show-versions",
                display=False,
                hide=True,
            )
        )
    )
    return remote_extensions


def extensions_to_install(
    source_extensions: dict[str, str],
    dest_extensions: dict[str, str],
    source_name: str,
    dest_name: str,
) -> dict[str, str]:
    extensions_to_install_on_dest: dict[str, str] = {}

    for source_extension_name, source_version in source_extensions.items():
        if source_extension_name not in dest_extensions:
            logger.debug(
                f"Installing extension {source_extension_name} on {dest_name}."
            )
            extensions_to_install_on_dest[source_extension_name] = source_version
        elif (local_version_tuple := _as_version_tuple(source_version)) > (
            dest_version_tuple := _as_version_tuple(
                dest_version := dest_extensions[source_extension_name]
            )
        ):
            logger.debug(
                f"Updating {source_extension_name} to version {source_version}."
            )
            extensions_to_install_on_dest[source_extension_name] = source_version
        elif local_version_tuple < dest_version_tuple:
            # The extension is at a newer version on the remote.
            logger.debug(
                f"extension {source_extension_name} on {source_name} is older than "
                f"on {dest_name}: {source_version} < {dest_version}"
            )
        else:
            # The extension is already installed at that version on that cluster.
            pass
    return extensions_to_install_on_dest


def find_code_server_executable(
    remote: Remote | RemoteV2, remote_vscode_server_dir: str = "~/.vscode-server"
) -> str | None:
    """Find the most recent `code-server` executable on the remote.

    Returns `None` if none are found.
    """
    cluster = remote.hostname
    # TODO: When doing this for the first time on a remote cluster, this file might not
    # be present until the vscode window has opened and installed the vscode server on the
    # remote! Perhaps we should wait a little bit until it finishes installing somehow?
    find_code_server_executables_command = (
        f"find {remote_vscode_server_dir} -name code-server -executable -type f"
    )
    code_server_executables = stripped_lines_of(
        remote.get_output(
            find_code_server_executables_command,
            display=False,
            warn=True,
            hide=True,
        )
    )
    if not code_server_executables:
        logger.warning(f"Unable to find any code-server executables on {cluster}.")
        return None

    # Run a single fused command over SSH instead of one command for each executable.
    # Each executable outputs 3 lines:
    # ```
    # 1.83.1
    # f1b07bd25dfad64b0167beb15359ae573aecd2cc
    # x64
    # ```
    remote_version_command_output = stripped_lines_of(
        remote.get_output(
            find_code_server_executables_command + " -exec {} --version \\;",
            display=False,
            hide=True,
        )
    )
    # The commands are run in sequence, so the outputs are not interleaved, so we can
    # group the output lines by 3 safely.
    code_server_executable_versions: dict[str, tuple[int | str, ...]] = {}
    for version, hash, _x64 in batched(remote_version_command_output, 3):
        version = _as_version_tuple(version)
        for code_server_executable in code_server_executables:
            if hash in code_server_executable:
                code_server_executable_versions[code_server_executable] = version
                break

    if not code_server_executable_versions:
        logger.warning(
            f"Unable to determine the versions of any of the code-server "
            f"executables found on {cluster}."
        )
        return None

    logger.debug(
        f"Found {len(code_server_executable_versions)} code-server executables on "
        f"{cluster}."
    )
    # Use the most recent vscode-server executable.
    most_recent_code_server_executable = max(
        code_server_executable_versions.keys(),
        key=code_server_executable_versions.__getitem__,
    )
    return most_recent_code_server_executable


def parse_vscode_extensions_versions(
    list_extensions_output_lines: list[str],
) -> dict[str, str]:
    extensions = [line for line in list_extensions_output_lines if "@" in line]

    def _extension_name_and_version(extension: str) -> tuple[str, str]:
        # extensions should include name@version since we use --show-versions.
        assert "@" in extension
        name, version = extension.split("@", maxsplit=1)
        return name, version

    # NOTE: Unsure if it's possible to have more than one version of an extension
    # installed, but getting the latest version (based on int/string comparison) just to
    # be sure.
    name_to_versions: dict[str, list[str]] = {}
    for extension_name, version in map(_extension_name_and_version, extensions):
        name_to_versions.setdefault(extension_name, []).append(version)

    return dict(
        sorted(
            [
                (name, max(versions, key=_as_version_tuple))
                for name, versions in name_to_versions.items()
            ]
        )
    )


def _as_version_tuple(version_str: str) -> tuple[int | str, ...]:
    return tuple([int(v) if v.isdigit() else v for v in version_str.split(".")])
