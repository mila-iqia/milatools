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
from milatools.cli.utils import Cluster, T

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
    vscode_extensions_folder: Path,
    remote: Remote | None = None,
    extensions_archive_path: str | Path = Path("~/.milatools/vscode-extensions.tar.gz"),
):
    if remote is None:
        remote = Remote(cluster)

    extensions_archive_path = Path(extensions_archive_path).expanduser()
    extensions_archive_path.parent.mkdir(exist_ok=True, parents=False)

    print(f"Creating archive of VSCode extensions at {extensions_archive_path} ")

    local_extensions = list(
        str(p.relative_to(vscode_extensions_folder))
        for p in vscode_extensions_folder.iterdir()
    )
    remote.run(
        "ls ~/.vscode-server/extensions", display=True, hide="stdout", warn=True
    ).stdout.split()

    # TODO: This is a bit brittle: If we interrupt the transfer then each extension will
    # be seen as "transferred" but it might be only partially extracted... Therefore for
    # now we copy everything over.
    missing_extensions = set(local_extensions)  # - set(extensions_on_remote)

    with tarfile.open(extensions_archive_path, mode="w:gz") as extensions_tarfile:
        # Only ship the extensions that aren't already on the remote?
        with tqdm.tqdm(
            sorted(missing_extensions),
            desc=(
                f"Packing the {len(missing_extensions)} missing VsCode extensions into "
                f"an archive..."
            ),
            unit="extension",
        ) as pbar:
            for extension in pbar:
                extensions_tarfile.add(
                    vscode_extensions_folder / extension,
                    # Name in the archive will be {extension}
                    arcname=extension,
                    recursive=True,
                )
                pbar.set_postfix({"extension": extension})

    print(f"Transferring archive of missing VsCode extensions over to {cluster}.")
    cmd = f"scp {extensions_archive_path} {cluster}:.vscode-server/"
    print(T.bold_green("(local) $ ", cmd))
    subprocess.run(cmd, shell=True, check=True)

    print("Extracting archive...")
    remote.run(
        f"tar --extract --gzip --file ~/.vscode-server/{extensions_archive_path.name} "
        f"--directory ~/.vscode-server/extensions"
    )
    # cmd = f"scp" f" {remote.hostname}:.vscode-server/"
    # print(T.bold_green("(local) $ ", cmd))
    # env = os.environ.copy()
    # copying_io = io.StringIO()
    # copying_process = subprocess.Popen(
    #     shlex.split(cmd),
    #     stdout=copying_io,
    #     env=env,
    #     shell=True,
    # )
