from __future__ import annotations

import functools
import os
import shlex
import shutil
import subprocess
import sys
from logging import getLogger as get_logger
from pathlib import Path
from typing import Literal

from milatools.cli import console
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
    TaskFn,
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

    # if source in DRAC_CLUSTERS or any(dest in DRAC_CLUSTERS for dest in destinations):
    #     if not shutil.which("sshpass"):
    #         raise RuntimeError(
    #             "The `sshpass` command is required to sync extensions to DRAC "
    #             "clusters in order to send the two-factor authentication "
    #             "notification."
    #         )

    return sync_vscode_extensions_in_parallel(source_obj, destinations)


def setup_first_connection_controlpath(cluster: str) -> Path:
    # note: this is the pattern in ~/.ssh/config: ~/.cache/ssh/%r@%h:%p
    for file in (Path.home() / ".cache" / "ssh").glob(f"*@{cluster}*"):
        control_file = file
        break
    else:
        control_file = Path.home() / ".cache" / "ssh" / f"{cluster}.control"
        control_file.parent.mkdir(parents=True, exist_ok=True)

    if control_file.exists():
        logger.info(
            f"Reusable SSH socket is already setup for {cluster}: {control_file}"
        )
        return control_file

    logger.info(f"Connecting to {cluster}.")
    logger.debug(f"({control_file} does not exist)")
    console.log(
        f"Cluster {cluster} may be using two-factor authentication. "
        f"If you enabled 2FA, please take out your phone to confirm when prompted...",
        style="yellow",
    )
    first_command = (
        f"sshpass -P 'Enter a passcode' -p 1 ssh -o ControlMaster=auto "
        f"-o ControlPath={control_file} -o ControlPersist=yes {cluster} echo OK"
    )
    console.log(f"Making the first connection to {cluster}...")
    console.log(f"[green](local) $ {first_command}")
    try:
        first_connection_output = subprocess.check_output(
            first_command,
            shell=True,
            text=True,
            timeout=30,
        )
    except subprocess.CalledProcessError as err:
        raise RuntimeError(
            f"Unable to setup a reusable SSH connection to cluster {cluster}!"
        ) from err
    if "OK" not in first_connection_output:
        raise RuntimeError(
            f"Did not receive the expected output ('OK') from {cluster}: "
            f"{first_connection_output}"
        )
    if not control_file.exists():
        raise RuntimeError(
            f"{control_file} was not created after the first connection."
        )
    return control_file


def create_reusable_ssh_connection_sockets(clusters: list[str]) -> list[Path | None]:
    """Try to create the connection sockets to be reused by the subprocesses when
    connecting."""
    control_paths: list[Path | None] = []
    for cluster in clusters:
        console.log(f"Creating the controlpath for cluster {cluster}")
        try:
            control_path = setup_first_connection_controlpath(cluster)
        except RuntimeError as err:
            logger.warning(
                f"Unable to setup a reusable SSH connection to cluster {cluster}."
            )
            logger.error(err)
            control_path = None

        control_paths.append(control_path)
    return control_paths


class DumbRemote:
    def __init__(
        self,
        hostname: str,
        control_path: Path,
        control_persist: int | Literal["yes", "no"] = "yes",
    ):
        self.hostname = hostname
        self.control_path = control_path
        self.control_persist = control_persist

    def run(
        self, command: str, display: bool = True, warn: bool = False, hide: bool = False
    ):
        ssh_command = (
            f"ssh -o ControlMaster=yes -o ControlPersist={self.control_persist} "
            f'-o ControlPath={self.control_path} {self.hostname} "{command}"'
        )
        # if display:
        logger.debug(f"[green](local) $ {ssh_command}")
        result = subprocess.run(
            ssh_command,
            capture_output=True,
            check=not warn,
            shell=True,
            text=True,
        )
        logger.debug(f"{result.stdout=}")
        logger.debug(f"{result.stdout=}")
        return result

    def get_output(
        self,
        command: str,
        display=False,
        warn=False,
        hide=True,
    ):
        ssh_command = (
            f"ssh -o ControlMaster=auto -o ControlPersist={self.control_persist} "
            f'-o ControlPath={self.control_path} {self.hostname} "{command}"'
        )
        if display:
            console.log(f"[green](local) $ {ssh_command}")
        if warn or not hide:
            result = subprocess.run(
                ssh_command,
                capture_output=hide,
                check=not warn,
                shell=True,
                text=True,
            ).stdout
        else:
            result = subprocess.check_output(
                ssh_command,
                shell=True,
                text=True,
            )
        logger.debug(f"{result=}")
        return result.strip()


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

    task_fns: list[TaskFn[None]] = []
    task_descriptions: list[str] = []

    for dest_remote in dest_clusters:
        if dest_remote == "localhost":
            control_path = None
        else:
            logger.debug(
                f"Creating a reusable connection to the {dest_remote} cluster."
            )
            try:
                control_path = setup_first_connection_controlpath(dest_remote)
            except RuntimeError as err:
                logger.warning(
                    f"Unable to setup a reusable SSH connection to cluster {dest_remote}."
                )
                logger.error(err)
                control_path = None
                raise  # FIXME: remove this

        task_fns.append(
            functools.partial(
                _install_vscode_extensions_task_function,
                dest_hostname=dest_remote,
                source_extensions=source_extensions,
                control_path=control_path,
                source_name=source_hostname,
            )
        )
        task_descriptions.append(f"{source_hostname} -> {dest_remote}")

    for result in parallel_progress_bar(
        task_fns=task_fns,
        task_descriptions=task_descriptions,
        overall_progress_task_description="[green]Syncing vscode extensions:",
    ):
        print(result)


def _install_vscode_extensions_task_function(
    progress_dict: DictProxy[TaskID, ProgressDict],
    task_id: TaskID,
    dest_hostname: str | Literal["localhost"],
    source_extensions: dict[str, str],
    control_path: Path | None,
    source_name: str,
):
    progress_dict[task_id] = {
        "progress": 0,
        "total": len(source_extensions),
        "info": "Connecting...",
    }
    if dest_hostname == "localhost":
        remote = Local()
    elif control_path:
        remote = DumbRemote(dest_hostname, control_path=control_path)
    else:
        remote = Remote(dest_hostname)

    if isinstance(remote, (Remote, DumbRemote)):
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
            progress_dict[task_id] = {
                "progress": 0,
                "total": 0,
                "info": "code-server executable not found!",
            }
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

    if to_install:
        logger.debug(f"Will install {len(to_install)} extensions on {dest_hostname}.")
    else:
        logger.info(f"No extensions to sync to {dest_hostname}.")

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
        elif isinstance(remote, DumbRemote):
            result = remote.run(
                shlex.join(command),
                display=False,
                warn=True,
                hide=True,
            )
            success = result.returncode == 0
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
            "progress": index + 1,
            "total": len(to_install),
            "info": f"Installing {extension_name}",
        }

    progress_dict[task_id] = {
        "progress": len(to_install),
        "total": len(to_install),
        "info": "Done.",
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
    remote: Remote | DumbRemote,
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
    remote: Remote | DumbRemote, remote_vscode_server_dir: str
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
