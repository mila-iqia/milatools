from __future__ import annotations

import functools
import os
import shlex
import shutil
import subprocess
import sys
from logging import getLogger as get_logger
from pathlib import Path
from typing import Callable, Literal

from milatools.cli.local import Local
from milatools.cli.remote import Remote
from milatools.cli.utils import (
    CLUSTERS,
    batched,
    stripped_lines_of,
)
from milatools.parallel_progress import (
    DictProxy,
    ProgressDict,
    TaskID,
    parallel_progress_bar,
)

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


def sync_vscode_extensions_in_parallel_with_hostnames(
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

    source_obj = Local() if source == "localhost" else Remote(source)

    return sync_vscode_extensions_in_parallel(source_obj, destinations)


def sync_vscode_extensions_in_parallel(
    source: Remote | Local,
    dest_clusters: list[str],
):
    """Syncs vscode extensions between `source` all all the clusters in `dest`."""

    if isinstance(source, Local):
        source_extensions = get_local_vscode_extensions()
        source_hostname = "localhost"
    else:
        source_extensions, _ = get_remote_vscode_extensions(source)
        source_hostname = source.hostname
        if source_extensions is None:
            raise RuntimeError(
                f"The vscode-server executable was not found on {source.hostname}."
            )

    task_fns: list[Callable[[DictProxy[TaskID, ProgressDict], TaskID], None]] = []
    task_descriptions: list[str] = []

    for dest_remote in dest_clusters:
        task_fns.append(
            functools.partial(
                _install_vscode_extensions_task_function,
                dest_hostname=dest_remote,
                source_extensions=source_extensions,
                source_name=source_hostname,
            )
        )
        task_descriptions.append(
            f"Syncing extensions from {source_hostname} -> {dest_remote}"
        )

    parallel_progress_bar(
        task_fns=task_fns,
        task_descriptions=task_descriptions,
        overall_progress_task_description="[green]Syncing vscode extensions:",
    )


def _install_vscode_extensions_task_function(
    progress_dict: DictProxy[TaskID, ProgressDict],
    task_id: TaskID,
    dest_hostname: str | Literal["localhost"],
    source_extensions: dict[str, str],
    source_name: str,
):
    if dest_hostname == "localhost":
        remote = Local()
    else:
        remote = Remote(dest_hostname)

    if isinstance(remote, Remote):
        dest_hostname = remote.hostname
        (
            extensions_on_dest,
            code_server_executable,
        ) = get_remote_vscode_extensions(remote)
        if extensions_on_dest is None:
            assert code_server_executable is None
            logger.warning(
                f"The vscode-server executable was not found on {remote.hostname}."
                f"Skipping syncing extensions to {remote.hostname}."
            )
            progress_dict[task_id] = {"progress": 1, "total": 1}
            return

        assert code_server_executable is not None
    else:
        dest_hostname = "localhost"
        extensions_on_dest = get_local_vscode_extensions()
        code_server_executable = get_vscode_executable_path()
        assert code_server_executable

    to_install = extensions_to_install(
        source_extensions,
        extensions_on_dest,
        source_name=source_name,
        dest_name=dest_hostname,
    )

    if not to_install:
        logger.info(f"No extensions to sync to {dest_hostname}.")
        progress_dict[task_id] = {"progress": len(to_install), "total": len(to_install)}
        return

    logger.debug(f"Will install {len(to_install)} extensions on {dest_hostname}.")

    for index, (extension_name, extension_version) in enumerate(to_install.items()):
        command = (
            code_server_executable,
            "--install-extension",
            f"{extension_name}@{extension_version}",
        )
        if isinstance(remote, Remote):
            result = remote.run(
                shlex.join(command),
                in_stream=False,
                display=False,
                echo=False,
                warn=True,
                asynchronous=False,
                hide=True,
            )
            success = result.return_code == 0
        else:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
            )
            success = result.returncode == 0

        if not success:
            logger.info(f"{dest_hostname}: " + result.stderr)

        progress_dict[task_id] = {
            "progress": index,
            "total": len(to_install),
        }


def get_local_vscode_extensions() -> dict[str, str]:
    return parse_vscode_extensions_versions(
        subprocess.run(
            (
                get_code_command(),
                "--list-extensions",
                "--show-versions",
            ),
            capture_output=True,
            text=True,
        ).stdout
    )


def get_remote_vscode_extensions(
    remote: Remote,
) -> tuple[dict[str, str], str] | tuple[None, None]:
    """Returns the list of isntalled extensions and the path to the code-server
    executable."""
    remote_code_server_executable = find_code_server_executable(
        remote,
        remote_vscode_server_dir="~/.vscode-server",
    )
    if not remote_code_server_executable:
        return None, None
    remote_extensions = parse_vscode_extensions_versions(
        remote.get_output(
            f"{remote_code_server_executable} --list-extensions --show-versions",
            display=False,
            hide=True,
        )
    )
    return remote_extensions, remote_code_server_executable


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
    remote: Remote, remote_vscode_server_dir: str
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
    list_extensions_output: str,
) -> dict[str, str]:
    extensions = stripped_lines_of(list_extensions_output)

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
