from __future__ import annotations

import asyncio
import functools
import os
import shutil
import subprocess
import sys
import textwrap
from logging import getLogger as get_logger
from pathlib import Path
from typing import Sequence

from milatools.cli.utils import (
    CommandNotFoundError,
    batched,
    stripped_lines_of,
)
from milatools.utils.local_v2 import LocalV2
from milatools.utils.parallel_progress import (
    AsyncTaskFn,
    ReportProgressFn,
    run_async_tasks_with_progress_bar,
)
from milatools.utils.remote_v2 import RemoteV2

logger = get_logger(__name__)


def _running_inside_WSL() -> bool:
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
    if _running_inside_WSL():
        # Need to get the Windows Home directory, not the WSL one!
        windows_username = subprocess.getoutput("powershell.exe '$env:UserName'")
        return Path(
            f"/mnt/c/Users/{windows_username}/AppData/Roaming/Code/User/settings.json"
        )
    # Linux:
    return Path.home() / ".config/Code/User/settings.json"


def get_code_command() -> str:
    return os.environ.get("MILATOOLS_CODE_COMMAND", "code")


def _get_local_vscode_executable_path(code_command: str | None = None) -> str:
    if code_command is None:
        code_command = get_code_command()

    code_command_path = shutil.which(code_command)
    if not code_command_path:
        raise CommandNotFoundError(code_command)
    return code_command_path


def vscode_installed() -> bool:
    try:
        _ = _get_local_vscode_executable_path()
    except CommandNotFoundError:
        return False
    return True


async def sync_vscode_extensions(
    source: str | LocalV2 | RemoteV2,
    destinations: Sequence[str | LocalV2 | RemoteV2],
) -> dict[str, list[str]]:
    """Syncs vscode extensions between `source` all all the clusters in `dest`.

    This creates an async task for each cluster in `dest` and displays a progress bar.
    Returns the extensions that were installed on each cluster.
    """
    if isinstance(source, str):
        if source == "localhost":
            source = LocalV2()
        else:
            source = await RemoteV2.connect(source)

    destinations = _remove_source_from_destinations(source, destinations)

    if not destinations:
        logger.info("No destinations to sync extensions to!")
        return {}

    source_extensions = await _get_vscode_extensions(source)

    tasks: list[AsyncTaskFn[list[str]]] = []
    task_descriptions: list[str] = []

    dest_hostnames = [
        dest if isinstance(dest, str) else dest.hostname for dest in destinations
    ]
    for dest_runner, dest_hostname in zip(destinations, dest_hostnames):
        tasks.append(
            functools.partial(
                _install_vscode_extensions_task_function,
                source_extensions=source_extensions,
                remote=dest_runner,
                source_name=source.hostname,
            )
        )
        task_descriptions.append(f"{source.hostname} -> {dest_hostname}")

    results = await run_async_tasks_with_progress_bar(
        async_task_fns=tasks,
        task_descriptions=task_descriptions,
        overall_progress_task_description="[green]Syncing vscode extensions:",
    )
    return {hostname: result for hostname, result in zip(dest_hostnames, results)}


def _remove_source_from_destinations(
    source: LocalV2 | RemoteV2, destinations: Sequence[str | LocalV2 | RemoteV2]
):
    dest_hostnames = [
        dest if isinstance(dest, str) else dest.hostname for dest in destinations
    ]
    if source.hostname in dest_hostnames:
        logger.debug(f"{source.hostname!r} is also in the destinations, removing it.")
        destinations = list(destinations)
        destinations.pop(dest_hostnames.index(source.hostname))

    if len(set(dest_hostnames)) != len(dest_hostnames):
        raise ValueError(f"{dest_hostnames=} contains duplicate hostnames!")
    return destinations


async def _get_vscode_extensions(
    source: LocalV2 | RemoteV2,
) -> dict[str, str]:
    if isinstance(source, LocalV2):
        code_server_executable = _get_local_vscode_executable_path(code_command=None)
    else:
        code_server_executable = await _find_code_server_executable(
            source, remote_vscode_server_dir="~/.vscode-server"
        )
    if not code_server_executable:
        raise RuntimeError(
            f"The vscode-server executable was not found on {source.hostname}."
        )
    return await _get_vscode_extensions_dict(source, code_server_executable)


async def _install_vscode_extensions_task_function(
    report_progress: ReportProgressFn,
    source_extensions: dict[str, str],
    remote: str | RemoteV2 | LocalV2,
    source_name: str,
    verbose: bool = False,
) -> list[str]:
    """Installs vscode extensions on the remote cluster.

    1. Finds the `code-server` executable on the remote;
    2. Get the list of installed extensions on the remote;
    3. Compare the list of installed extensions on the remote with the list of
       extensions on the source;
    4. Install the extensions that are missing or out of date on the remote, updating
       the progress dict as it goes.


    Returns the list of installed extensions, in the form 'extension_name@version'.
    """
    installed: list[str] = []

    def _update_progress(
        progress: int, status: str, total: int = len(source_extensions)
    ):
        info = textwrap.shorten(status, 50, placeholder="...")
        report_progress(progress=progress, total=total, info=info)

    dest_hostname = remote if isinstance(remote, str) else remote.hostname

    if isinstance(remote, str):
        if remote == "localhost":
            remote = LocalV2()
        else:
            _update_progress(0, "Connecting...")
            remote = await RemoteV2.connect(remote)

    if isinstance(remote, LocalV2):
        code_server_executable = _get_local_vscode_executable_path()
        _update_progress(0, status="fetching installed extensions...")
        extensions_on_dest = await _get_vscode_extensions_dict(
            remote, code_server_executable
        )
    else:
        remote_vscode_server_dir = "~/.vscode-server"
        _update_progress(0, f"Looking for code-server in {remote_vscode_server_dir}")
        code_server_executable = await _find_code_server_executable(
            remote,
            remote_vscode_server_dir=remote_vscode_server_dir,
        )
        if not code_server_executable:
            logger.debug(
                f"The vscode-server executable was not found on {remote.hostname}."
                f"Skipping syncing extensions to {remote.hostname}."
            )
            _update_progress(
                # IDEA: Use a progress of `-1` to signify an error, and use a "X"
                # instead of a checkmark?
                progress=0,
                total=0,
                status="code-server executable not found!",
            )
            return installed

        _update_progress(0, status="fetching installed extensions...")
        extensions_on_dest = await _get_vscode_extensions_dict(
            remote, code_server_executable
        )

    logger.debug(f"{len(source_extensions)=}, {len(extensions_on_dest)=}")
    to_install = _extensions_to_install(
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
            extension = f"{extension_name}@{extension_version}"
            result = await _install_vscode_extension(
                remote,
                code_server_executable=code_server_executable,
                extension=extension,
                verbose=verbose,
            )
            if result.returncode != 0:
                logger.debug(
                    f"Unable to install extension {extension} on {dest_hostname}: {result.stderr}"
                )
            else:
                installed.append(extension)
        except (KeyboardInterrupt, asyncio.CancelledError):
            _update_progress(
                progress=index,
                total=len(to_install),
                status="Interrupted.",
            )
            return installed

    _update_progress(
        progress=len(to_install),
        total=len(to_install),
        status="Done.",
    )
    return installed


async def _install_vscode_extension(
    remote: LocalV2 | RemoteV2,
    code_server_executable: str,
    extension: str,
    verbose: bool = False,
):
    command = f"{code_server_executable} --install-extension {extension}"
    result = await remote.run_async(
        command,
        display=verbose,
        warn=True,
        hide=not verbose,
    )
    if result.stdout:
        logger.debug(result.stdout)
    return result


async def _get_vscode_extensions_dict(
    remote: RemoteV2 | LocalV2,
    code_server_executable: str,
) -> dict[str, str]:
    """Returns the list of isntalled extensions and the path to the code-server
    executable."""
    return _parse_vscode_extensions_versions(
        stripped_lines_of(
            await remote.get_output_async(
                f"{code_server_executable} --list-extensions --show-versions",
                display=False,
                hide=True,
            )
        )
    )


def _extensions_to_install(
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


async def _find_code_server_executable(
    remote: RemoteV2, remote_vscode_server_dir: str = "~/.vscode-server"
) -> str | None:
    """Find the most recent `code-server` executable on the remote.

    Returns `None` if none are found.
    """
    cluster = remote.hostname
    # TODO: When doing this for the first time on a remote cluster, this file might not
    # be present until the vscode window has opened and installed the vscode server on
    # the remote! Perhaps we should wait a little bit until it finishes installing
    # somehow?
    find_code_server_executables_command = (
        f"find {remote_vscode_server_dir} -name code-server -executable -type f"
    )
    code_server_executables = stripped_lines_of(
        await remote.get_output_async(
            find_code_server_executables_command,
            display=False,
            warn=True,
            hide=True,
        )
    )
    if not code_server_executables:
        logger.warning(f"Unable to find any code-server executables on {cluster}.")
        return None

    # Now that we have the list of vscode-server executables, we get the version of
    # each.

    # Run a single fused command over SSH instead of one command for each executable.
    # Each executable outputs 3 lines:
    # ```
    # 1.83.1
    # f1b07bd25dfad64b0167beb15359ae573aecd2cc
    # x64
    # ```
    remote_version_command_output = stripped_lines_of(
        await remote.get_output_async(
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
    # TODO: Should we instead use the one that is closest to the version of the local
    # editor?
    most_recent_code_server_executable = max(
        code_server_executable_versions.keys(),
        key=code_server_executable_versions.__getitem__,
    )
    return most_recent_code_server_executable


def _parse_vscode_extensions_versions(
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
