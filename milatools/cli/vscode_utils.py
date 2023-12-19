from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tarfile
from logging import getLogger as get_logger
from pathlib import Path

import tqdm

from milatools.cli.remote import Remote
from milatools.cli.utils import Cluster, T, internet_on_compute_nodes

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


def vscode_installed() -> bool:
    return bool(shutil.which(os.environ.get("MILATOOLS_CODE_COMMAND", "code")))


def copy_vscode_extensions_to_remote(
    cluster: Cluster,
    local_vscode_extensions_folder: Path,
    remote: Remote | None = None,
    local_extensions_archive_path: str | Path = "~/.milatools/vscode-extensions.tar.gz",
    remote_vscode_extensions_folder: str = "~/.vscode-server/extensions",
):
    assert not internet_on_compute_nodes(cluster)

    if remote is None:
        remote = Remote(cluster)

    local_extensions_archive_path = Path(local_extensions_archive_path).expanduser()
    local_extensions_archive_path.parent.mkdir(exist_ok=True, parents=False)

    local_extensions_names = list(
        str(p.relative_to(local_vscode_extensions_folder))
        for p in local_vscode_extensions_folder.iterdir()
    )
    remote.run(f"mkdir -p {remote_vscode_extensions_folder}", display=False)
    remote_extension_files = remote.run(
        f"ls {remote_vscode_extensions_folder}", display=False, hide="stdout", warn=True
    ).stdout.split()

    # A file on the remote that contains the names of all the previously successfully
    # extracted VsCode extensions.
    remote_extracted_vscode_extensions_file = (
        ".milatools/extracted_vscode_extensions.txt"
    )
    remote.run("mkdir -p ~/.milatools", display=False)
    remote.run(f"touch {remote_extracted_vscode_extensions_file}", display=False)
    already_extracted_extensions = _read_text_file_lines(
        remote, remote_extracted_vscode_extensions_file
    )

    # Same idea: a file that contains the list of extensions in the archive that was
    # already scp'ed to the remote. If we already scp'ed the archive with all the
    # required extensions, then we don't need to scp it again.
    remote_transferred_extensions_file = ".milatools/transferred_vscode_extensions.txt"
    remote.run(f"touch {remote_transferred_extensions_file}", display=False)
    already_transfered_extensions = _read_text_file_lines(
        remote, remote_transferred_extensions_file
    )
    # TODO: There's one minor unhandled edge case here: If we sent an archive in the
    # past, but uninstall some extensions later, then all the required extensions are in
    # the archive, so this will skip the scp, but the remote will still have the
    # uninstalled extensions.

    # If an extension is listed in this text file, then it should also be present in the
    # folder, but filter things just to be sure. For example, when an extension is
    # uninstalled, its name might still be in the .txt file, but the folder might have
    # been removed on the remote. Therefore, we also check that the folder still exists.
    extensions_on_remote = [
        extension
        for extension in already_extracted_extensions
        if extension in remote_extension_files
    ]
    missing_extensions = set(local_extensions_names) - set(extensions_on_remote)
    logger.debug(f"Missing extensions: {missing_extensions}")

    if not missing_extensions:
        print(
            T.bold_green(
                f"All local VsCode extensions are already synced to the {cluster} "
                f"cluster."
            )
        )
        return

    # NOTE: Here we just check if we already completed the transfer. We don't try to
    # only send what's missing, that would make things too complicated.
    extensions_that_need_to_be_transfered = set(local_extensions_names) - set(
        already_transfered_extensions
    )
    if extensions_that_need_to_be_transfered:
        print(
            T.bold_cyan(
                f"Syncing {len(missing_extensions)} local VsCode extensions with the "
                f"{cluster} cluster..."
            )
        )

        with tarfile.open(
            local_extensions_archive_path, mode="w:gz"
        ) as extensions_tarfile:
            # Only ship the extensions that aren't already on the remote?
            with tqdm.tqdm(
                sorted(missing_extensions),
                desc="Packing missing VsCode extensions into an archive...",
                unit="extension",
            ) as pbar:
                for extension in pbar:
                    extensions_tarfile.add(
                        local_vscode_extensions_folder / extension,
                        # Name in the archive will be {extension} so it can be extracted
                        # directly in the extensions folder.
                        arcname=extension,
                        recursive=True,
                    )
                    pbar.set_postfix({"extension": extension})

        print(f"Sending archive of missing VsCode extensions over to {cluster}...")
        scp_command = (
            f"scp {local_extensions_archive_path} "
            f"{cluster}:.milatools/{local_extensions_archive_path.name}"
        )
        print(T.bold_green("(local) $ ", scp_command))
        subprocess.run(scp_command, shell=True, check=True)

        print(
            f"Saving list of synced extensions to {remote_transferred_extensions_file} "
            f"on the {cluster} cluster."
        )
        remote.puttext(
            "\n".join(local_extensions_names) + "\n",
            remote_transferred_extensions_file,
        )

    print(
        f"Extracting archive with the missing {len(missing_extensions)} VsCode "
        f"extensions."
    )
    remote.run(
        f"tar --extract --gzip --totals "
        f"--file ~/.milatools/{local_extensions_archive_path.name} "
        f"--directory {remote_vscode_extensions_folder}"
    )

    print(
        f"Saving list of extracted extensions to "
        f"{remote_extracted_vscode_extensions_file} on {cluster}."
    )
    remote.puttext(
        "\n".join(local_extensions_names) + "\n",
        remote_extracted_vscode_extensions_file,
    )
    print(
        T.bold_green(
            f"Done synchronizing VsCode extensions between the local machine and the "
            f"{cluster} cluster."
        )
    )


def _read_text_file_lines(remote: Remote, file: str) -> list[str]:
    return [
        line
        for line in remote.get_output(
            f"cat {file}",
            display=False,
            hide="stdout",
        ).splitlines()
        if line.strip()
    ]
