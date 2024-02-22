from __future__ import annotations

import shutil
import subprocess
from logging import getLogger as get_logger
from pathlib import Path
from typing import Literal

from paramiko import SSHConfig

from milatools.cli import console
from milatools.cli.utils import DRAC_CLUSTERS

logger = get_logger(__name__)


class MfaRemote:
    def __init__(
        self,
        hostname: str,
        control_path: Path | None = None,
        control_persist: int | Literal["yes", "no"] = "yes",
    ):
        self.hostname = hostname
        self.control_persist = control_persist

        if (
            control_path is None
            or (control_path := _controlpath_from_sshconfig(self.hostname)) is None
        ):
            control_path = Path.home() / ".cache" / "ssh" / f"{self.hostname}.control"
            logger.warning(
                f"ControlPath wasn't set in the SSH config for {self.hostname}. "
                f"Will try to set the value to {control_path}."
            )

        self.control_path = control_path

        if not self.control_path.exists():
            console.log(
                f"Creating a reusable connection to the {self.hostname} cluster."
            )
            setup_connection_with_controlpath(self.hostname, self.control_path)

        assert self.control_path.exists()
        logger.info(
            f"Reusable SSH socket is setup for {self.hostname}: {self.control_path}"
        )

    def run(
        self, command: str, display: bool = True, warn: bool = False, hide: bool = False
    ):
        ssh_command = (
            f"ssh -o ControlMaster=auto -o ControlPersist={self.control_persist} "
            f'-o ControlPath={self.control_path} {self.hostname} "{command}"'
        )
        assert self.control_path.exists()

        if display:
            console.log(f"({self.hostname}) $ {command}", style="green")
        logger.debug(f"(local) $ {ssh_command}")
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
        assert self.control_path.exists()
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
        if not hide:
            console.log(result, style="dim")
        return result.strip()


def _controlpath_from_sshconfig(cluster: str) -> Path | None:
    """Returns true if the ControlMaster and ControlPath options are set in the config
    for that host."""
    ssh_config = SSHConfig.from_path(str(Path.home() / ".ssh" / "config"))
    values = ssh_config.lookup(cluster)
    if not (control_path := values.get("controlpath")):
        logger.debug("ControlPath isn't set.")
        return None
    return Path(control_path).expanduser()


def setup_connection_with_controlpath(
    cluster: str, control_path: Path, display: bool = True
) -> None:
    # note: this is the pattern in ~/.ssh/config: ~/.cache/ssh/%r@%h:%p
    if not control_path.exists():
        control_path.parent.mkdir(parents=True, exist_ok=True)
        logger.info(f"Connecting to {cluster}.")

    first_command = (
        f"ssh -o ControlMaster=auto -o ControlPath={control_path} "
        f"-o ControlPersist=yes {cluster} 'echo OK'"
    )
    if cluster in DRAC_CLUSTERS:
        console.log(
            f"Cluster {cluster} may be using two-factor authentication. "
            f"If you enabled 2FA, please take out your phone now. ",
            style="yellow",
        )
        if shutil.which("sshpass"):
            console.log(
                "If 2FA is enabled, you should now receive a push notification in the "
                "Duo app. Confirm it to continue."
            )
            # Enter 1 to go straight to the prompt on the phone.
            first_command = f"sshpass -P 'Duo two-factor login' -p 1 {first_command}"
        else:
            logger.debug(
                f"`sshpass` is not installed. If 2FA is setup on {cluster}, you might "
                "be asked to press 1 or enter a 2fa passcode."
            )
    else:
        # NOTE: Assuming that passwordless ssh is setup to the cluster, we could also
        # use the sshpass command above even if 2fa isn't setup. This doesn't seem to
        # change anything about the outputs.
        pass

    logger.info(f"Making the first connection to {cluster}...")
    if display:
        console.log(f"({cluster}) $ echo OK", style="green")
    logger.debug(f"(local) $ {first_command}")
    try:
        first_connection_output = subprocess.check_output(
            first_command,
            shell=True,
            text=True,
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
    if not control_path.exists():
        raise RuntimeError(
            f"{control_path} was not created after the first connection."
        )


def create_reusable_ssh_connection_sockets(clusters: list[str]) -> list[Path | None]:
    """Try to create the connection sockets to be reused by the subprocesses when
    connecting."""
    control_paths: list[Path | None] = []
    for cluster in clusters:
        console.log(f"Creating the controlpath for cluster {cluster}")
        try:
            control_path = setup_connection_with_controlpath(cluster)
        except RuntimeError as err:
            logger.warning(
                f"Unable to setup a reusable SSH connection to cluster {cluster}."
            )
            logger.error(err)
            control_path = None

        control_paths.append(control_path)
    return control_paths
