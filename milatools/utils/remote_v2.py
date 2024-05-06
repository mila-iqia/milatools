from __future__ import annotations

import contextlib
import dataclasses
import getpass
import shutil
import subprocess
import sys
from logging import getLogger as get_logger
from pathlib import Path
from typing import Literal

from paramiko import SSHConfig

from milatools.cli import console
from milatools.cli.utils import (
    DRAC_CLUSTERS,
    SSH_CACHE_DIR,
    SSH_CONFIG_FILE,
    MilatoolsUserError,
)
from milatools.utils.local_v2 import LocalV2, run, run_async
from milatools.utils.remote_v1 import Hide
from milatools.utils.runner import Runner

logger = get_logger(__name__)


class UnsupportedPlatformError(MilatoolsUserError):
    ...


@dataclasses.dataclass(init=False)
class RemoteV2(Runner):
    """Simpler Remote where commands are run in subprocesses sharing an SSH connection.

    This doesn't work on Windows, as it assumes that the SSH client has SSH multiplexing
    support (ControlMaster, ControlPath and ControlPersist).
    """

    hostname: str
    control_path: Path
    ssh_config_path: Path

    def __init__(
        self,
        hostname: str,
        *,
        control_path: Path | None = None,
        ssh_config_path: Path = SSH_CONFIG_FILE,
        _start_control_socket: bool = True,
    ):
        """Create an SSH connection using this control_path, creating it if necessary.

        Parameters
        ----------
        hostname: The hostname to connect to.
        control_path: The path where the control socket will be created if it doesn't \
            already exist. You can use `get_controlpath_for` to get this for a given \
            hostname.
        ssh_config_path: Path to the ssh config file.
        """
        self.hostname = hostname
        self.ssh_config_path = ssh_config_path
        self.control_path = control_path or get_controlpath_for(
            hostname, ssh_config_path=self.ssh_config_path
        )
        self.control_path = self.control_path.expanduser()
        self.local_runner = LocalV2()
        self._started = False
        if _start_control_socket:
            # Run an ssh command to start the control socket (synchronously), if needed.
            self._start()

    @staticmethod
    async def connect(
        hostname: str,
        *,
        control_path: Path | None = None,
        ssh_config_path: Path = SSH_CONFIG_FILE,
    ) -> RemoteV2:
        """Async constructor.

        Having an async constructor makes it possible to connect to multiple hosts
        simultaneously with things like `asyncio.gather` (assuming that there is no
        password prompt, in which case it is done sequentially).
        """
        logger.debug(f"Connecting to {hostname}...")
        remote = RemoteV2(
            hostname=hostname,
            control_path=control_path,
            ssh_config_path=ssh_config_path,
            _start_control_socket=False,
        )
        await remote._start_async()
        return remote

    def run(
        self,
        command: str,
        *,
        input: str | None = None,
        display: bool = True,
        warn: bool = False,
        hide: Hide = False,
    ):
        assert self._started
        run_command = ssh_command(
            hostname=self.hostname,
            control_path=self.control_path,
            command=command,
            ssh_config_path=self.ssh_config_path,
        )
        if display:
            # NOTE: Only display the input if it is passed.
            if not input:
                console.log(f"({self.hostname}) $ {command}", style="green")
            else:
                console.log(f"({self.hostname}) $ {command=}\n{input}", style="green")
        # Run the ssh command with the subprocess.run function.
        return self.local_runner.run(
            command=run_command, input=input, display=False, warn=warn, hide=hide
        )

    async def run_async(
        self,
        command: str,
        *,
        input: str | None = None,
        display: bool = True,
        warn: bool = False,
        hide: Hide = False,
    ) -> subprocess.CompletedProcess[str]:
        assert self._started
        run_command = ssh_command(
            hostname=self.hostname,
            control_path=self.control_path,
            command=command,
            ssh_config_path=self.ssh_config_path,
        )
        if display:
            if not input:
                console.log(f"({self.hostname}) $ {command}", style="green")
            else:
                console.log(f"({self.hostname}) $ {command=}\n{input}", style="green")
        return await self.local_runner.run_async(
            command=run_command, input=input, display=False, warn=warn, hide=hide
        )

    def _start(self) -> None:
        """Called by `__init__` to start the control socket if needed."""
        if self._started:
            return
        if not control_socket_is_running(self.hostname, self.control_path):
            logger.info(
                f"Creating a reusable connection to the {self.hostname} cluster."
            )
            setup_connection_with_controlpath(
                self.hostname,
                control_path=self.control_path,
                ssh_config_path=self.ssh_config_path,
            )
        else:
            logger.debug(f"Reusing an existing SSH socket at {self.control_path}.")
        assert control_socket_is_running(self.hostname, self.control_path)
        self._started = True

    async def _start_async(self) -> None:
        """Called by `connect` to start the control socket asynchronously if needed."""
        if self._started:
            return
        if not await control_socket_is_running_async(self.hostname, self.control_path):
            logger.info(
                f"Creating a reusable connection to the {self.hostname} cluster."
            )
            await setup_connection_with_controlpath_async(
                self.hostname,
                control_path=self.control_path,
                ssh_config_path=self.ssh_config_path,
            )
        else:
            logger.debug(f"Reusing an existing SSH socket at {self.control_path}.")

        assert await control_socket_is_running_async(self.hostname, self.control_path)
        self._started = True


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


# note: Could potentially cache the results of this function if we wanted to, assuming
# that the ssh config file doesn't change.


def ssh_command(
    hostname: str,
    control_path: Path,
    command: str,
    control_master: Literal["yes", "no", "auto", "ask", "autoask"] = "auto",
    control_persist: int | str | Literal["yes", "no"] = "yes",
    ssh_config_path: Path = SSH_CONFIG_FILE,
):
    """Returns a tuple of strings to be used as the command to be run in a subprocess.

    When the path to the SSH config file is passed and exists, this will only add the
    options which aren't already set in the SSH config, so as to avoid redundant
    arguments to the `ssh` command.

    Parameters
    ----------
    hostname: The hostname to connect to.
    control_path : See https://man.openbsd.org/ssh_config#ControlPath
    command: The command to run on the remote host (kept as a string).
    control_master: See https://man.openbsd.org/ssh_config#ControlMaster
    control_persist: See https://man.openbsd.org/ssh_config#ControlPersist
    ssh_config_path: Path to the ssh config file.

    Returns
    -------
    The tuple of strings to pass to `subprocess.run` or similar.
    """
    control_path = control_path.expanduser()
    ssh_config_path = ssh_config_path.expanduser()
    if not ssh_config_path.exists():
        return (
            "ssh",
            f"-oControlMaster={control_master}",
            f"-oControlPersist={control_persist}",
            f"-oControlPath={control_path}",
            hostname,
            command,
        )

    ssh_command: list[str] = ["ssh"]
    ssh_config_entry = SSHConfig.from_path(str(ssh_config_path)).lookup(hostname)
    if ssh_config_entry.get("controlmaster") != control_master:
        ssh_command.append(f"-oControlMaster={control_master}")
    if ssh_config_entry.get("controlpersist") != control_persist:
        ssh_command.append(f"-oControlPersist={control_persist}")

    control_path_in_config = ssh_config_entry.get("controlpath")
    if (
        control_path_in_config is None
        or Path(control_path_in_config).expanduser() != control_path
    ):
        # Only add the ControlPath arg if it is not in the config, or if it differs from
        # the value in the config.
        ssh_command.append(f"-oControlPath={control_path}")
    ssh_command.append(hostname)
    # NOTE: Not quoting the command here, `subprocess.run` does it (since shell=False).
    ssh_command.append(command)
    return tuple(ssh_command)


def control_socket_is_running(host: str, control_path: Path) -> bool:
    """Check whether the control socket at the given path is running."""
    control_path = control_path.expanduser()
    if not control_path.exists():
        return False

    result = run(
        (
            "ssh",
            "-O",
            "check",
            f"-oControlPath={control_path}",
            host,
        ),
        warn=True,
        hide=True,
    )
    return _is_control_socket_running_given_result(result, control_path)


async def control_socket_is_running_async(host: str, control_path: Path) -> bool:
    """Asynchronously checks whether the control socket at the given path is running."""
    control_path = control_path.expanduser()
    if not control_path.exists():
        return False

    result = await run_async(
        (
            "ssh",
            "-O",
            "check",
            f"-oControlPath={control_path}",
            host,
        ),
        warn=True,
        hide=True,
    )
    return _is_control_socket_running_given_result(result, control_path)


def _is_control_socket_running_given_result(
    result: subprocess.CompletedProcess, control_path: Path
) -> bool:
    if (
        result.returncode != 0
        or not result.stderr
        or not result.stderr.startswith("Master running")
    ):
        logger.debug(f"{control_path=} doesn't exist or isn't running: {result=}.")
        return False
    return True


def option_dict_to_flags(options: dict[str, str]) -> list[str]:
    return [
        (
            f"--{key.removeprefix('--')}={value}"
            if value is not None
            else f"--{key.removeprefix('--')}"
        )
        for key, value in options.items()
    ]


def is_already_logged_in(cluster: str, ssh_config_path: Path = SSH_CONFIG_FILE) -> bool:
    """Checks whether we are already logged in to the given cluster.

    More specifically, this checks whether a reusable SSH control master is setup at the
    controlpath for the given cluster.

    NOTE: This function is not supported on Windows.
    """
    if not ssh_config_path.exists():
        return False
    control_path = get_controlpath_for(cluster, ssh_config_path=ssh_config_path)
    if not control_path.exists():
        logger.debug(f"ControlPath at {control_path} doesn't exist. Not logged in.")
        return False

    if not control_socket_is_running(cluster, control_path):
        return False
    return True


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
    ssh_config_values: dict[str, str] = {}

    if ssh_config_path.exists():
        # note: This also does the substitutions in the vars, e.g. %p -> port, etc.
        ssh_config_values = SSHConfig.from_path(str(ssh_config_path)).lookup(cluster)

    if control_path := ssh_config_values.get("controlpath"):
        # Controlpath is set in the SSH config.
        return Path(control_path).expanduser()

    if ssh_cache_dir is None:
        raise RuntimeError(
            f"ControlPath isn't set in the ssh config for {cluster}, and "
            "ssh_cache_dir isn't set."
        )

    ssh_cache_dir = ssh_cache_dir.expanduser()
    logger.debug(
        f"ControlPath isn't set for host {cluster}. Falling back to the ssh cache "
        f"directory at {ssh_cache_dir}."
    )
    # Assume that the hostname is the same if not set.
    hostname = ssh_config_values.get("hostname", cluster)
    if "@" in hostname:
        logger.debug(f"Username is already in the hostname: {hostname}")
        return ssh_cache_dir / hostname
    username = ssh_config_values.get("user", getpass.getuser())
    port = int(ssh_config_values.get("port", 22))
    return ssh_cache_dir / f"{username}@{hostname}:{port}"


def setup_connection_with_controlpath(
    cluster: str,
    control_path: Path,
    ssh_config_path: Path = SSH_CONFIG_FILE,
) -> None:
    """Setup (or test) an SSH connection to this cluster using this control path.

    This goes through the 2FA process for clusters where 2FA is enabled.

    Parameters
    ----------
    cluster: name of the cluster to connect to.
    control_path: Path to the control socket file.
    ssh_config_path: Path to the ssh config file.

    Raises
    ------
    subprocess.CalledProcessError
        If the subprocess call raised an error.
    RuntimeError
        If the control path doesn't exist after the first connection, or if we didn't
        receive the output we expected from running the command.
    """
    # note: Trying to reduce the code duplication between sync/async versions of this
    # function as much as possible, but this isn't quite there yet :(.
    raise_error_if_running_on_windows()
    setup_ssh_control_socket_command = _get_setup_ssh_control_socket_command(
        cluster=cluster, control_path=control_path, ssh_config_path=ssh_config_path
    )
    with _catch_setup_ssh_control_socket_errors(cluster=cluster):
        first_connection_output = LocalV2().get_output(
            command=setup_ssh_control_socket_command,
            display=False,
            hide="out",
            warn=False,
        )
    _raise_if_controlsocket_not_setup(cluster, control_path, first_connection_output)
    _log_successful_setup(cluster, control_path)


async def setup_connection_with_controlpath_async(
    cluster: str,
    control_path: Path,
    ssh_config_path: Path = SSH_CONFIG_FILE,
) -> None:
    """Sets up the SSH control socket asynchronously.

    See the sync version of this function for more info. Only a single line differs
    between the two.
    """
    raise_error_if_running_on_windows()
    setup_ssh_control_socket_command = _get_setup_ssh_control_socket_command(
        cluster=cluster, control_path=control_path, ssh_config_path=ssh_config_path
    )
    with _catch_setup_ssh_control_socket_errors(cluster=cluster):
        first_connection_output = await LocalV2().get_output_async(  # only change
            command=setup_ssh_control_socket_command,
            display=False,
            hide="out",
            warn=False,
        )
    _raise_if_controlsocket_not_setup(cluster, control_path, first_connection_output)
    _log_successful_setup(cluster, control_path)


def _log_successful_setup(cluster: str, control_path: Path):
    logger.info(
        f"Success: Shareable SSH Connection to {cluster} is setup at {control_path=}"
    )


def _get_setup_ssh_control_socket_command(
    cluster: str, control_path: Path, ssh_config_path: Path = SSH_CONFIG_FILE
):
    control_path = control_path.expanduser()
    if not control_path.exists():
        control_path.parent.mkdir(parents=True, exist_ok=True)

    command = "echo OK"
    ssh_command_args = ssh_command(
        hostname=cluster,
        control_path=control_path,
        control_master="auto",
        control_persist="yes",
        command=command,
        ssh_config_path=ssh_config_path,
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
            ssh_command_args = (
                "sshpass",
                "-P",
                "Duo two-factor login",
                "-p",
                "1",
                *ssh_command_args,
            )
        else:
            logger.warning(
                f"`sshpass` is not installed. If 2FA is setup on {cluster}, you might "
                "be asked to press 1 or enter a 2fa passcode."
            )
    else:
        # NOTE: Assuming that passwordless ssh is setup to the cluster, we could also
        # use the sshpass command above even if 2fa isn't setup. This doesn't seem to
        # change anything about the outputs.
        pass

    logger.info(f"Making the first connection to {cluster}...")
    return ssh_command_args


@contextlib.contextmanager
def _catch_setup_ssh_control_socket_errors(cluster: str):
    try:
        yield
    except subprocess.TimeoutExpired:
        console.log(
            f"Timeout while setting up a reusable SSH connection to cluster {cluster}!"
        )
        raise
    except subprocess.CalledProcessError:
        console.log(f"Unable to setup a reusable SSH connection to cluster {cluster}!")
        raise


def _raise_if_controlsocket_not_setup(
    cluster: str, control_path: Path, first_connection_output: str
):
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
