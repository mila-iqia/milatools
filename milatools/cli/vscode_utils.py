from __future__ import annotations

import os
import shutil
import subprocess
import sys
from logging import getLogger as get_logger
from pathlib import Path

from rich.progress import Progress, SpinnerColumn, TimeElapsedColumn

from milatools.cli.local import Local
from milatools.cli.remote import Remote
from milatools.cli.utils import (
    ClusterWithoutInternetOnCNodes,
    T,
    batched,
    console,
    stripped_lines_of,
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


def install_vscode_extensions_on_remote(
    cluster: ClusterWithoutInternetOnCNodes,
    remote: Remote | None = None,
    remote_vscode_server_dir: str = "~/.vscode-server",
):
    if remote is None:
        remote = Remote(cluster)

    code_server_executable = find_code_server_executable(
        remote, remote_vscode_server_dir
    )

    local_extensions = parse_vscode_extensions_versions(
        Local()
        .run(
            get_code_command(),
            "--list-extensions",
            "--show-versions",
            capture_output=True,
        )
        .stdout
    )
    remote_extensions = parse_vscode_extensions_versions(
        remote.run(f"{code_server_executable} --list-extensions --show-versions").stdout
    )

    # NOTE: Perhaps we could also make a dict of extensions to install or update locally
    # because they are installed at a newer version on the remote than on the local
    # machine? However at that point you might as well just use the vscode sync option.
    # extensions_to_install_locally: dict[str, str] = {}
    extensions_to_install_on_remote: dict[str, str] = {}

    for local_extension_name, local_version in local_extensions.items():
        if local_extension_name not in remote_extensions:
            logger.debug(f"Installing extension {local_extension_name} on {cluster}.")
            extensions_to_install_on_remote[local_extension_name] = local_version
        elif (local_version_tuple := _as_version_tuple(local_version)) > (
            remote_version_tuple := _as_version_tuple(
                remote_version := remote_extensions[local_extension_name]
            )
        ):
            logger.debug(f"Updating {local_extension_name} to version {local_version}.")
            extensions_to_install_on_remote[local_extension_name] = local_version
        elif local_version_tuple < remote_version_tuple:
            # The extension is at a newer version on the remote.
            logger.debug(
                f"Local extension {local_extension_name} is older than version on "
                f"{cluster}: {local_version} < {remote_version}"
            )
        else:
            # The extension is already installed at that version on that cluster.
            pass

    # NOTE: It seems like --install-extension only installs one extension (the first
    # given). Although we could do a single command with xargs to install all
    # extensions, it's a much nicer user experience to instead use a progress bar and
    # install them one by one. We're reusing the same ssh connection, so it isn't
    # _that_ bad.
    with Progress(
        SpinnerColumn(),
        *Progress.get_default_columns(),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    ) as progress:
        # IDEA: Use progress bars for each cluster, and sync the extensions in parallel!
        # https://www.deanmontgomery.com/2022/03/24/rich-progress-and-multiprocessing/
        # task1 = progress.add_task(
        #     f"[green]Syncing extensions on {cluster}",
        #     total=len(extensions_to_install_on_remote),
        # )
        # task2 = progress.add_task("[green]Processing", total=1000)
        # task3 = progress.add_task("[yellow]Thinking", total=None)
        for extension_name, extension_verison in progress.track(
            extensions_to_install_on_remote.items(),
            description=f"[green]Syncing vscode extensions on {cluster}",
        ):
            result = remote.run(
                f"{code_server_executable} --install-extension {extension_name}@{extension_verison}",
                in_stream=False,
                display=False,
                echo=False,
                warn=True,
                asynchronous=False,
                hide="stdout",
                echo_format=T.bold_cyan(f"({cluster})" + " $ {command}"),
            )
            if not result.ok:
                logger.warning(
                    f"Failed to sync vscode extensions on {cluster}: " + result.stderr
                )

    print(
        T.bold_green(
            f"Done synchronizing VsCode extensions between the local machine and the "
            f"{cluster} cluster."
        )
    )


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
            hide="stdout",
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
        f"Found {len(code_server_executable_versions)} code-server executables."
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
