from __future__ import annotations

import getpass
import shlex
import shutil
import subprocess
import sys
from logging import getLogger as get_logger
from pathlib import Path
from typing import Any, Literal

from paramiko import SSHConfig

from milatools.cli import console
from milatools.cli.remote import Hide
from milatools.cli.utils import DRAC_CLUSTERS, MilatoolsUserError

logger = get_logger(__name__)

SSH_CONFIG_FILE = Path.home() / ".ssh" / "config"
SSH_CACHE_DIR = Path.home() / ".cache" / "ssh"


class UnsupportedPlatformError(MilatoolsUserError):
    ...


def raise_error_if_running_on_windows():
    if sys.platform == "win32":
        raise UnsupportedPlatformError(
            "This feature isn't supported on Windows, as it requires an SSH client "
            "with SSH multiplexing support (ControlMaster, ControlPath and "
            "ControlPersist).\n"
            "Please consider switching to the Windows Subsystem for Linux (WSL).\n"
            "See https://learn.microsoft.com/en-us/windows/wsl/install for a guide on "
            "setting up WSL."
        )


def ssh_command(
    hostname: str,
    control_path: Path | Literal["none"],
    command: str,
    control_master: Literal["yes", "no", "auto", "ask", "autoask"] = "auto",
    control_persist: int | str | Literal["yes", "no"] = "yes",
):
    """Returns a tuple of strings to be used as the command to be run in a subprocess.

    Parameters
    ----------
    hostname: The hostname to connect to.
    control_path : See https://man.openbsd.org/ssh_config#ControlPath
    command: The command to run on the remote host (kept as a string).
    control_master: See https://man.openbsd.org/ssh_config#ControlMaster
    control_persist: See https://man.openbsd.org/ssh_config#ControlPersist

    Returns
    -------
    The tuple of strings to pass to `subprocess.run` or similar.
    """
    return (
        "ssh",
        f"-oControlMaster={control_master}",
        f"-oControlPersist={control_persist}",
        f"-oControlPath={control_path}",
        hostname,
        command,
    )


def control_socket_is_running(host: str, control_path: Path) -> bool:
    """Check whether the control socket at the given path is running."""
    if not control_path.exists():
        return False
    result = subprocess.run(
        ("ssh", "-O", "check", f"-oControlPath={control_path}", host),
        shell=False,
        text=True,
        capture_output=True,
    )
    if (
        result.returncode != 0
        or not result.stderr
        or not result.stderr.startswith("Master running")
    ):
        logger.debug(f"{control_path=} doesn't exist or isn't running: {result=}.")
        return False
    return True


class RemoteV2:
    """Simpler Remote where commands are run in subprocesses sharing an SSH connection.

    This doesn't work on Windows, as it assumes that the SSH client has SSH multiplexing
    support (ControlMaster, ControlPath and ControlPersist).
    """

    def __init__(
        self,
        hostname: str,
        control_path: Path | None = None,
    ):
        """Create an SSH connection using this control_path, creating it if necessary.

        Parameters
        ----------
        hostname: The hostname to connect to.
        control_path: The path where the control socket will be created if it doesn't \
            already exist. You can use `get_controlpath_for` to get this for a given
            hostname.
        """
        self.hostname = hostname
        self.control_path = control_path or get_controlpath_for(hostname)

        if not control_socket_is_running(self.hostname, self.control_path):
            logger.info(
                f"Creating a reusable connection to the {self.hostname} cluster."
            )
            setup_connection_with_controlpath(
                self.hostname,
                self.control_path,
                timeout=None,
                display=False,
            )
        else:
            logger.info(f"Reusing an existing SSH socket at {self.control_path}.")

        assert control_socket_is_running(self.hostname, self.control_path)

    def run(
        self, command: str, display: bool = True, warn: bool = False, hide: Hide = False
    ):
        assert self.control_path.exists()
        run_command = ssh_command(
            hostname=self.hostname,
            control_path=self.control_path,
            control_master="auto",
            control_persist="yes",
            command=command,
        )
        logger.debug(f"(local) $ {shlex.join(run_command)}")
        if display:
            console.log(f"({self.hostname}) $ {command}", style="green")
        result = subprocess.run(
            run_command,
            capture_output=True,
            check=not warn,
            text=True,
            bufsize=1,  # 1 means line buffered
        )
        if result.stdout:
            if hide not in [True, "out", "stdout"]:
                print(result.stdout)
            logger.debug(f"{result.stdout}")
        if result.stderr:
            if hide not in [True, "err", "stderr"]:
                print(result.stderr)
            logger.debug(f"{result.stderr}")
        return result

    def __eq__(self, other: Any) -> bool:
        return (
            isinstance(other, type(self))
            and other.hostname == self.hostname
            and other.control_path == self.control_path
        )

    def __repr__(self) -> str:
        return f"{type(self).__name__}(hostname={self.hostname!r}, control_path={str(self.control_path)})"

    def get_output(
        self,
        command: str,
        display=False,
        warn=False,
        hide=True,
    ):
        return self.run(command, display=display, warn=warn, hide=hide).stdout.strip()


def is_already_logged_in(cluster: str, also_run_command_to_check: bool = False) -> bool:
    """Checks whether we are already logged in to the given cluster.

    More specifically, this checks whether a reusable SSH control master is setup at the
    controlpath for the given cluster.

    NOTE: This function is not supported on Windows.

    Parameters
    ----------
    cluster: Hostname of the cluster to connect to.
    also_run_command_to_check: Whether we should also run a command over SSH to make
        100% sure that we are logged in. In most cases this isn't necessary so we can
        skip it, since it can take a few seconds.
    """
    if not SSH_CONFIG_FILE.exists():
        return False
    control_path = get_controlpath_for(cluster, ssh_config_path=SSH_CONFIG_FILE)
    if not control_path.exists():
        logger.debug(f"ControlPath at {control_path} doesn't exist. Not logged in.")
        return False

    if not control_socket_is_running(cluster, control_path):
        return False
    if not also_run_command_to_check:
        return True
    return RemoteV2(cluster, control_path=control_path).get_output("echo OK") == "OK"


def get_controlpath_for(
    cluster: str,
    ssh_config_path: Path = SSH_CONFIG_FILE,
    ssh_cache_dir: Path | None = SSH_CACHE_DIR,
) -> Path:
    """Returns the control path to use for the given host using the ssh config.

    If the ControlPath option is set or applies to that host in the ssh config, returns
    the string with user, hostname, port already resolved (based on the values in the
    config).

    If the `ControlPath` option doesn't apply for that host in the SSH config
    and `ssh_cache_dir` is set, a path of the form
    '{ssh_cache_dir}/{user}@{qualified_hostname}:{port}' is returned, with values based
    on the values in the SSH config for that host if present.

    If `ssh_cache_dir` is not set, and the `ControlPath` option doesn't apply for that
    hostname, a `RuntimeError` is raised.
    """
    if not ssh_config_path.exists():
        raise MilatoolsUserError(f"SSH config file doesn't exist at {ssh_config_path}.")

    ssh_config = SSHConfig.from_path(str(ssh_config_path))
    values = ssh_config.lookup(cluster)
    if not (control_path := values.get("controlpath")):
        if ssh_cache_dir is None:
            raise RuntimeError(
                f"ControlPath isn't set in the ssh config for {cluster}, and "
                "ssh_cache_dir isn't set."
            )
        logger.debug(
            f"ControlPath isn't set for host {cluster}. Falling back to the ssh cache "
            f"directory at {ssh_cache_dir}."
        )
        hostname = values.get("hostname", cluster)
        username = values.get("user", getpass.getuser())
        port = values.get("port", 22)
        control_path = ssh_cache_dir / f"{username}@{hostname}:{port}"
    return Path(control_path).expanduser()


def setup_connection_with_controlpath(
    cluster: str,
    control_path: Path,
    display: bool = True,
    timeout: int | None = None,
) -> None:
    """Setup (or test) an SSH connection to this cluster using this control path.

    This goes through the 2FA process for clusters where 2FA is enabled.

    Parameters
    ----------
    cluster: name of the cluster to connect to.
    control_path: Path to the control socket file.
    display: Whether to display the command being run.
    timeout: Timeout in seconds for the subprocess. Set to `None` for no timeout.

    Raises
    ------
    subprocess.TimeoutExpired
        If `timeout` was passed and the subprocess times out.
    subprocess.CalledProcessError
        If the subprocess call raised an error.
    RuntimeError
        If the control path doesn't exist after the first connection, or if we didn't
        receive the output we expected from running the command.
    """
    raise_error_if_running_on_windows()

    if not control_path.exists():
        control_path.parent.mkdir(parents=True, exist_ok=True)

    command = "echo OK"
    first_command_args = ssh_command(
        hostname=cluster,
        control_path=control_path,
        control_master="auto",
        control_persist="yes",
        command=command,
    )
    if cluster in DRAC_CLUSTERS:
        console.log(
            f"The {cluster} cluster may be using two-factor authentication. ",
            "If you enabled 2FA, please take out your phone now.",
            sep="\n",
            style="yellow",
        )
        if shutil.which("sshpass"):
            # console.log(
            #     f"If 2FA is enabled on {cluster}, you should now receive a push "
            #     "notification in the Duo app. Confirm it to continue."
            #     style="yellow",
            # )
            # Enter 1 with `sshpass` to go straight to the prompt on the phone.
            first_command_args = (
                "sshpass",
                "-P",
                "Duo two-factor login",
                "-p",
                "1",
                *first_command_args,
            )
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
    logger.debug(f"(local) $ {first_command_args}")
    if display:
        console.log(f"({cluster}) $ {command}", style="green")
    try:
        first_connection_result = subprocess.run(
            first_command_args,
            shell=False,
            text=True,
            bufsize=1,  # line buffered
            timeout=timeout,
            capture_output=True,
            check=True,
        )
        first_connection_output = first_connection_result.stdout
    except subprocess.TimeoutExpired as err:
        console.log(
            f"Timeout while setting up a reusable SSH connection to cluster {cluster}!"
        )
        raise err
    except subprocess.CalledProcessError as err:
        console.log(
            f"Unable to setup a reusable SSH connection to cluster {cluster}!", err
        )
        raise err
    if "OK" not in first_connection_output:
        raise RuntimeError(
            f"Did not receive the expected output ('OK') from {cluster}: "
            f"{first_connection_output}"
        )
    if not control_path.exists():
        raise RuntimeError(
            f"Expected a socket file to be created at {control_path} after the first "
            f"connection!"
        )
    logger.info(
        f"Success: Shareable SSH Connection to {cluster} is setup at {control_path=}"
    )
